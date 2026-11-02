import { useCallback } from "react";
import { Link, useOutletContext, useParams } from "react-router-dom";
import { StateBadge } from "../../components/StateBadge";
import { formatDateTime } from "../../format";
import { canInvestigate } from "../../permissions";
import { getTrace, getTraces } from "../../productApi";
import type { SpanObservation } from "../../productTypes";
import type { ConsoleContextValue } from "../context";
import { EmptyPanel, ErrorPanel, LoadingPanel, PageHeader } from "../Primitives";
import { useProductRead } from "../useProductRead";

const number = (value: number | null) => value === null ? "—" : value.toLocaleString("zh-CN", { maximumFractionDigits: 3 });

export function TracesPage() {
  const { session } = useOutletContext<ConsoleContextValue>();
  const { traceId } = useParams();
  if (!canInvestigate(session.role)) return <EmptyPanel title="需要调查权限" message="Trace 包含 L1 事件信息，仅调查员和管理员可读。" />;
  return traceId ? <TraceDetailView id={traceId} /> : <TraceListView />;
}

function TraceListView() {
  const { filters, session } = useOutletContext<ConsoleContextValue>();
  const read = useCallback(() => getTraces(filters), [filters]);
  const { data, error, reload } = useProductRead(read);
  if (error) return <ErrorPanel message={error} onRetry={() => void reload()} />;
  if (!data) return <LoadingPanel label="正在读取手动 Trace…" />;
  const appPath = `/apps/${encodeURIComponent(session.scope.appId)}`;
  return <div className="console-page"><PageHeader title="Trace" description={`当前版本 ${filters.newRelease} 的手动 Span；根节点存在不证明链路完整。`} actions={<button onClick={() => void reload()}>刷新</button>} />
    <section className="console-panel"><StateBadge state={data.state} /><p>Trace {data.traceCount} · Span {data.sampleCount} · 无效 {data.invalidSpanCount} · 原文过期 {data.expiredSpanCount} · 时长样本 {data.durationSampleCount}</p><p>Span 平均时长 {number(data.meanSpanDurationMs)} ms · P95 {number(data.p95SpanDurationMs)} ms</p>{data.truncated && <p>仅展示最近 50 条 Trace，请缩小时间窗口。</p>}</section>
    {data.items.length === 0 ? <EmptyPanel title="没有可展示的 Trace" message="缺失、原文过期或旧 Span 不会被补成完整时间线。" /> : <section className="console-panel product-table"><table><thead><tr><th>Trace</th><th>状态 / 覆盖</th><th>Span / 错误</th><th>观测跨度</th><th>孤立 / 关系异常 / 迟到</th></tr></thead><tbody>{data.items.map((item) => <tr key={item.traceId}><th><Link to={`${appPath}/traces/${encodeURIComponent(item.traceId)}`}>{item.traceId}</Link><small>{formatDateTime(item.lastSeenMs)}</small></th><td><StateBadge state={item.state} /><small>{item.coverageState}</small></td><td>{item.spanCount} / {item.errorSpanCount}</td><td>{item.durationMs ?? "—"} ms</td><td>{item.orphanCount} / {item.invalidRelationCount} / {item.lateCount}</td></tr>)}</tbody></table></section>}
  </div>;
}

function TraceDetailView({ id }: { id: string }) {
  const { filters, session } = useOutletContext<ConsoleContextValue>();
  const read = useCallback(() => getTrace(id, filters), [id, filters]);
  const { data, error, reload } = useProductRead(read);
  if (error) return <ErrorPanel message={error} onRetry={() => void reload()} />;
  if (!data) return <LoadingPanel label="正在读取 Span 关系和真实起止…" />;
  const appPath = `/apps/${encodeURIComponent(session.scope.appId)}`;
  const timed = data.spans.filter((span) => span.timingState === "AVAILABLE" && span.startMs !== null && span.endMs !== null);
  const start = timed.length ? Math.min(...timed.map((span) => span.startMs!)) : 0;
  const end = timed.length ? Math.max(...timed.map((span) => span.endMs!)) : 0;
  const width = Math.max(1, end - start);
  return <div className="console-page"><PageHeader title="Trace 详情" description={id} backTo={`${appPath}/traces`} /><section className="console-panel"><StateBadge state={data.summary.state} /><p>{data.summary.coverageState} · Span {data.summary.spanCount} · 可用起止 {data.summary.timingAvailableCount} · 孤立 {data.summary.orphanCount} · 关系异常 {data.summary.invalidRelationCount}</p><p>观测跨度 {data.summary.durationMs ?? "—"} ms。缺少真实起止的 Span 不绘制时间条。</p></section>
    <section className="console-panel product-table"><table><thead><tr><th>Span / 父节点</th><th>关系 / 状态</th><th>真实时间线</th><th>起止时间</th><th>单调时长</th><th>证据</th></tr></thead><tbody>{data.spans.map((span) => <tr key={span.eventId}><th style={{ paddingLeft: `${12 + depth(span, data.spans) * 16}px` }}>{span.name}<small>{span.spanId} / {span.parentSpanId ?? "ROOT"}</small></th><td>{span.relationState}<small>{span.status} · {span.timingState}{span.late ? " · LATE" : ""}</small></td><td>{span.timingState === "AVAILABLE" && span.startMs !== null && span.endMs !== null ? <div className="span-track" aria-label={`${span.name} 起始 ${span.startMs - start} ms，结束 ${span.endMs - start} ms`}><span style={{ marginLeft: `${(span.startMs - start) / width * 100}%`, width: `${(span.endMs - span.startMs) / width * 100}%` }} /></div> : "不可用"}</td><td>{span.startMs === null ? "—" : formatDateTime(span.startMs)}<small>{span.endMs === null ? "—" : formatDateTime(span.endMs)}</small></td><td>{span.durationMs ?? "—"} ms</td><td><Link to={`${appPath}/events/${encodeURIComponent(span.eventId)}`}>事件详情</Link><small>自定义属性 {span.customAttributeCount} 项，原文需审计读取</small></td></tr>)}</tbody></table></section>
  </div>;
}

function depth(span: SpanObservation, spans: SpanObservation[]): number {
  // Invalid, duplicated and cyclic relationships are displayed without inventing a tree.
  if (span.relationState !== "CHILD") return 0;
  let current = span;
  const seen = new Set([span.spanId]);
  let level = 0;
  while (current.parentSpanId && level < 8) {
    const parents = spans.filter((item) => item.spanId === current.parentSpanId);
    const parent = parents[0];
    if (parents.length !== 1 || parent === undefined || seen.has(parent.spanId)) break;
    current = parent; seen.add(current.spanId); level++;
  }
  return level;
}
