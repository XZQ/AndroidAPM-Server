import { formatMetric, formatPercent, reasonLabel } from "../format";
import type { MetricResult } from "../types";
import { StateBadge } from "./StateBadge";

interface MetricCardProps {
  label: string;
  metric: MetricResult;
  kind?: "count" | "ratio";
  delta?: number | null;
}

export function MetricCard({ label, metric, kind = "count", delta }: MetricCardProps) {
  const reason = reasonLabel(metric.reason);
  const deltaText = delta == null ? null : kind === "ratio" ? formatPercent(delta) : signed(delta);
  return (
    <article className={`metric-card metric-${metric.state.toLowerCase()}`}>
      <div className="metric-heading">
        <h3>{label}</h3>
        <StateBadge state={metric.state} />
      </div>
      <div className="metric-value-row">
        <strong className="metric-value">{formatMetric(metric, kind)}</strong>
        {deltaText !== null && (
          <span className={`metric-delta ${deltaClass(delta ?? 0)}`} aria-label={`相对基线 ${deltaText}`}>
            {deltaText}
          </span>
        )}
      </div>
      <dl className="metric-meta">
        <div>
          <dt>样本</dt>
          <dd>{metric.sampleCount.toLocaleString("zh-CN")}</dd>
        </div>
        <div>
          <dt>覆盖</dt>
          <dd>{formatPercent(metric.coverage)}</dd>
        </div>
        {metric.denominator !== null && (
          <div>
            <dt>分子 / 分母</dt>
            <dd>
              {metric.numerator ?? "—"} / {metric.denominator}
            </dd>
          </div>
        )}
      </dl>
      {reason !== null && <p className="metric-reason">{reason}</p>}
    </article>
  );
}

function signed(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toLocaleString("zh-CN")}`;
}

function deltaClass(value: number): string {
  if (value > 0) return "delta-up";
  if (value < 0) return "delta-down";
  return "delta-flat";
}
