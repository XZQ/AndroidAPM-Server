import { formatDateTime, formatPercent, shortId } from "../format";
import type { Fingerprints } from "../types";
import { StateBadge } from "./StateBadge";

interface FingerprintListProps {
  data: Fingerprints;
  selected: string | null;
  onSelect: (fingerprint: string | null) => void;
  canInvestigate: boolean;
}

export function FingerprintList({
  data,
  selected,
  onSelect,
  canInvestigate,
}: FingerprintListProps) {
  return (
    <section className="panel" aria-labelledby="fingerprints-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">INCIDENT CLUSTERS</p>
          <h2 id="fingerprints-heading">Top Crash / ANR 指纹</h2>
        </div>
        <div className="heading-status">
          <StateBadge state={data.state} />
          <span>覆盖 {formatPercent(data.fingerprintCoverage)}</span>
        </div>
      </div>
      {data.items.length === 0 ? (
        <div className="empty-state">
          <strong>{data.state === "ZERO" ? "查询成功，当前窗口没有事故" : "没有可展示的指纹"}</strong>
          <p>这不是模拟零值；请结合上方数据可信度和样本门禁判断。</p>
        </div>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">指纹</th>
                <th scope="col">事件族</th>
                <th scope="col">事件</th>
                <th scope="col">受影响安装</th>
                <th scope="col">首次 / 最近</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <tr key={`${item.fingerprint}:${item.eventFamily}`} data-selected={selected === item.fingerprint}>
                  <td>
                    <button
                      className="fingerprint-button"
                      type="button"
                      disabled={!canInvestigate}
                      title={canInvestigate ? item.fingerprint : "viewer 仅可查看 L0 聚合"}
                      onClick={() => onSelect(selected === item.fingerprint ? null : item.fingerprint)}
                    >
                      {shortId(item.fingerprint, 10)}
                    </button>
                  </td>
                  <td>{item.eventFamily}</td>
                  <td>{item.eventCount.toLocaleString("zh-CN")}</td>
                  <td>{item.affectedInstallationCount?.toLocaleString("zh-CN") ?? "不可用"}</td>
                  <td className="time-cell">
                    <span>{formatDateTime(item.firstSeenMs)}</span>
                    <span>{formatDateTime(item.lastSeenMs)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!canInvestigate && <p className="permission-note">当前是 viewer，会保留聚合结果但不会请求事件级证据。</p>}
    </section>
  );
}
