import type { QueryState } from "./types";

export interface Observation {
  state: QueryState; sampleCount: number; eventCount: number; missingCount: number;
  invalidCount: number; unavailableCount: number; coverage: number | null;
  mean: number | null; minimum: number | null; maximum: number | null;
  p50: number | null; p75: number | null; p95: number | null;
}
export interface PerformanceMetric {
  id: string; module: string; name: string; field: string; label: string; unit: string;
  current: Observation; baseline: Observation; p95Delta: number | null;
}
export interface CategoryObservation {
  state: QueryState; eventCount: number; availableCount: number; missingCount: number;
  invalidCount: number; unknownCount: number; expiredCount: number; counts: Record<string, number>;
}
export interface WeightedFps {
  state: QueryState; value: number | null; sampleCount: number; eventCount: number;
  totalWindowMs: number | null; missingCount: number; invalidCount: number;
  unavailableCount: number; coverage: number | null;
}
export interface PerformanceResponse {
  requestId: string; newRelease: string; baselineRelease: string; fromMs: number; toMs: number;
  metrics: PerformanceMetric[]; trendMetric: string;
  trend: { fromMs: number; toMs: number; current: Observation; baseline: Observation }[];
  lateCount: number; nonOccurrenceCount: number; networkErrorRatio: number | null; jankRatio: number | null;
  eventStatistics: { module: string; name: string; label: string; currentCount: number; baselineCount: number; totalCount: number; nonOccurrenceCount: number }[];
  dimensions: { id: string; label: string; current: CategoryObservation; baseline: CategoryObservation }[];
  weightedFps: WeightedFps; baselineWeightedFps: WeightedFps;
}
export interface AlertInput {
  name: string; metric: string; comparison: "gt" | "lt"; threshold: number; minSamples: number;
  windowSeconds: number; releaseVersion: string | null; channel: string | null; enabled: boolean;
}
export interface AlertRule extends AlertInput {
  id: number; state: string; lastValue: number | null; lastSampleCount: number;
  activeInstanceId: number | null; evaluatedAt: string | null; silenceUntil: string | null;
}
export interface AlertsResponse {
  rules: AlertRule[]; metrics: { id: string; label: string; unit: string }[]; channels: string[];
  historyLimit: number;
  instances: { id: number; ruleId: number; triggeredAt: string; resolvedAt: string | null; acknowledgedAt: string | null; triggerValue: number; evidence: Record<string, unknown> }[];
  deliveries: { id: number; instanceId: number; transition: string; channel: string; status: string; attemptCount: number; lastError: string | null; deliveredAt: string | null }[];
}
export interface SettingsResponse {
  displayName: string; role: string; pipeline: Record<string, number>; symbolJobs: Record<string, number>;
  operations: Record<string, boolean | number>;
  artifacts: { id: number; type: string; versionCode: string; appBuild: string; variant: string; abi: string | null; buildId: string | null; sha256: string; status: string }[];
}
export type KeyKind = "query" | "ingest" | "ci";
export interface Credential {
  kind: KeyKind; id: string; description: string; role: string | null; active: boolean;
  expiresAt: string | null; revokedAt: string | null; createdAt: string; current: boolean;
}
export interface KeyInput {
  kind: KeyKind; role: "viewer" | "investigator" | "admin"; description: string; expiresDays: number;
}
export interface ConfigHistory {
  signingConfigured: boolean; publicKey: string | null; keyId: string;
  items: { revision: number; payload: Record<string, unknown>; issuedAt: string; expiresAt: string; rolloutBasisPoints: number; active: boolean }[];
}
export interface AuditEntry {
  id: number; action: string; actor: string; objectType: string; objectId: string | null;
  result: string; createdAt: string; requestId: string | null;
}
export interface TraceSummary {
  traceId: string; spanCount: number; errorSpanCount: number; durationMs: number | null;
  firstSeenMs: number; lastSeenMs: number; timingAvailableCount: number; orphanCount: number;
  invalidRelationCount: number; lateCount: number; coverageState: string; state: QueryState;
}
export interface TraceList {
  state: QueryState; sampleCount: number; invalidSpanCount: number; expiredSpanCount: number;
  traceCount: number; durationSampleCount: number; meanSpanDurationMs: number | null;
  p95SpanDurationMs: number | null; items: TraceSummary[]; truncated: boolean;
}
export interface SpanObservation {
  eventId: string; traceId: string; spanId: string; parentSpanId: string | null; name: string;
  status: string; durationMs: number | null; startMs: number | null; endMs: number | null;
  timingState: string; relationState: string; customAttributeCount: number; occurrenceTimestampMs: number;
  late: boolean; releaseVersion: string | null; releaseIdentityQuality: string;
}
export interface TraceDetail { summary: TraceSummary; spans: SpanObservation[] }
