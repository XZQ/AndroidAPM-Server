import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ArrowRight, Filter, Search } from "lucide-react";
import { Link, useOutletContext } from "react-router-dom";

import { getEvents } from "../../api";
import { formatDateTime, shortId } from "../../format";
import type { EventPage } from "../../types";
import type { ConsoleContextValue } from "../context";
import { EmptyPanel, ErrorPanel, LoadingPanel, PageHeader, SectionHeading } from "../Primitives";

export function ExplorePage() {
  const context = useOutletContext<ConsoleContextValue>();
  const { filters, session, handleFailure } = context;
  const [page, setPage] = useState<EventPage | null>(null);
  const [module, setModule] = useState("");
  const [name, setName] = useState("");
  const [active, setActive] = useState({ module: "", name: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (cursor?: string) => {
    if (session.role !== "investigator") return;
    setLoading(true); setError(null);
    try {
      const next = await getEvents(filters, undefined, cursor, active.module || undefined, active.name || undefined);
      setPage((current) => cursor !== undefined && current !== null ? { ...next, items: [...current.items, ...next.items] } : next);
    } catch (caught) { setError(handleFailure(caught, "事件探索查询失败")); }
    finally { setLoading(false); }
  }, [active.module, active.name, filters, handleFailure, session.role]);
  useEffect(() => { void load(); }, [load]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActive({ module: module.trim(), name: name.trim() });
  }
  const appPath = `/apps/${encodeURIComponent(session.scope.appId)}`;
  if (session.role !== "investigator") {
    return <div className="console-page"><PageHeader title="事件探索" description="安装级事件属于 L1 数据" /><EmptyPanel title="需要调查员权限" message="当前 viewer 凭据只能读取 L0 聚合。" /></div>;
  }
  return (
    <div className="console-page">
      <PageHeader eyebrow="事件证据 / L1" title="事件探索" description={`在固定 scope 内检索 ${filters.newRelease} 的 allow-listed 事件元数据`} />
      <form className="explore-filter" onSubmit={submit}>
        <Filter size={17} aria-hidden="true" />
        <label>模块<input value={module} onChange={(event) => setModule(event.target.value)} placeholder="例如 crash" /></label>
        <label>事件名<input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如 java_crash" /></label>
        <button className="console-button primary" type="submit"><Search size={15} /> 查询</button>
        <span>时间窗最多 31 天，结果使用 HMAC 游标</span>
      </form>
      {page === null && loading ? <LoadingPanel label="正在检索事件…" /> : null}
      {error !== null ? <ErrorPanel message={error} onRetry={() => void load()} /> : null}
      {page !== null ? (
        <section className="console-panel explore-results">
          <SectionHeading title="事件结果" meta={<code>{shortId(page.requestId, 8)}</code>} />
          {page.items.length === 0 ? <EmptyPanel title="没有匹配事件" message="扩大时间范围或检查 module/name；空结果不会被当成监控健康。" /> : (
            <div className="console-table-wrap"><table className="console-table"><thead><tr><th>发生时间</th><th>模块 / 事件</th><th>版本</th><th>场景</th><th>进程 / 线程</th><th>状态</th><th><span className="sr-only">详情</span></th></tr></thead>
              <tbody>{page.items.map((item) => <tr key={item.eventId}>
                <td>{formatDateTime(item.occurrenceTimestampMs)}<small>{item.timestampQuality}</small></td>
                <td><strong>{item.module}/{item.name}</strong><code>{shortId(item.eventId, 8)}</code></td>
                <td>{item.appVersion ?? "—"}<small>{item.releaseIdentityQuality}</small></td>
                <td>{item.scene ?? "—"}</td><td>{item.processName ?? "—"}<small>{item.threadName ?? "—"}</small></td>
                <td>{item.inboxStatus}</td><td><Link to={`${appPath}/events/${encodeURIComponent(item.eventId)}`}><ArrowRight size={16} /></Link></td>
              </tr>)}</tbody></table></div>
          )}
          {page.nextCursor !== null ? <button className="console-button secondary load-more" type="button" disabled={loading} onClick={() => void load(page.nextCursor ?? undefined)}>{loading ? "加载中…" : "加载更多"}</button> : null}
        </section>
      ) : null}
    </div>
  );
}
