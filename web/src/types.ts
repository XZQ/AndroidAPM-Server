export type QueryState =
  | "PRESENT"
  | "ZERO"
  | "NO_DATA"
  | "UNAVAILABLE"
  | "UNKNOWN_COVERAGE"
  | "DEGRADED"
  | "LATE"
  | "ERROR";

export interface QueryScope {
  tenantId: string;
  appId: string;
  environment: string;
}

export interface QueryWindow {
  fromMs: number;
  toMs: number;
}

export interface WebSession {
  requestId: string;
  scope: QueryScope;
  role: "viewer" | "investigator";
  expiresAtMs: number;
}

export interface MetricResult {
  state: QueryState;
  value: number | null;
  numerator: number | null;
  denominator: number | null;
  sampleCount: number;
  coverage: number | null;
  asOfMs: number;
  source: string;
  reason: string | null;
}

export interface ReleaseMetrics {
  javaCrashEvents: MetricResult;
  anrEvents: MetricResult;
  affectedInstallations: MetricResult;
  activeInstallations: MetricResult;
  affectedInstallationRatio: MetricResult;
  incidentFingerprintCount: MetricResult;
  lateRatio: MetricResult;
  sdkDropRate: MetricResult;
  crashFreeSessions: MetricResult;
  anrFreeSessions: MetricResult;
}

export interface ReleaseSlice {
  releaseVersion: string;
  state: QueryState;
  declaredSampleCount: number;
  eligibleSampleCount: number;
  excludedNonOccurrenceCount: number;
  releaseIdentityCoverage: number | null;
  installationIdentityCoverage: number | null;
  metrics: ReleaseMetrics;
}

export interface ReleaseHealth {
  requestId: string;
  scope: QueryScope;
  window: QueryWindow;
  newRelease: ReleaseSlice;
  baselineRelease: ReleaseSlice;
  comparison: {
    javaCrashEventDelta: number | null;
    anrEventDelta: number | null;
    affectedInstallationDelta: number | null;
    affectedInstallationRatioDelta: number | null;
  };
  trend: ReleaseTrendPoint[];
}

export interface ReleaseTrendPoint {
  bucketStartMs: number;
  bucketEndMs: number;
  newJavaCrashEvents: number;
  newAnrEvents: number;
  baselineJavaCrashEvents: number;
  baselineAnrEvents: number;
}

export interface FingerprintItem {
  fingerprint: string;
  eventFamily: string;
  eventCount: number;
  affectedInstallationCount: number | null;
  firstSeenMs: number;
  lastSeenMs: number;
}

export interface Fingerprints {
  requestId: string;
  scope: QueryScope;
  window: QueryWindow;
  releaseVersion: string;
  state: QueryState;
  sampleCount: number;
  fingerprintCoverage: number | null;
  installationReason?: string | null;
  items: FingerprintItem[];
}

export interface IssueDistributionItem {
  label: string;
  eventCount: number;
  affectedInstallationCount: number | null;
}

export interface IssueDistribution {
  dimension: string;
  state: QueryState;
  reason: string | null;
  items: IssueDistributionItem[];
}

export interface IssueTrendPoint {
  bucketStartMs: number;
  bucketEndMs: number;
  eventCount: number;
  affectedInstallationCount: number | null;
}

export interface IssueDetail {
  requestId: string;
  scope: QueryScope;
  window: QueryWindow;
  fingerprint: string;
  state: QueryState;
  reason: string | null;
  eventFamily: string | null;
  eventCount: number;
  affectedInstallationCount: number | null;
  firstSeenMs: number | null;
  lastSeenMs: number | null;
  trend: IssueTrendPoint[];
  releases: IssueDistribution;
  scenes: IssueDistribution;
  deviceModels: IssueDistribution;
  androidVersions: IssueDistribution;
}

export interface DataQuality {
  requestId: string;
  scope: QueryScope;
  window: QueryWindow;
  releaseVersion: string | null;
  state: QueryState;
  sampleCount: number;
  protocolCounts: Record<string, number>;
  schemaVersionCounts: Record<string, number>;
  inboxStatusCounts: Record<string, number>;
  releaseIdentity: MetricResult;
  installationIdentity: MetricResult;
  sdkHealth: MetricResult;
  lateData: MetricResult;
}

export interface SymbolizationSummary {
  status: string;
  fingerprint: string | null;
  toolName: string | null;
  toolVersion: string | null;
  lastErrorCode: string | null;
}

export interface EventSummary {
  eventId: string;
  module: string;
  name: string;
  eventFamily: string;
  appVersion: string | null;
  appBuild: string | null;
  versionCode: string | null;
  variant: string | null;
  releaseIdentityQuality: string;
  installationIdentityQuality: string;
  installationHmac: string | null;
  occurrenceTimestampMs: number;
  collectionTimestampMs: number | null;
  receivedAt: string;
  timestampQuality: string;
  scene: string | null;
  processName: string | null;
  threadName: string | null;
  foreground: boolean | null;
  incidentFingerprint: string | null;
  inboxStatus: string;
  symbolization: SymbolizationSummary | null;
}

export interface EventPage {
  requestId: string;
  scope: QueryScope;
  window: QueryWindow;
  items: EventSummary[];
  nextCursor: string | null;
}

export interface EventMetadata {
  rawAvailable?: boolean;
  requestId: string;
  scope: QueryScope;
  event: EventSummary;
  schemaVersion: string;
  protocol: string;
  normalizationVersion: number;
  fieldStates: Record<string, string>;
}

export interface RawEvent {
  requestId: string;
  scope: QueryScope;
  event: EventSummary;
  rawPayload: Record<string, unknown>;
  symbolizedResult: Record<string, unknown> | null;
}

export type ReleaseDecisionValue = "continue" | "pause" | "rollback";

export interface ReleaseEvidence {
  releaseState: string;
  baselineState: string | null;
  javaCrashEvents: number | null;
  anrEvents: number | null;
  affectedInstallations: number | null;
  activeInstallations: number | null;
  affectedInstallationRatio: number | null;
  dataQualityState: string;
  queryRequestId: string;
}

export interface ReleaseDecision {
  id: number;
  releaseVersion: string;
  decision: ReleaseDecisionValue;
  actor: string;
  evidenceFromMs: number;
  evidenceToMs: number;
  reason: string;
  evidence: ReleaseEvidence;
  createdAt: string;
}

export interface ReleaseDecisionList {
  requestId: string;
  scope: QueryScope;
  items: ReleaseDecision[];
}

export interface ReleaseDecisionInput {
  releaseVersion: string;
  decision: ReleaseDecisionValue;
  evidenceFromMs: number;
  evidenceToMs: number;
  reason: string;
  evidence: ReleaseEvidence;
}

export interface QueryFilters {
  newRelease: string;
  baselineRelease: string;
  fromMs: number;
  toMs: number;
}

export interface ApiErrorBody {
  requestId?: string;
  code?: string;
  message?: string;
  retryable?: boolean;
}
