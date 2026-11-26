import { csrfHeaders, queryString, request } from "./api";
import type { QueryFilters } from "./types";
import type { AlertInput, AlertRule, AlertsResponse, AuditEntry, ConfigHistory, Credential, KeyInput, KeyKind, PerformanceResponse, SettingsResponse, TraceDetail, TraceList } from "./productTypes";

export function getPerformance(filters: QueryFilters, metric: string, signal?: AbortSignal) {
  return request<PerformanceResponse>(`/v1/query/performance?${queryString({ ...filters, metric })}`, { signal });
}
export const getAlerts = (signal?: AbortSignal) => request<AlertsResponse>("/v1/alerts", { signal });
export const getSettingsState = (signal?: AbortSignal) => request<SettingsResponse>("/v1/manage/settings", { signal });
export const getKeys = (signal?: AbortSignal) => request<{ items: Credential[]; limitPerKind: number }>("/v1/manage/keys", { signal });
export const getConfigs = (signal?: AbortSignal) => request<ConfigHistory>("/v1/manage/configs", { signal });
export const getAudit = (signal?: AbortSignal) => request<{ items: AuditEntry[] }>("/v1/manage/audit", { signal });

/** Cookie-authenticated writes all use the existing session-bound CSRF token. */
async function write<T>(path: string, body: unknown, method = "POST"): Promise<T> {
  return request<T>(path, { method, headers: csrfHeaders(), body: JSON.stringify(body) });
}
export const saveAlert = (input: AlertInput, id?: number) => write<AlertRule>(id === undefined ? "/v1/alerts/rules" : `/v1/alerts/rules/${id}`, input, id === undefined ? "POST" : "PUT");
export const evaluateAlerts = () => write("/v1/alerts/evaluate", {});
export const acknowledgeAlert = (id: number) => write(`/v1/alerts/instances/${id}/acknowledge`, {});
export const silenceAlert = (id: number, minutes: number) => write(`/v1/alerts/rules/${id}/silence`, { minutes });
export const updateDisplayName = (displayName: string) => write("/v1/manage/settings", { displayName }, "PUT");
export const createKey = (input: KeyInput) => write<{ id: string; secret: string }>("/v1/manage/keys", input);
export const revokeKey = (kind: KeyKind, id: string) => write(`/v1/manage/keys/${kind}/${encodeURIComponent(id)}/revoke`, {});
export const publishConfig = (payload: Record<string, unknown>, expiresHours: number, rolloutBasisPoints: number, sourceRevision?: number) => write<{ revision: number }>("/v1/manage/configs", { payload, expiresHours, rolloutBasisPoints, sourceRevision });
export const getTraces = (filters: QueryFilters, signal?: AbortSignal) => request<TraceList>(`/v1/query/traces?${queryString({ fromMs: filters.fromMs, toMs: filters.toMs, releaseVersion: filters.newRelease, limit: 50 })}`, { signal });
export const getTrace = (id: string, filters: QueryFilters, signal?: AbortSignal) => request<TraceDetail>(`/v1/query/traces/${encodeURIComponent(id)}?${queryString({ fromMs: filters.fromMs, toMs: filters.toMs, releaseVersion: filters.newRelease })}`, { signal });
