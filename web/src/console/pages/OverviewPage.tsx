import { useCallback, useEffect, useState, type FormEvent } from "react";
import { AlertTriangle, ArrowRight, RefreshCw } from "lucide-react";
import { Link, useOutletContext } from "react-router-dom";

import { getDataQuality, getFingerprints, getReleaseHealth } from "../../api";
import { StateBadge } from "../../components/StateBadge";
import { formatDateTime, formatPercent, shortId } from "../../format";
import type { DataQuality, Fingerprints, ReleaseHealth } from "../../types";
import type { ConsoleContextValue } from "../context";
import { useRequestGuard } from "../useRequestGuard";
import { ErrorPanel, LoadingPanel, MetricTile, PageHeader, SectionHeading } from "../Primitives";
import { TrendChart } from "../TrendChart";

export function OverviewPage() {
  const consoleContext = useOutletContext<ConsoleContextValue>();
  const { filters, setFilters, handleFailure, session } = consoleContext;
  const [health, setHealth] = useState<ReleaseHealth | null>(null);
  const [quality, setQuality] = useState<DataQuality | null>(null);
  const [fingerprints, setFingerprints] = useState<Fingerprints | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newRelease, setNewRelease] = useState(filters.newRelease);
  const [baselineRelease, setBaselineRelease] = useState(filters.baselineRelease);
  const begin = useRequestGuard();

  const load = useCallback(async () => {
    const current = begin();
    setHealth(null); setQuality(null); setFingerprints(null);
    setLoading(true);
    setError(null);
    const results = await Promise.allSettled([
      getReleaseHealth(filters),
      getDataQuality(filters),
      getFingerprints(filters),
    ]);
    const [healthResult, qualityResult, fingerprintResult] = results;
    if (!current()) return;
    const failure = results.find((result) => result.status === "rejected");
    if (failure?.status === "rejected") {
      setError(handleFailure(failure.reason, "总览证据查询失败"));
      setLoading(false);
      return;
    }
    if (healthResult.status === "fulfilled") setHealth(healthResult.value);
    else setError(handleFailure(healthResult.reason, "发布健康查询失败"));
    if (qualityResult.status === "fulfilled") setQuality(qualityResult.value);
    if (fingerprintResult.status === "fulfilled") setFingerprints(fingerprintResult.value);
    setLoading(false);
  }, [begin, filters, handleFailure]);

  useEffect(() => { void load(); }, [load]);

  function applyRelease(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = newRelease.trim();
    const baseline = baselineRelease.trim();
    if (next === "" || baseline === "" || next === baseline) return;
    setFilters((current) => ({ ...current, newRelease: next, baselineRelease: baseline }));
  }

  const appPath = `/apps/${encodeURIComponent(session.scope.appId)}`;
  if (loading && health === null) return <LoadingPanel label="正在计算可信发布结论…" />;
  if (error !== null && health === null) return <ErrorPanel message={error} onRetry={() => void load()} />;
  if (health === null) return null;

  const riskState = quality?.state ?? health.newRelease.state;
  const topIssues = fingerprints?.items.slice(0, 5) ?? [];
  return (
    <div className="console-page">
      <PageHeader
        eyebrow="总览 / 发布诊断"
        title="应用总览"
        description={`${health.newRelease.releaseVersion} 与 ${health.baselineRelease.releaseVersion} 的 occurrence-time 可信对比`}
        actions={
          <button className="console-button secondary" type="button" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={15} /> 刷新
          </button>
        }
      />

      <form className="release-compare-bar" onSubmit={applyRelease}>
        <label>当前版本<input value={newRelease} onChange={(event) => setNewRelease(event.target.value)} /></label>
        <span>对比</span>
        <label>基线版本<input value={baselineRelease} onChange={(event) => setBaselineRelease(event.target.value)} /></label>
        <button className="console-button primary" type="submit" disabled={newRelease.trim() === baselineRelease.trim()}>应用对比</button>
        <small>范围：{formatDateTime(filters.fromMs)} — {formatDateTime(filters.toMs)}</small>
      </form>

      <section className={`trust-banner trust-${riskState.toLowerCase()}`}>
        <AlertTriangle size={19} aria-hidden="true" />
        <div>
          <strong>数据可信度：<StateBadge state={riskState} /></strong>
          <span>
            版本身份 {formatPercent(quality?.releaseIdentity.coverage ?? null)} · 安装身份 {formatPercent(quality?.installationIdentity.coverage ?? null)} · Crash-free Sessions 不可计算
          </span>
        </div>
        <Link to={`${appPath}/quality`}>查看数据质量 <ArrowRight size={14} /></Link>
      </section>

      <section className="console-metric-row" aria-label="发布关键指标">
        <MetricTile label="Java Crash" metric={health.newRelease.metrics.javaCrashEvents} delta={health.comparison.javaCrashEventDelta} />
        <MetricTile label="ANR" metric={health.newRelease.metrics.anrEvents} delta={health.comparison.anrEventDelta} />
        <MetricTile label="受影响安装" metric={health.newRelease.metrics.affectedInstallations} delta={health.comparison.affectedInstallationDelta} />
        <MetricTile label="活跃安装" metric={health.newRelease.metrics.activeInstallations} />
        <MetricTile label="安装影响率" metric={health.newRelease.metrics.affectedInstallationRatio} kind="ratio" delta={health.comparison.affectedInstallationRatioDelta} />
        <MetricTile label="SDK 丢弃率" metric={health.newRelease.metrics.sdkDropRate} kind="ratio" />
      </section>

      <div className="overview-grid">
        <section className="console-panel trend-panel">
          <SectionHeading title="Crash / ANR 发生趋势" meta={<span>最多 24 个真实零值桶</span>} />
          <TrendChart
            timestamps={health.trend.map((point) => point.bucketStartMs)}
            series={[
              { label: `${filters.newRelease} Crash`, color: "#52d9ba", values: health.trend.map((point) => point.newJavaCrashEvents) },
              { label: `${filters.newRelease} ANR`, color: "#ffb84d", values: health.trend.map((point) => point.newAnrEvents) },
              { label: `${filters.baselineRelease} Crash`, color: "#63758a", values: health.trend.map((point) => point.baselineJavaCrashEvents) },
            ]}
            summary={`当前版本 Java Crash ${health.newRelease.metrics.javaCrashEvents.value ?? "不可用"}，ANR ${health.newRelease.metrics.anrEvents.value ?? "不可用"}；基线 Java Crash ${health.baselineRelease.metrics.javaCrashEvents.value ?? "不可用"}`}
          />
        </section>
        <aside className="console-panel release-proof">
          <SectionHeading title="结论依据" />
          <dl>
            <div><dt>当前版本状态</dt><dd><StateBadge state={health.newRelease.state} /></dd></div>
            <div><dt>基线版本状态</dt><dd><StateBadge state={health.baselineRelease.state} /></dd></div>
            <div><dt>发生时合格样本</dt><dd>{health.newRelease.eligibleSampleCount} / {health.newRelease.declaredSampleCount}</dd></div>
            <div><dt>Request</dt><dd title={health.requestId}>{shortId(health.requestId, 7)}</dd></div>
            <div><dt>Source</dt><dd>durable_inbox</dd></div>
          </dl>
          <p>会话身份尚未标准化，因此不会展示 Crash-free 或 ANR-free 的伪百分比。</p>
        </aside>
      </div>

      <section className="console-panel issues-preview">
        <SectionHeading title="Top Issues" meta={<Link to={`${appPath}/issues`}>查看全部 <ArrowRight size={14} /></Link>} />
        {topIssues.length === 0 ? (
          <p className="table-empty">当前窗口没有合格的 Crash / ANR 指纹。</p>
        ) : (
          <div className="console-table-wrap">
            <table className="console-table">
              <thead><tr><th>问题类型</th><th>指纹</th><th>事件</th><th>影响安装</th><th>首次 / 最近</th><th><span className="sr-only">操作</span></th></tr></thead>
              <tbody>
                {topIssues.map((item) => (
                  <tr key={item.fingerprint}>
                    <td><strong>{item.eventFamily}</strong></td>
                    <td><code>{shortId(item.fingerprint, 9)}</code></td>
                    <td>{item.eventCount}</td>
                    <td>{item.affectedInstallationCount}</td>
                    <td>{formatDateTime(item.firstSeenMs)}<small>{formatDateTime(item.lastSeenMs)}</small></td>
                    <td><Link aria-label={`查看 ${item.eventFamily} 详情`} to={`${appPath}/issues/${item.fingerprint}`}><ArrowRight size={16} /></Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
