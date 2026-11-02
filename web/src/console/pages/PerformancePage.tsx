import { useCallback, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { StateBadge } from "../../components/StateBadge";
import { formatDateTime, formatPercent } from "../../format";
import { getPerformance } from "../../productApi";
import type { ConsoleContextValue } from "../context";
import { ErrorPanel, LoadingPanel, PageHeader } from "../Primitives";
import { useProductRead } from "../useProductRead";

const number = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("zh-CN", { maximumFractionDigits: 3 });

export function PerformancePage() {
  const { filters, setFilters } = useOutletContext<ConsoleContextValue>();
  const [metric, setMetric] = useState("launch.cold_start.launchDurationMs");
  const [releases, setReleases] = useState({ current: filters.newRelease, baseline: filters.baselineRelease });
  const read = useCallback(() => getPerformance(filters, metric), [filters, metric]);
  const { data, error, reload } = useProductRead(read);
  if (error) return <ErrorPanel message={error} onRetry={() => void reload()} />;
  if (!data) return <LoadingPanel label="正在读取性能观测…" />;
  return <div className="console-page">
    <PageHeader title="性能分析" description="上报观测的版本对比；缺失不填零，摘要分位数不代表单帧或单请求分位数。" actions={<button type="button" onClick={() => void reload()}>刷新</button>} />
    <form className="product-form" onSubmit={(event) => { event.preventDefault(); setFilters((current) => ({ ...current, newRelease: releases.current.trim(), baselineRelease: releases.baseline.trim() })); }}>
      <label>当前版本<input required value={releases.current} onChange={(event) => setReleases({ ...releases, current: event.target.value })} /></label>
      <label>基线版本<input required value={releases.baseline} onChange={(event) => setReleases({ ...releases, baseline: event.target.value })} /></label>
      <button disabled={!releases.current.trim() || !releases.baseline.trim() || releases.current.trim() === releases.baseline.trim()}>应用对比</button>
    </form>
    <section className="console-panel"><h2>覆盖与口径</h2><p>未绑定发生版本 {data.nonOccurrenceCount} · 迟到 {data.lateCount} · 网络失败比例 {formatPercent(data.networkErrorRatio)} · Jank 比例 {formatPercent(data.jankRatio)}</p>
      <p>当前加权 FPS：{number(data.weightedFps.value)} <StateBadge state={data.weightedFps.state} /> · 有效窗口 {data.weightedFps.sampleCount}/{data.weightedFps.eventCount} · 覆盖 {formatPercent(data.weightedFps.coverage)}</p>
      <p>基线加权 FPS：{number(data.baselineWeightedFps.value)} <StateBadge state={data.baselineWeightedFps.state} /></p>
    </section>
    <section className="console-panel"><h2>数值观测</h2><label>趋势指标<select value={metric} onChange={(event) => setMetric(event.target.value)}>{data.metrics.map((item) => <option key={item.id} value={item.id}>{item.module}/{item.name} · {item.label} ({item.unit})</option>)}</select></label>
      <div className="product-table"><table><thead><tr><th>指标</th><th>状态</th><th>有效/事件</th><th>覆盖</th><th>缺失/无效/不可用</th><th>均值 / 最小 / 最大</th><th>P50 / P75 / P95</th><th>基线 P95</th><th>P95 差值</th></tr></thead><tbody>
        {data.metrics.map((item) => <tr key={item.id}><th>{item.label}<small>{item.id} · {item.unit}</small></th><td><StateBadge state={item.current.state} /></td><td>{item.current.sampleCount}/{item.current.eventCount}</td><td>{formatPercent(item.current.coverage)}</td><td>{item.current.missingCount}/{item.current.invalidCount}/{item.current.unavailableCount}</td><td>{number(item.current.mean)} / {number(item.current.minimum)} / {number(item.current.maximum)}</td><td>{number(item.current.p50)} / {number(item.current.p75)} / {number(item.current.p95)}</td><td>{number(item.baseline.p95)} <StateBadge state={item.baseline.state} /></td><td>{number(item.p95Delta)}</td></tr>)}
      </tbody></table></div>
    </section>
    <section className="console-panel"><h2>发生时间趋势</h2><div className="product-table"><table><thead><tr><th>时间</th><th>当前 P95</th><th>基线 P95</th><th>当前有效/事件</th></tr></thead><tbody>{data.trend.map((point) => <tr key={point.fromMs}><td>{formatDateTime(point.fromMs)}</td><td>{number(point.current.p95)} <StateBadge state={point.current.state} /></td><td>{number(point.baseline.p95)} <StateBadge state={point.baseline.state} /></td><td>{point.current.sampleCount}/{point.current.eventCount}</td></tr>)}</tbody></table></div></section>
    <section className="console-panel"><h2>固定事件目录</h2><div className="product-table"><table><thead><tr><th>事件</th><th>当前</th><th>基线</th><th>命中合计</th><th>未绑定发生版本</th></tr></thead><tbody>{data.eventStatistics.map((item) => <tr key={`${item.module}/${item.name}`}><th>{item.module}/{item.name}</th><td>{item.currentCount}</td><td>{item.baselineCount}</td><td>{item.totalCount}</td><td>{item.nonOccurrenceCount}</td></tr>)}</tbody></table></div></section>
    <section className="console-panel"><h2>固定类别</h2><div className="product-table"><table><thead><tr><th>类别</th><th>当前分布</th><th>基线分布</th><th>缺失/无效/未知/过期</th></tr></thead><tbody>{data.dimensions.map((item) => <tr key={item.id}><th>{item.id}</th><td><StateBadge state={item.current.state} /> {Object.entries(item.current.counts).map(([key, count]) => `${key}: ${count}`).join(" · ") || "—"}</td><td>{Object.entries(item.baseline.counts).map(([key, count]) => `${key}: ${count}`).join(" · ") || "—"}</td><td>{item.current.missingCount}/{item.current.invalidCount}/{item.current.unknownCount}/{item.current.expiredCount}</td></tr>)}</tbody></table></div></section>
  </div>;
}
