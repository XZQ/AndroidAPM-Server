import { csrfHeaders, queryString, request } from "./api";
import type { QueryFilters } from "./types";
import type { AlertInput, AlertRule, AlertsResponse, AuditEntry, ConfigHistory, Credential, KeyInput, KeyKind, PerformanceResponse, SettingsResponse, TraceDetail, TraceList } from "./productTypes";

export function getPerformance(filters: QueryFilters, metric: string) {
  return request<PerformanceResponse>(`/v1/query/performance?${queryString({ ...filters, metric })}`);
}
export const getAlerts = () => request<AlertsResponse>("/v1/alerts");
export const getSettingsState = () => request<SettingsResponse>("/v1/manage/settings");
export const getKeys = () => request<{ items: Credential[]; limitPerKind: number }>("/v1/manage/keys");
export const getConfigs = () => request<ConfigHistory>("/v1/manage/configs");
export const getAudit = () => request<{ items: AuditEntry[] }>("/v1/manage/audit");

/** Cookie-authenticated writes all use the existing session-bound CSRF token. */
function write<T>(path: string, body: unknown, method = "POST") {
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
export const getTraces = (filters: QueryFilters) => request<TraceList>(`/v1/query/traces?${queryString({ fromMs: filters.fromMs, toMs: filters.toMs, releaseVersion: filters.newRelease, limit: 50 })}`);
export const getTrace = (id: string, filters: QueryFilters) => request<TraceDetail>(`/v1/query/traces/${encodeURIComponent(id)}?${queryString({ fromMs: filters.fromMs, toMs: filters.toMs, releaseVersion: filters.newRelease })}`);
