import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowRight, Search } from "lucide-react";
import { Link, useOutletContext } from "react-router-dom";

import { getFingerprints } from "../../api";
import { StateBadge } from "../../components/StateBadge";
import { formatDateTime, formatPercent, shortId } from "../../format";
import type { Fingerprints } from "../../types";
import type { ConsoleContextValue } from "../context";
import { useRequestGuard } from "../useRequestGuard";
import { EmptyPanel, ErrorPanel, LoadingPanel, PageHeader } from "../Primitives";

export function IssuesPage() {
  const context = useOutletContext<ConsoleContextValue>();
  const { filters, handleFailure, session } = context;
  const [data, setData] = useState<Fingerprints | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [family, setFamily] = useState("ALL");
  const begin = useRequestGuard();
  const load = useCallback(async () => {
    const current = begin();
    setData(null);
    setError(null);
    try { const result = await getFingerprints(filters); if (current()) setData(result); }
    catch (caught) { if (current()) setError(handleFailure(caught, "Issues 聚合查询失败")); }
  }, [begin, filters, handleFailure]);
  useEffect(() => { void load(); }, [load]);

  const items = useMemo(() => {
    const query = search.trim().toLowerCase();
    return (data?.items ?? []).filter((item) =>
      (family === "ALL" || item.eventFamily === family)
      && (query === "" || item.fingerprint.includes(query) || item.eventFamily.toLowerCase().includes(query)),
    );
  }, [data, family, search]);
  const appPath = `/apps/${encodeURIComponent(session.scope.appId)}`;

  return (
    <div className="console-page">
      <PageHeader
        eyebrow="问题归类 / 稳定指纹"
        title="Issues"
        description={`按发生时身份聚合 ${filters.newRelease} 的 Java Crash 与 ANR，不展示原始异常内容`}
      />
      <div className="issues-toolbar">
        <label className="search-control"><Search size={16} /><span className="sr-only">搜索指纹</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索指纹或问题类型" /></label>
        <label><span className="sr-only">问题类型</span><select value={family} onChange={(event) => setFamily(event.target.value)}><option value="ALL">全部问题</option><option value="JAVA_CRASH">Java Crash</option><option value="ANR">ANR</option></select></label>
        <span>{formatDateTime(filters.fromMs)} — {formatDateTime(filters.toMs)}</span>
      </div>
      {data === null && error === null ? <LoadingPanel label="正在聚合 Issues…" /> : null}
      {error !== null ? <ErrorPanel message={error} onRetry={() => void load()} /> : null}
      {data !== null ? (
        <section className="console-panel issues-list-panel">
          <div className="issues-summary">
            <div><strong>{data.items.length}</strong><span>稳定指纹</span></div>
            <div><strong>{data.sampleCount}</strong><span>合格事故样本</span></div>
            <div><strong>{formatPercent(data.fingerprintCoverage)}</strong><span>指纹覆盖率</span></div>
            <StateBadge state={data.state} />
          </div>
          {items.length === 0 ? <EmptyPanel title="没有匹配的问题" message="调整问题类型或搜索条件；无结果不会被解释为健康零值。" /> : (
            <div className="console-table-wrap">
              <table className="console-table issue-table">
                <thead><tr><th>问题</th><th>事件数</th><th>影响安装</th><th>首次发生</th><th>最近发生</th><th><span className="sr-only">详情</span></th></tr></thead>
                <tbody>{items.map((item) => (
                  <tr key={item.fingerprint}>
                    <td><strong>{item.eventFamily}</strong><code>{shortId(item.fingerprint, 12)}</code></td>
                    <td>{item.eventCount}</td><td>{item.affectedInstallationCount}</td>
                    <td>{formatDateTime(item.firstSeenMs)}</td><td>{formatDateTime(item.lastSeenMs)}</td>
                    <td><Link to={`${appPath}/issues/${item.fingerprint}`} aria-label={`打开 ${item.eventFamily} Issue`}><ArrowRight size={16} /></Link></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </section>
      ) : null}
    </div>
  );
}
