import { useCallback, useEffect, useState } from "react";
import { Database, RadioTower, ShieldCheck, TimerReset } from "lucide-react";
import { useOutletContext } from "react-router-dom";

import { getDataQuality } from "../../api";
import { StateBadge } from "../../components/StateBadge";
import { formatDateTime, formatPercent, reasonLabel, shortId } from "../../format";
import type { DataQuality, MetricResult } from "../../types";
import type { ConsoleContextValue } from "../context";
import { useRequestGuard } from "../useRequestGuard";
import { ErrorPanel, LoadingPanel, PageHeader, SectionHeading } from "../Primitives";

export function QualityPage() {
  const { filters, handleFailure } = useOutletContext<ConsoleContextValue>();
  const [quality, setQuality] = useState<DataQuality | null>(null);
  const [error, setError] = useState<string | null>(null);
  const begin = useRequestGuard();
  const load = useCallback(async () => {
    const current = begin();
    setQuality(null);
    setError(null);
    try { const result = await getDataQuality(filters); if (current()) setQuality(result); }
    catch (caught) { if (current()) setError(handleFailure(caught, "数据质量查询失败")); }
  }, [begin, filters, handleFailure]);
  useEffect(() => { void load(); }, [load]);

  if (quality === null && error === null) return <LoadingPanel label="正在核对遥测可解释性…" />;
  if (quality === null && error !== null) return <ErrorPanel message={error} onRetry={() => void load()} />;
  if (quality === null) return null;

  return (
    <div className="console-page">
      <PageHeader eyebrow="可信度 / 可解释性" title="数据质量" description={`${quality.releaseVersion ?? "全部版本"} · 缺失、真实零值、迟到和覆盖未知严格区分`} actions={<StateBadge state={quality.state} />} />
      <section className="quality-proof-strip">
        <span>样本 <strong>{quality.sampleCount}</strong></span>
        <span>as of <strong>{latestAsOf(quality)}</strong></span>
        <span>source <strong>durable_inbox</strong></span>
        <span>request <strong title={quality.requestId}>{shortId(quality.requestId, 8)}</strong></span>
      </section>
      <div className="quality-grid">
        <QualityCard icon={<ShieldCheck />} title="发生时版本身份" metric={quality.releaseIdentity} />
        <QualityCard icon={<ShieldCheck />} title="安装身份" metric={quality.installationIdentity} />
        <QualityCard icon={<RadioTower />} title="SDK 自健康" metric={quality.sdkHealth} />
        <QualityCard icon={<TimerReset />} title="迟到数据" metric={quality.lateData} />
      </div>
      <div className="quality-contract-grid">
        <CountPanel title="协议分布" values={quality.protocolCounts} />
        <CountPanel title="Schema 分布" values={quality.schemaVersionCounts} />
        <CountPanel title="Inbox 状态" values={quality.inboxStatusCounts} />
      </div>
      <section className="console-panel quality-boundary">
        <SectionHeading title="解释边界" meta={<Database size={16} />} />
        <ul>
          <li>身份覆盖来自标准协议字段，不从任意 context/extras 推导。</li>
          <li>Crash-free Sessions 与 ANR-free Sessions 仍不可计算，因为客户端尚未提供 occurrence-bound session identity。</li>
          <li>本页证明查询契约与本地数据质量，不证明 PostgreSQL、SigNoz、TLS 或云端告警已部署。</li>
        </ul>
      </section>
    </div>
  );
}

function QualityCard({ icon, title, metric }: { icon: React.ReactNode; title: string; metric: MetricResult }) {
  return <article className="quality-card"><div className="quality-card-head">{icon}<span>{title}</span><StateBadge state={metric.state} /></div><strong>{formatPercent(metric.coverage ?? metric.value)}</strong><p>{reasonLabel(metric.reason) ?? `样本 ${metric.sampleCount.toLocaleString("zh-CN")}`}</p><small>{metric.source} · {formatDateTime(metric.asOfMs)}</small></article>;
}

function CountPanel({ title, values }: { title: string; values: Record<string, number> }) {
  const entries = Object.entries(values).sort((a, b) => b[1] - a[1]);
  return <section className="console-panel count-panel"><SectionHeading title={title} />{entries.length === 0 ? <p>当前窗口没有可报告数据。</p> : <dl>{entries.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value.toLocaleString("zh-CN")}</dd></div>)}</dl>}</section>;
}

function latestAsOf(quality: DataQuality): string {
  return formatDateTime(Math.max(quality.releaseIdentity.asOfMs, quality.installationIdentity.asOfMs, quality.sdkHealth.asOfMs, quality.lateData.asOfMs));
}
