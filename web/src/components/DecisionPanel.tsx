import { useState, type FormEvent } from "react";

import { formatDateTime, formatPercent } from "../format";
import type {
  QueryScope,
  ReleaseDecision,
  ReleaseDecisionValue,
  ReleaseHealth,
} from "../types";

interface DecisionPanelProps {
  scope: QueryScope;
  health: ReleaseHealth;
  decisions: ReleaseDecision[];
  submitting: boolean;
  error: string | null;
  onSubmit: (decision: ReleaseDecisionValue, reason: string) => Promise<void>;
}

const DECISION_LABELS: Record<ReleaseDecisionValue, string> = {
  continue: "继续灰度",
  pause: "暂停发布",
  rollback: "建议回滚",
};

export function DecisionPanel({
  scope,
  health,
  decisions,
  submitting,
  error,
  onSubmit,
}: DecisionPanelProps) {
  const [decision, setDecision] = useState<ReleaseDecisionValue>("pause");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onSubmit(decision, reason);
    setReason("");
    setConfirmed(false);
  }

  const release = health.newRelease;
  return (
    <section className="panel decision-panel" aria-labelledby="decision-heading">
      <div className="section-heading">
        <div>
          <p className="eyebrow">HUMAN GATE</p>
          <h2 id="decision-heading">人工发布决策</h2>
        </div>
        <span className="human-only">只记录，不自动操作发布系统</span>
      </div>
      <div className="decision-layout">
        <form className="decision-form" onSubmit={(event) => void submit(event)}>
          <div className="decision-options" role="radiogroup" aria-label="发布决策">
            {(Object.keys(DECISION_LABELS) as ReleaseDecisionValue[]).map((value) => (
              <label key={value} className={`decision-option option-${value}`}>
                <input
                  type="radio"
                  name="decision"
                  value={value}
                  checked={decision === value}
                  onChange={() => setDecision(value)}
                />
                <span>{DECISION_LABELS[value]}</span>
              </label>
            ))}
          </div>
          <label>
            判断理由（至少 10 字符）
            <textarea
              value={reason}
              minLength={10}
              maxLength={2000}
              required
              rows={4}
              onChange={(event) => setReason(event.target.value)}
              placeholder="记录样本、风险、业务窗口和下一步"
            />
          </label>
          <label className="confirm-row">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            <span>
              确认记录 {scope.appId} / {scope.environment} / {release.releaseVersion}，并理解服务端不会自动执行该动作。
            </span>
          </label>
          {error !== null && <div className="inline-error" role="alert">{error}</div>}
          <button
            className={`primary-button action-${decision}`}
            type="submit"
            disabled={submitting || !confirmed || reason.length < 10}
          >
            {submitting ? "正在提交审计记录…" : `记录“${DECISION_LABELS[decision]}”`}
          </button>
        </form>
        <div className="decision-evidence" aria-label="当前决策证据摘要">
          <h3>本次证据快照</h3>
          <dl>
            <div>
              <dt>发布状态</dt>
              <dd>{release.state}</dd>
            </div>
            <div>
              <dt>Java Crash</dt>
              <dd>{release.metrics.javaCrashEvents.value ?? "不可用"}</dd>
            </div>
            <div>
              <dt>ANR</dt>
              <dd>{release.metrics.anrEvents.value ?? "不可用"}</dd>
            </div>
            <div>
              <dt>受影响安装率</dt>
              <dd>{formatPercent(release.metrics.affectedInstallationRatio.value)}</dd>
            </div>
            <div>
              <dt>证据窗口</dt>
              <dd>{formatDateTime(health.window.fromMs)} → {formatDateTime(health.window.toMs)}</dd>
            </div>
          </dl>
        </div>
      </div>
      <div className="decision-history">
        <h3>最近记录</h3>
        {decisions.length === 0 ? (
          <p className="muted">还没有人工决策；这不等于默认继续发布。</p>
        ) : (
          <ol>
            {decisions.map((item) => (
              <li key={item.id}>
                <span className={`decision-mark mark-${item.decision}`}>
                  {DECISION_LABELS[item.decision]}
                </span>
                <div>
                  <strong>{item.reason}</strong>
                  <small>{formatDateTime(item.createdAt)} · {item.actor}</small>
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
