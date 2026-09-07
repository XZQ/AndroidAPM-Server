import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Braces, FileLock2, ShieldCheck } from "lucide-react";
import { useOutletContext, useParams } from "react-router-dom";

import { getEventMetadata, getRawEvent } from "../../api";
import { formatDateTime, shortId } from "../../format";
import type { EventMetadata, RawEvent } from "../../types";
import type { ConsoleContextValue } from "../context";
import { useRequestGuard } from "../useRequestGuard";
import { EmptyPanel, ErrorPanel, LoadingPanel, PageHeader, SectionHeading } from "../Primitives";

export function EventDetailPage() {
  const context = useOutletContext<ConsoleContextValue>();
  const { session, handleFailure } = context;
  const { eventId = "" } = useParams();
  const [metadata, setMetadata] = useState<EventMetadata | null>(null);
  const [raw, setRaw] = useState<RawEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [rawLoading, setRawLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rawError, setRawError] = useState<string | null>(null);
  const [purpose, setPurpose] = useState("incident_diagnosis");
  const [reason, setReason] = useState("");
  const begin = useRequestGuard();
  const beginRaw = useRequestGuard();

  const load = useCallback(async () => {
    if (session.role !== "investigator") return;
    const current = begin();
    beginRaw();
    setMetadata(null); setRaw(null); setRawError(null); setRawLoading(false);
    setLoading(true);
    setError(null);
    try {
      const result = await getEventMetadata(eventId);
      if (current()) setMetadata(result);
    } catch (caught) {
      if (current()) setError(handleFailure(caught, "事件元数据查询失败"));
    } finally {
      if (current()) setLoading(false);
    }
  }, [begin, beginRaw, eventId, handleFailure, session.role]);

  useEffect(() => { void load(); }, [load]);

  async function requestRaw(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const current = beginRaw();
    setRaw(null);
    setRawLoading(true);
    setRawError(null);
    try {
      const result = await getRawEvent(eventId, purpose, reason.trim());
      if (current()) setRaw(result);
    } catch (caught) {
      if (current()) setRawError(handleFailure(caught, "原始证据读取失败"));
    } finally {
      if (current()) setRawLoading(false);
    }
  }

  const appPath = `/apps/${encodeURIComponent(session.scope.appId)}`;
  if (session.role !== "investigator") {
    return (
      <div className="console-page">
        <PageHeader title="事件详情" description="单事件元数据和原始证据属于 L1/L2 数据" backTo={`${appPath}/issues`} />
        <EmptyPanel title="需要调查员权限" message="viewer 只能读取 L0 聚合，服务端也会拒绝事件级请求。" />
      </div>
    );
  }
  if (loading && metadata === null) return <LoadingPanel label="正在读取 L1 事件元数据…" />;
  if (error !== null && metadata === null) return <ErrorPanel message={error} onRetry={() => void load()} />;
  if (metadata === null) return null;

  const item = metadata.event;
  const facts: Array<[string, string]> = [
    ["事件 ID", item.eventId],
    ["发生时间", formatDateTime(item.occurrenceTimestampMs)],
    ["接收时间", formatDateTime(item.receivedAt)],
    ["模块 / 事件", `${item.module}/${item.name}`],
    ["版本 / 构建", `${item.appVersion ?? "—"} / ${item.appBuild ?? "—"}`],
    ["版本身份", item.releaseIdentityQuality],
    ["安装身份", item.installationIdentityQuality],
    ["安装 HMAC", shortId(item.installationHmac, 12)],
    ["场景", item.scene ?? "不可用"],
    ["进程 / 线程", `${item.processName ?? "—"} / ${item.threadName ?? "—"}`],
    ["协议", `${metadata.protocol} · schema ${metadata.schemaVersion}`],
    ["Inbox / 符号化", `${item.inboxStatus} / ${item.symbolization?.status ?? "raw-only"}`],
  ];

  return (
    <div className="console-page">
      <PageHeader
        eyebrow={`事件证据 / ${item.eventFamily}`}
        title={`${item.module}/${item.name}`}
        description={`L1 allow-list 元数据 · ${shortId(item.eventId, 12)}`}
        backTo={item.incidentFingerprint === null ? `${appPath}/explore` : `${appPath}/issues/${encodeURIComponent(item.incidentFingerprint)}`}
      />

      <section className="console-panel event-metadata-panel">
        <SectionHeading title="事件元数据" meta={<span>L1 · 不含任意 payload</span>} />
        <dl className="event-fact-grid">
          {facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd title={value}>{value}</dd></div>)}
        </dl>
        <div className="field-state-strip">
          <strong>字段状态</strong>
          {Object.entries(metadata.fieldStates).map(([name, state]) => <span key={name}>{name}: {state}</span>)}
        </div>
      </section>

      <section className="console-panel raw-evidence-panel">
        {metadata.rawAvailable === false ? <p>原始证据已按保留策略清理；事件身份和去重记录仍保留。</p> : null}
        <div className="raw-access-copy">
          <FileLock2 size={25} aria-hidden="true" />
          <div>
            <SectionHeading title="L2 原始证据" meta={<span>显式审计读取</span>} />
            <p>页面不会自动加载原始 payload。提交用途和理由后，服务端先持久化审计记录，再返回 no-store 响应。</p>
          </div>
        </div>
        <form className="raw-access-form" onSubmit={(event) => void requestRaw(event)}>
          <label>用途<select value={purpose} onChange={(event) => setPurpose(event.target.value)}><option value="incident_diagnosis">事故诊断</option><option value="release_validation">发布验证</option><option value="customer_support">客户支持</option><option value="security_investigation">安全调查</option></select></label>
          <label>读取理由（至少 10 字符）<textarea rows={3} minLength={10} maxLength={512} required value={reason} onChange={(event) => setReason(event.target.value)} placeholder="说明为何必须查看这一条原始事件" /></label>
          <button className="console-button primary" type="submit" disabled={metadata.rawAvailable === false || rawLoading || reason.trim().length < 10}><ShieldCheck size={15} />{rawLoading ? "审计并读取中…" : "审计后读取"}</button>
        </form>
        {rawError !== null ? <ErrorPanel message={rawError} /> : null}
        {raw !== null ? (
          <div className="evidence-columns">
            <EvidenceBlock title="原始 payload" value={raw.rawPayload} />
            <EvidenceBlock title="符号化结果" value={raw.symbolizedResult} empty="当前没有可用符号化结果；raw-only 不等于成功符号化。" />
          </div>
        ) : null}
      </section>
    </div>
  );
}

function EvidenceBlock({ title, value, empty }: { title: string; value: Record<string, unknown> | null; empty?: string }) {
  return (
    <section className="evidence-block">
      <h3><Braces size={16} aria-hidden="true" />{title}</h3>
      {value === null ? <p>{empty ?? "不可用"}</p> : <pre>{JSON.stringify(value, null, 2)}</pre>}
    </section>
  );
}
