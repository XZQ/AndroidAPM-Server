import type { MetricResult, QueryState } from "./types";

const STATE_LABELS: Record<QueryState, string> = {
  PRESENT: "可用",
  ZERO: "真实零值",
  NO_DATA: "无合格样本",
  UNAVAILABLE: "不可计算",
  UNKNOWN_COVERAGE: "覆盖未知",
  DEGRADED: "数据降级",
  LATE: "存在迟到",
  ERROR: "查询错误",
};

const REASON_LABELS: Record<string, string> = {
  SESSION_ID_NOT_PROVIDED: "客户端尚未提供标准会话身份",
  OCCURRENCE_IDENTITY_NOT_PROVIDED: "缺少事件发生时版本身份",
  INSTALLATION_IDENTITY_NOT_PROVIDED: "缺少可用安装身份",
  SDK_HEALTH_NOT_PROVIDED: "当前窗口没有 SDK 自健康事件",
  SDK_HEALTH_FIELDS_OR_SAMPLE_INVALID: "SDK 自健康字段缺失、无效或没有发出样本",
  SDK_HEALTH_INSTALLATION_COVERAGE_INCOMPLETE: "SDK 自健康未覆盖足够的安装样本",
  INSUFFICIENT_INSTALLATION_SAMPLE: "安装样本不足，比例暂不可用",
  INSTALLATION_HMAC_CONTINUITY_BREAK: "窗口跨越安装密钥轮换，无法合并安装身份",
  INSTALLATION_HMAC_VERSION_MISSING: "安装身份缺少密钥版本，无法确认连续性",
  SDK_REPORTED_DROPS: "SDK 报告了事件丢弃",
  RELEASE_IDENTITY_COVERAGE_INCOMPLETE: "发生时版本身份覆盖不完整",
  INSTALLATION_IDENTITY_COVERAGE_INCOMPLETE: "安装身份覆盖不完整",
  IDENTITY_COVERAGE_INCOMPLETE: "身份覆盖不完整",
  INCIDENT_FINGERPRINT_NOT_PROVIDED: "事故指纹缺失",
  LATE_DATA_PRESENT: "窗口内存在迟到数据",
  FINGERPRINT_NOT_FOUND: "当前固定范围内没有找到该事故指纹",
  SCENE_NOT_PROVIDED: "客户端未提供标准场景字段",
  STANDARD_DEVICE_RESOURCE_NOT_PROVIDED: "客户端尚未提供标准设备型号资源",
  STANDARD_OS_RESOURCE_NOT_PROVIDED: "客户端尚未提供标准 Android 版本资源",
};

export function stateLabel(state: QueryState): string {
  return STATE_LABELS[state] ?? state;
}

export function reasonLabel(reason: string | null): string | null {
  if (reason === null) return null;
  return REASON_LABELS[reason] ?? reason;
}

export function formatMetric(metric: MetricResult, kind: "count" | "ratio" = "count"): string {
  if (metric.value === null) return "—";
  if (kind === "ratio") return formatPercent(metric.value);
  return formatInteger(metric.value);
}

export function formatInteger(value: number): string {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
}

export function formatPercent(value: number | null): string {
  if (value === null) return "—";
  return new Intl.NumberFormat("zh-CN", {
    style: "percent",
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatDateTime(value: number | string): string {
  const date = typeof value === "number" ? new Date(value) : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function shortId(value: string | null, edge = 8): string {
  if (value === null) return "—";
  if (value.length <= edge * 2 + 1) return value;
  return `${value.slice(0, edge)}…${value.slice(-edge)}`;
}
