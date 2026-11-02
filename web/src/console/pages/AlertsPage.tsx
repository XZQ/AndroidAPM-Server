import { useState } from "react";
import { useOutletContext } from "react-router-dom";
import { acknowledgeAlert, evaluateAlerts, getAlerts, saveAlert, silenceAlert } from "../../productApi";
import type { AlertInput, AlertRule } from "../../productTypes";
import type { ConsoleContextValue } from "../context";
import { ErrorPanel, LoadingPanel, PageHeader } from "../Primitives";
import { useProductRead } from "../useProductRead";

const emptyRule: AlertInput = { name: "", metric: "crash.count", comparison: "gt", threshold: 0, minSamples: 1, windowSeconds: 900, releaseVersion: null, channel: null, enabled: true };
const definition = (rule: AlertRule): AlertInput => ({ name: rule.name, metric: rule.metric, comparison: rule.comparison, threshold: rule.threshold, minSamples: rule.minSamples, windowSeconds: rule.windowSeconds, releaseVersion: rule.releaseVersion, channel: rule.channel, enabled: rule.enabled });

export function AlertsPage() {
  const { session } = useOutletContext<ConsoleContextValue>();
  const admin = session.role === "admin";
  const { data, error, actionError, busy, reload, run } = useProductRead(getAlerts);
  const [input, setInput] = useState<AlertInput>(emptyRule);
  const [editing, setEditing] = useState<number | undefined>();
  if (error) return <ErrorPanel message={error} onRetry={() => void reload()} />;
  if (!data) return <LoadingPanel label="正在读取告警规则与实例…" />;
  const editingActive = data.rules.find((rule) => rule.id === editing)?.activeInstanceId != null;
  return <div className="console-page">
    <PageHeader title="告警" description="独立持久化评估与通知；空遥测和低样本不会自动恢复已有实例。" actions={<><button onClick={() => void reload()} disabled={busy}>刷新</button>{admin && <button disabled={busy} onClick={() => void run(evaluateAlerts)}>立即评估</button>}</>} />
    {actionError && <ErrorPanel message={actionError} />}
    {!admin && <p>当前角色只读；规则、确认与静默需要管理员权限。</p>}
    {admin && <section className="console-panel"><h2>{editing === undefined ? "创建规则" : `编辑规则 #${editing}`}</h2>
      {editingActive && <p>活动实例未恢复前只能启用或停用规则。</p>}
      <form className="product-form" onSubmit={(event) => { event.preventDefault(); void run(() => saveAlert(input, editing), () => { setInput(emptyRule); setEditing(undefined); }); }}>
        <fieldset disabled={busy || editingActive}>
          <label>规则名称<input required maxLength={128} value={input.name} onChange={(event) => setInput({ ...input, name: event.target.value })} /></label>
          <label>指标<select value={input.metric} onChange={(event) => setInput({ ...input, metric: event.target.value })}>{data.metrics.map((item) => <option key={item.id} value={item.id}>{item.label} · {item.unit}</option>)}</select></label>
          <label>比较<select value={input.comparison} onChange={(event) => setInput({ ...input, comparison: event.target.value as AlertInput["comparison"] })}><option value="gt">大于</option><option value="lt">小于</option></select></label>
          <label>阈值<input type="number" required min={0} step="any" value={input.threshold} onChange={(event) => setInput({ ...input, threshold: Number(event.target.value) })} /></label>
          <label>最少样本<input type="number" required min={1} max={100000} value={input.minSamples} onChange={(event) => setInput({ ...input, minSamples: Number(event.target.value) })} /></label>
          <label>窗口秒数<input type="number" required min={60} max={86400} value={input.windowSeconds} onChange={(event) => setInput({ ...input, windowSeconds: Number(event.target.value) })} /></label>
          <label>限定版本（留空为全部）<input maxLength={256} value={input.releaseVersion ?? ""} onChange={(event) => setInput({ ...input, releaseVersion: event.target.value || null })} /></label>
          <label>通知渠道<select value={input.channel ?? ""} onChange={(event) => setInput({ ...input, channel: event.target.value || null })}><option value="">仅控制台</option>{data.channels.map((channel) => <option key={channel}>{channel}</option>)}</select></label>
        </fieldset>
        <label><input type="checkbox" checked={input.enabled} onChange={(event) => setInput({ ...input, enabled: event.target.checked })} disabled={busy} />启用规则</label>
        <button disabled={busy}>保存规则</button>{editing !== undefined && <button type="button" onClick={() => { setEditing(undefined); setInput(emptyRule); }}>取消编辑</button>}
      </form>
    </section>}
    <section className="console-panel"><h2>规则</h2>{data.rules.length === 0 && <p>当前范围没有规则。</p>}<div className="product-table"><table><thead><tr><th>名称 / 指标</th><th>状态</th><th>观测值 / 样本</th><th>静默截止</th><th>操作</th></tr></thead><tbody>{data.rules.map((rule) => <tr key={rule.id}><th>{rule.name}<small>{rule.metric} {rule.comparison} {rule.threshold}</small></th><td>{rule.enabled ? rule.state : "DISABLED"}</td><td>{rule.lastValue ?? "—"} / {rule.lastSampleCount}</td><td>{rule.silenceUntil ?? "—"}</td><td>{admin && <div className="product-actions"><button disabled={busy} onClick={() => { setEditing(rule.id); setInput(definition(rule)); }}>编辑</button><button disabled={busy} onClick={() => void run(() => saveAlert({ ...definition(rule), enabled: !rule.enabled }, rule.id))}>{rule.enabled ? "停用" : "启用"}</button><button disabled={busy} onClick={() => void run(() => silenceAlert(rule.id, 60))}>静默 1 小时</button><button disabled={busy || rule.silenceUntil === null} onClick={() => void run(() => silenceAlert(rule.id, 0))}>解除静默</button></div>}</td></tr>)}</tbody></table></div></section>
    <section className="console-panel"><h2>最近实例（最多 {data.historyLimit} 条）</h2><div className="product-table"><table><thead><tr><th>实例 / 规则</th><th>触发</th><th>恢复</th><th>确认</th></tr></thead><tbody>{data.instances.map((item) => <tr key={item.id}><td>#{item.id} / #{item.ruleId}</td><td>{item.triggeredAt} · {item.triggerValue}</td><td>{item.resolvedAt ?? "未恢复"}</td><td>{item.acknowledgedAt ?? (admin ? <button disabled={busy} onClick={() => void run(() => acknowledgeAlert(item.id))}>确认实例 #{item.id}</button> : "未确认")}</td></tr>)}</tbody></table></div></section>
    <section className="console-panel"><h2>通知送达</h2><div className="product-table"><table><thead><tr><th>实例</th><th>转换</th><th>渠道</th><th>状态</th><th>尝试</th><th>错误</th></tr></thead><tbody>{data.deliveries.map((item) => <tr key={item.id}><td>#{item.instanceId}</td><td>{item.transition}</td><td>{item.channel}</td><td>{item.status}</td><td>{item.attemptCount}</td><td>{item.lastError ?? "—"}</td></tr>)}</tbody></table></div></section>
  </div>;
}
