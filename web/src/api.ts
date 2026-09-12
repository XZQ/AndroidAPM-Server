import type {
  ApiErrorBody,
  DataQuality,
  EventMetadata,
  EventPage,
  Fingerprints,
  IssueDetail,
  QueryFilters,
  RawEvent,
  ReleaseDecisionInput,
  ReleaseDecisionList,
  ReleaseDecision,
  ReleaseHealth,
  WebSession,
} from "./types";

const CSRF_COOKIE = "androidapm_csrf";
const CSRF_HEADER = "X-Apm-Csrf-Token";

export class ApiFailure extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;
  readonly retryable: boolean;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message ?? `请求失败（HTTP ${status}）`);
    this.name = "ApiFailure";
    this.status = status;
    this.code = body.code ?? "unknown_error";
    this.requestId = body.requestId;
    this.retryable = body.retryable ?? false;
  }
}

export async function login(queryKey: string): Promise<WebSession> {
  return request<WebSession>("/v1/web/session", {
    method: "POST",
    body: JSON.stringify({ queryKey }),
  });
}

export async function restoreSession(): Promise<WebSession> {
  return request<WebSession>("/v1/web/session");
}

export async function logout(): Promise<void> {
  await request<void>("/v1/web/session", {
    method: "DELETE",
    headers: csrfHeaders(),
  });
}

export async function getReleaseHealth(filters: QueryFilters): Promise<ReleaseHealth> {
  return request<ReleaseHealth>(
    `/v1/query/release-health?${queryString({
      newRelease: filters.newRelease,
      baselineRelease: filters.baselineRelease,
      fromMs: filters.fromMs,
      toMs: filters.toMs,
    })}`,
  );
}

export async function getDataQuality(filters: QueryFilters): Promise<DataQuality> {
  return request<DataQuality>(
    `/v1/query/data-quality?${queryString({
      releaseVersion: filters.newRelease,
      fromMs: filters.fromMs,
      toMs: filters.toMs,
    })}`,
  );
}

export async function getFingerprints(filters: QueryFilters): Promise<Fingerprints> {
  return request<Fingerprints>(
    `/v1/query/fingerprints?${queryString({
      releaseVersion: filters.newRelease,
      fromMs: filters.fromMs,
      toMs: filters.toMs,
      limit: 20,
    })}`,
  );
}

export async function getIssueDetail(
  fingerprint: string,
  filters: QueryFilters,
): Promise<IssueDetail> {
  return request<IssueDetail>(
    `/v1/query/issues/${encodeURIComponent(fingerprint)}?${queryString({
      fromMs: filters.fromMs,
      toMs: filters.toMs,
    })}`,
  );
}

export async function getEvents(
  filters: QueryFilters,
  fingerprint?: string,
  cursor?: string,
  module?: string,
  name?: string,
): Promise<EventPage> {
  return request<EventPage>(
    `/v1/query/events?${queryString({
      releaseVersion: filters.newRelease,
      fromMs: filters.fromMs,
      toMs: filters.toMs,
      fingerprint,
      cursor,
      module,
      name,
      limit: 25,
    })}`,
  );
}

export async function getEventMetadata(eventId: string): Promise<EventMetadata> {
  return request<EventMetadata>(`/v1/query/events/${encodeURIComponent(eventId)}`);
}

export async function getRawEvent(
  eventId: string,
  purposeCode: string,
  reason: string,
): Promise<RawEvent> {
  return request<RawEvent>(`/v1/query/events/${encodeURIComponent(eventId)}/raw`, {
    method: "POST",
    headers: csrfHeaders(),
    body: JSON.stringify({ purposeCode, reason }),
  });
}

export async function createReleaseDecision(
  input: ReleaseDecisionInput,
): Promise<ReleaseDecision> {
  return request<ReleaseDecision>("/v1/query/release-decisions", {
    method: "POST",
    headers: csrfHeaders(),
    body: JSON.stringify(input),
  });
}

export async function getReleaseDecisions(releaseVersion: string): Promise<ReleaseDecisionList> {
  return request<ReleaseDecisionList>(
    `/v1/query/release-decisions?${queryString({ releaseVersion, limit: 20 })}`,
  );
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) {
    let body: ApiErrorBody = {};
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      body = { message: response.statusText };
    }
    throw new ApiFailure(response.status, body);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function csrfHeaders(): HeadersInit {
  const token = readCookie(CSRF_COOKIE);
  if (token === null) {
    throw new ApiFailure(403, {
      code: "csrf_token_missing",
      message: "当前会话缺少请求校验信息，请重新登录。",
    });
  }
  return { [CSRF_HEADER]: token };
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  for (const part of document.cookie.split(";")) {
    const candidate = part.trim();
    if (candidate.startsWith(prefix)) {
      return decodeURIComponent(candidate.slice(prefix.length));
    }
  }
  return null;
}

function queryString(values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  }
  return params.toString();
}
