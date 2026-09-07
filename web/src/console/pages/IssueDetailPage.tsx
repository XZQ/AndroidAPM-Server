import { useCallback, useEffect, useState } from "react";
import { ArrowRight, Layers3 } from "lucide-react";
import { Link, useOutletContext, useParams } from "react-router-dom";

import { getEvents, getIssueDetail } from "../../api";
import { StateBadge } from "../../components/StateBadge";
import { formatDateTime, reasonLabel, shortId } from "../../format";
import type { EventPage, IssueDetail, IssueDistribution } from "../../types";
import type { ConsoleContextValue } from "../context";
import { EmptyPanel, ErrorPanel, LoadingPanel, PageHeader, SectionHeading } from "../Primitives";
import { TrendChart } from "../TrendChart";

type DistributionKey = "releases" | "scenes" | "deviceModels" | "androidVersions";

export function IssueDetailPage() {
  const context = useOutletContext<ConsoleContextValue>();
  const { filters, handleFailure, session } = context;
  const { fingerprint = "" } = useParams();
  const [detail, setDetail] = useState<IssueDetail | null>(null);
  const [events, setEvents] = useState<EventPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<DistributionKey>("releases");
  const load = useCallback(async () => {
    setError(null);
    try {
      const [issue, page] = await Promise.all([
        getIssueDetail(fingerprint, filters),
        session.role === "investigator" ? getEvents(filters, fingerprint) : Promise.resolve(null),
      ]);
      setDetail(issue);
      setEvents(page);
    } catch (caught) {
      setError(handleFailure(caught, "Issue 详情查询失败"));
    }
  }, [filters, fingerprint, handleFailure, session.role]);
  useEffect(() => { void load(); }, [load]);

  const appPath = `/apps/${encodeURIComponent(session.scope.appId)}`;
  if (detail === null && error === null) return <LoadingPanel label="正在加载 Issue 聚合与证据…" />;
  if (error !== null && detail === null) return <ErrorPanel message={error} onRetry={() => void load()} />;
  if (detail === null) return null;
  if (detail.state === "NO_DATA") {
    return <div className="console-page"><PageHeader title="Issue 不存在于当前范围" backTo={`${appPath}/issues`} /><EmptyPanel title="没有合格事实" message="该指纹在当前固定 scope 与时间窗口内不可见。" /></div>;
  }

  const distribution = detail[tab];
  return (
    <div className="console-page">
      <PageHeader
        eyebrow={`Issues / ${detail.eventFamily ?? "未知类型"} / ${shortId(detail.fingerprint, 8)}`}
        title={`${detail.eventFamily ?? "Issue"} · ${shortId(detail.fingerprint, 10)}`}
        description="稳定指纹聚合只展示 L0 事实；单事件原始证据需要调查员权限和访问理由"
        backTo={`${appPath}/issues`}
        actions={<StateBadge state={detail.state} />}
      />

      <section className="issue-summary-band">
        <SummaryFact label="事件数" value={detail.eventCount.toLocaleString("zh-CN")} />
        <SummaryFact label="影响安装" value={detail.affectedInstallationCount.toLocaleString("zh-CN")} />
        <SummaryFact label="首次发生" value={detail.firstSeenMs === null ? "—" : formatDateTime(detail.firstSeenMs)} />
        <SummaryFact label="最近发生" value={detail.lastSeenMs === null ? "—" : formatDateTime(detail.lastSeenMs)} />
        <SummaryFact label="Source" value="durable_inbox" />
      </section>

      <div className="issue-analysis-grid">
        <section className="console-panel trend-panel">
          <SectionHeading title="发生趋势" meta={<span>occurrence time</span>} />
          <TrendChart
            timestamps={detail.trend.map((point) => point.bucketStartMs)}
            series={[
              { label: "事件", color: "#52d9ba", values: detail.trend.map((point) => point.eventCount) },
              { label: "影响安装", color: "#ffb84d", values: detail.trend.map((point) => point.affectedInstallationCount) },
            ]}
            summary={`该 Issue 共 ${detail.eventCount} 个事件，影响 ${detail.affectedInstallationCount} 个安装`}
          />
        </section>
        <section className="console-panel distribution-panel">
          <div className="distribution-tabs" role="tablist" aria-label="Issue 分布维度">
            {([
              ["releases", "版本"], ["scenes", "场景"], ["deviceModels", "设备型号"], ["androidVersions", "Android 版本"],
            ] as const).map(([key, label]) => (
              <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>{label}</button>
            ))}
          </div>
          <DistributionView distribution={distribution} />
        </section>
      </div>

      <section className="console-panel issue-events-panel">
        <SectionHeading title={`样本事件 · ${filters.newRelease}`} meta={<span>最多显示当前页 25 条</span>} />
        {session.role !== "investigator" ? (
          <EmptyPanel title="需要调查员权限" message="viewer 可以查看 Issue 聚合，但不能枚举安装级事件。" />
        ) : events === null || events.items.length === 0 ? (
          <EmptyPanel title="当前版本没有可见样本" message="Issue 聚合可能来自时间窗内的其他版本；调整版本比较后重试。" />
        ) : (
          <div className="console-table-wrap">
            <table className="console-table">
              <thead><tr><th>发生时间</th><th>事件</th><th>场景</th><th>安装 HMAC</th><th>符号化</th><th><span className="sr-only">详情</span></th></tr></thead>
              <tbody>{events.items.map((event) => (
                <tr key={event.eventId}>
                  <td>{formatDateTime(event.occurrenceTimestampMs)}</td>
                  <td><strong>{event.eventFamily}</strong><code>{shortId(event.eventId, 9)}</code></td>
                  <td>{event.scene ?? "—"}</td>
                  <td><code>{shortId(event.installationHmac, 8)}</code></td>
                  <td>{event.symbolization?.status ?? "raw-only"}</td>
                  <td><Link to={`${appPath}/events/${encodeURIComponent(event.eventId)}`} aria-label={`查看事件 ${event.eventId}`}><ArrowRight size={16} /></Link></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function SummaryFact({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function DistributionView({ distribution }: { distribution: IssueDistribution }) {
  if (distribution.state === "UNKNOWN_COVERAGE" || distribution.state === "UNAVAILABLE") {
    return (
      <div className="distribution-unavailable">
        <Layers3 size={23} aria-hidden="true" />
        <StateBadge state={distribution.state} />
        <p>{reasonLabel(distribution.reason) ?? distribution.reason ?? "该维度当前不可用"}</p>
      </div>
    );
  }
  const max = Math.max(1, ...distribution.items.map((item) => item.eventCount));
  return (
    <div className="distribution-list">
      {distribution.items.map((item) => (
        <div key={item.label}>
          <span>{item.label}</span><strong>{item.eventCount} 事件</strong><small>{item.affectedInstallationCount} 安装</small>
          <i><b style={{ width: `${(item.eventCount / max) * 100}%` }} /></i>
        </div>
      ))}
    </div>
  );
}

