import { formatPercent, reasonLabel, shortId } from "../format";
import type { DataQuality, MetricResult } from "../types";
import { StateBadge } from "./StateBadge";

interface DataQualityStripProps {
  quality: DataQuality;
}

export function DataQualityStrip({ quality }: DataQualityStripProps) {
  return (
    <section className="quality-panel" aria-labelledby="quality-heading">
      <div className="section-heading compact-heading">
        <div>
          <p className="eyebrow">TRUST GATE</p>
          <h2 id="quality-heading">数据可信度</h2>
        </div>
        <div className="heading-status">
          <StateBadge state={quality.state} />
          <span>{quality.sampleCount.toLocaleString("zh-CN")} 条事实</span>
        </div>
      </div>
      <div className="quality-grid">
        <QualityItem label="发生时版本" metric={quality.releaseIdentity} value="coverage" />
        <QualityItem label="安装身份" metric={quality.installationIdentity} value="coverage" />
        <QualityItem label="SDK 丢弃" metric={quality.sdkHealth} value="ratio" />
        <QualityItem label="迟到数据" metric={quality.lateData} value="ratio" />
      </div>
      <div className="quality-foot">
        <span>协议 {formatCounts(quality.protocolCounts)}</span>
        <span>Schema {formatCounts(quality.schemaVersionCounts)}</span>
        <span>Inbox {formatCounts(quality.inboxStatusCounts)}</span>
        <span title={quality.requestId}>Request {shortId(quality.requestId, 6)}</span>
      </div>
    </section>
  );
}

interface QualityItemProps {
  label: string;
  metric: MetricResult;
  value: "coverage" | "ratio";
}

function QualityItem({ label, metric, value }: QualityItemProps) {
  const shown = value === "coverage" ? metric.coverage : metric.value;
  return (
    <article className={`quality-item quality-${metric.state.toLowerCase()}`}>
      <div className="quality-item-title">
        <span>{label}</span>
        <StateBadge state={metric.state} />
      </div>
      <strong>{formatPercent(shown)}</strong>
      <small>{reasonLabel(metric.reason) ?? `样本 ${metric.sampleCount.toLocaleString("zh-CN")}`}</small>
    </article>
  );
}

function formatCounts(values: Record<string, number>): string {
  const entries = Object.entries(values);
  if (entries.length === 0) return "—";
  return entries.map(([key, value]) => `${key}:${value}`).join(" · ");
}
