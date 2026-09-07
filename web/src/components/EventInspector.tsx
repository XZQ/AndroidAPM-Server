import { useState, type FormEvent } from "react";

import { formatDateTime, shortId } from "../format";
import type { EventMetadata, EventPage, RawEvent } from "../types";

interface EventInspectorProps {
  page: EventPage | null;
  fingerprint: string | null;
  loading: boolean;
  error: string | null;
  metadata: EventMetadata | null;
  metadataLoading: boolean;
  raw: RawEvent | null;
  rawLoading: boolean;
  rawError: string | null;
  onClearFingerprint: () => void;
  onSelectEvent: (eventId: string) => void;
  onCloseEvent: () => void;
  onLoadMore: () => void;
  onRequestRaw: (purposeCode: string, reason: string) => Promise<void>;
}

export function EventInspector({
  page,
  fingerprint,
  loading,
  error,
  metadata,
  metadataLoading,
  raw,
  rawLoading,
  rawError,
  onClearFingerprint,
  onSelectEvent,
  onCloseEvent,
  onLoadMore,
  onRequestRaw,
}: EventInspectorProps) {
  const [purpose, setPurpose] = useState("incident_diagnosis");
  const [reason, setReason] = useState("");

  async function submitRaw(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onRequestRaw(purpose, reason);
  }

  return (
    <section className="panel evidence-panel" aria-labelledby="events-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">AUDITED EVIDENCE</p>
          <h2 id="events-heading">事件证据</h2>
        </div>
        {fingerprint !== null && (
          <button className="ghost-button" type="button" onClick={onClearFingerprint}>
            清除指纹筛选
          </button>
        )}
      </div>
      {fingerprint !== null && (
        <p className="filter-chip" title={fingerprint}>
          指纹 · {shortId(fingerprint, 12)}
        </p>
      )}
      {error !== null && <InlineError message={error} />}
      {loading && page === null ? (
        <LoadingRows label="正在读取事件元数据" />
      ) : page?.items.length === 0 ? (
        <div className="empty-state">
          <strong>当前筛选没有事件级证据</strong>
          <p>聚合结果与 L1 事件列表使用相同固定 scope，但可能受指纹覆盖和筛选影响。</p>
        </div>
      ) : page !== null ? (
        <div className="event-layout">
          <div className="event-list" aria-label="事件列表">
            {page.items.map((event) => (
              <button
                className="event-row"
                data-active={metadata?.event.eventId === event.eventId}
                key={event.eventId}
                type="button"
                onClick={() => onSelectEvent(event.eventId)}
              >
                <span className="event-row-main">
                  <strong>{event.eventFamily}</strong>
                  <code title={event.eventId}>{shortId(event.eventId, 10)}</code>
                </span>
                <span>{event.module}/{event.name}</span>
                <span>{formatDateTime(event.occurrenceTimestampMs)}</span>
                <span className="event-row-status">{event.symbolization?.status ?? "raw-only"}</span>
              </button>
            ))}
            {page.nextCursor !== null && (
              <button className="secondary-button load-more" type="button" disabled={loading} onClick={onLoadMore}>
                {loading ? "读取中…" : "加载下一页"}
              </button>
            )}
          </div>
          {(metadataLoading || metadata !== null) && (
            <aside className="evidence-drawer" aria-label="事件详情">
              <button className="drawer-close" type="button" onClick={onCloseEvent} aria-label="关闭事件详情">
                ×
              </button>
              {metadataLoading ? (
                <LoadingRows label="正在读取 L1 元数据" />
              ) : metadata !== null ? (
                <>
                  <div className="drawer-title">
                    <p className="eyebrow">{metadata.event.eventFamily}</p>
                    <h3>{metadata.event.module}/{metadata.event.name}</h3>
                    <code>{metadata.event.eventId}</code>
                  </div>
                  <MetadataGrid metadata={metadata} />
                  <section className="raw-access" aria-labelledby="raw-heading">
                    <h4 id="raw-heading">L2 原始证据</h4>
                    <p>读取会先提交 purpose/reason 审计；响应禁止共享缓存。</p>
                    <form onSubmit={(event) => void submitRaw(event)}>
                      <label>
                        用途
                        <select value={purpose} onChange={(event) => setPurpose(event.target.value)}>
                          <option value="incident_diagnosis">事故诊断</option>
                          <option value="release_validation">发布验证</option>
                          <option value="customer_support">客户支持</option>
                          <option value="security_investigation">安全调查</option>
                        </select>
                      </label>
                      <label>
                        理由（至少 10 字符）
                        <textarea
                          value={reason}
                          minLength={10}
                          maxLength={512}
                          required
                          rows={3}
                          onChange={(event) => setReason(event.target.value)}
                          placeholder="说明为何需要查看这条原始事件"
                        />
                      </label>
                      <button className="secondary-button" type="submit" disabled={rawLoading || reason.length < 10}>
                        {rawLoading ? "审计并读取中…" : "审计后读取原始证据"}
                      </button>
                    </form>
                    {rawError !== null && <InlineError message={rawError} />}
                    {raw !== null && (
                      <div className="raw-result">
                        <h5>原始 payload</h5>
                        <pre>{JSON.stringify(raw.rawPayload, null, 2)}</pre>
                        {raw.symbolizedResult !== null && (
                          <>
                            <h5>符号化结果</h5>
                            <pre>{JSON.stringify(raw.symbolizedResult, null, 2)}</pre>
                          </>
                        )}
                      </div>
                    )}
                  </section>
                </>
              ) : null}
            </aside>
          )}
        </div>
      ) : null}
    </section>
  );
}

function MetadataGrid({ metadata }: { metadata: EventMetadata }) {
  const event = metadata.event;
  const rows: Array<[string, string]> = [
    ["发生时间", formatDateTime(event.occurrenceTimestampMs)],
    ["接收时间", formatDateTime(event.receivedAt)],
    ["版本", event.appVersion ?? "不可用"],
    ["构建", event.appBuild ?? "不可用"],
    ["版本身份", event.releaseIdentityQuality],
    ["安装身份", event.installationIdentityQuality],
    ["安装 HMAC", shortId(event.installationHmac, 10)],
    ["场景", event.scene ?? "不可用"],
    ["进程 / 线程", `${event.processName ?? "—"} / ${event.threadName ?? "—"}`],
    ["协议", `${metadata.protocol} · schema ${metadata.schemaVersion}`],
    ["Inbox", event.inboxStatus],
    ["符号状态", event.symbolization?.status ?? "raw-only"],
  ];
  return (
    <dl className="detail-grid">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd title={value}>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function InlineError({ message }: { message: string }) {
  return (
    <div className="inline-error" role="alert">
      <strong>请求失败</strong>
      <span>{message}</span>
    </div>
  );
}

function LoadingRows({ label }: { label: string }) {
  return (
    <div className="loading-block" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}
    </div>
  );
}
