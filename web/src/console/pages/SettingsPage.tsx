import { useCallback, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { createKey, getAudit, getConfigs, getKeys, getSettingsState, publishConfig, revokeKey, updateDisplayName } from "../../productApi";
import type { KeyInput } from "../../productTypes";
import type { ConsoleContextValue } from "../context";
import { ErrorPanel, LoadingPanel, PageHeader } from "../Primitives";
import { useProductRead } from "../useProductRead";

export function SettingsPage() {
  const { session } = useOutletContext<ConsoleContextValue>();
  const admin = session.role === "admin";
  const read = useCallback(async () => {
    const [settings, keys, configs, audit] = await Promise.all([
      getSettingsState(), admin ? getKeys() : null, admin ? getConfigs() : null, admin ? getAudit() : null,
    ]);
    return { settings, keys, configs, audit };
  }, [admin]);
  const { data, error, actionError, busy, reload, run } = useProductRead(read);
  const [name, setName] = useState<string | null>(null);
  const [keyInput, setKeyInput] = useState<KeyInput>({ kind: "query", role: "viewer", description: "", expiresDays: 30 });
  const [secret, setSecret] = useState<{ id: string; secret: string } | null>(null);
  const [payload, setPayload] = useState("{}");
  const [expiresHours, setExpiresHours] = useState(24);
  const [rollout, setRollout] = useState(10000);
  const [notice, setNotice] = useState<string | null>(null);
  if (error) return <ErrorPanel message={error} onRetry={() => void reload()} />;
  if (!data) return <LoadingPanel label="正在读取当前范围的设置…" />;
  return <div className="console-page">
    <PageHeader title="设置" description="凭据固定应用与环境；只有独立配置的管理员可以更改凭据、配置和应用名称。" actions={<button disabled={busy} onClick={() => void reload()}>刷新</button>} />
    {actionError && <ErrorPanel message={actionError} />}{notice && <p role="status">{notice}</p>}
    <section className="console-panel"><h2>应用与有效运行参数</h2><p>{data.settings.displayName} · {session.scope.appId} · {session.scope.environment} · {session.role}</p>
      {admin && <form className="product-form" onSubmit={(event) => { event.preventDefault(); void run(() => updateDisplayName((name ?? data.settings.displayName).trim()), () => setName(null)); }}><label>应用名称<input required maxLength={128} value={name ?? data.settings.displayName} onChange={(event) => setName(event.target.value)} /></label><button disabled={busy}>保存名称</button></form>}
      <dl className="product-facts">{Object.entries(data.settings.operations).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl><p>运行参数来自服务端配置，修改部署配置并重启后生效。</p>
      <h3>当前范围管线</h3><dl className="product-facts">{Object.entries(data.settings.pipeline).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
      <h3>符号任务</h3><dl className="product-facts">{Object.entries(data.settings.symbolJobs).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl>
    </section>
    {!admin && <p>凭据生命周期、签名配置和管理审计需要管理员权限。</p>}
    {admin && data.keys && <section className="console-panel"><h2>凭据生命周期</h2><p>每类最多显示 {data.keys.limitPerKind} 条。新 secret 只在本次响应显示，切换页面后清除。</p>
      {secret && <div className="product-secret" role="status"><strong>请立即保存新凭据 {secret.id}</strong><pre>{secret.secret}</pre><button onClick={() => setSecret(null)}>隐藏凭据</button></div>}
      <form className="product-form" onSubmit={(event) => { event.preventDefault(); setSecret(null); void run(() => createKey(keyInput), setSecret); }}>
        <label>凭据类型<select value={keyInput.kind} onChange={(event) => setKeyInput({ ...keyInput, kind: event.target.value as KeyInput["kind"] })}><option value="query">Query</option><option value="ingest">Ingest</option><option value="ci">CI 制品</option></select></label>
        {keyInput.kind === "query" && <label>新凭据角色<select value={keyInput.role} onChange={(event) => setKeyInput({ ...keyInput, role: event.target.value as KeyInput["role"] })}><option value="viewer">查看者</option><option value="investigator">调查员</option><option value="admin">管理员</option></select></label>}
        <label>说明<input required maxLength={512} value={keyInput.description} onChange={(event) => setKeyInput({ ...keyInput, description: event.target.value })} /></label>
        <label>有效天数<input type="number" required min={1} max={365} value={keyInput.expiresDays} onChange={(event) => setKeyInput({ ...keyInput, expiresDays: Number(event.target.value) })} /></label>
        <button disabled={busy}>创建凭据</button>
      </form>
      <div className="product-table"><table><thead><tr><th>类型 / ID</th><th>说明 / 角色</th><th>有效状态</th><th>到期</th><th>操作</th></tr></thead><tbody>{data.keys.items.map((key) => <tr key={`${key.kind}/${key.id}`}><th>{key.kind}<small>{key.id}</small></th><td>{key.description} · {key.role ?? "—"}</td><td>{key.active ? "ACTIVE" : "REVOKED"}{key.current && " · 当前登录"}</td><td>{key.expiresAt ?? "未设置"}</td><td><button disabled={busy || !key.active || key.current} onClick={() => void run(() => revokeKey(key.kind, key.id))}>撤销 {key.id}</button></td></tr>)}</tbody></table></div>
    </section>}
    {admin && data.configs && <section className="console-panel"><h2>签名远程配置</h2><p>签名状态：{data.configs.signingConfigured ? "已配置" : "未配置"} · key ID：{data.configs.keyId}</p>{data.configs.publicKey && <p className="product-code">验证公钥：{data.configs.publicKey}</p>}
      <form className="product-form" onSubmit={(event) => { event.preventDefault(); void run(async () => { const parsed: unknown = JSON.parse(payload); if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("配置必须是 JSON 对象"); return publishConfig(parsed as Record<string, unknown>, expiresHours, rollout); }, (result) => setNotice(`已发布配置 revision ${result.revision}`)); }}>
        <label className="product-wide">配置 JSON<textarea aria-label="配置 JSON" required rows={6} value={payload} onChange={(event) => setPayload(event.target.value)} /></label>
        <label>有效小时<input type="number" required min={1} max={720} value={expiresHours} onChange={(event) => setExpiresHours(Number(event.target.value))} /></label>
        <label>灰度基点（10000 为全部）<input type="number" required min={0} max={10000} value={rollout} onChange={(event) => setRollout(Number(event.target.value))} /></label>
        <button disabled={busy || !data.configs.signingConfigured}>发布签名配置</button>
      </form><p>回滚使用所选历史正文，按上方有效期与灰度发布新的递增 revision。</p>
      <div className="product-table"><table><thead><tr><th>Revision</th><th>签发 / 到期</th><th>灰度</th><th>历史正文</th><th>操作</th></tr></thead><tbody>{data.configs.items.map((item) => <tr key={item.revision}><th>{item.revision}{item.active ? " · active" : ""}</th><td>{item.issuedAt}<small>{item.expiresAt}</small></td><td>{item.rolloutBasisPoints}/10000</td><td><details><summary>查看正文</summary><pre>{JSON.stringify(item.payload, null, 2)}</pre></details></td><td><button disabled={busy || !data.configs?.signingConfigured} onClick={() => void run(() => publishConfig({}, expiresHours, rollout, item.revision), (result) => setNotice(`已从 ${item.revision} 回滚发布新 revision ${result.revision}`))}>回滚到 {item.revision}</button></td></tr>)}</tbody></table></div>
    </section>}
    <section className="console-panel"><h2>制品元数据（最近 100 条）</h2><div className="product-table"><table><thead><tr><th>类型</th><th>版本 / 构建 / variant</th><th>ABI / build ID</th><th>状态 / SHA256</th></tr></thead><tbody>{data.settings.artifacts.map((item) => <tr key={item.id}><th>{item.type}</th><td>{item.versionCode} / {item.appBuild} / {item.variant}</td><td>{item.abi ?? "—"} / {item.buildId ?? "—"}</td><td>{item.status}<small>{item.sha256}</small></td></tr>)}</tbody></table></div></section>
    {admin && data.audit && <section className="console-panel"><h2>管理审计（最近 100 条）</h2><div className="product-table"><table><thead><tr><th>时间</th><th>操作</th><th>操作者</th><th>对象</th><th>结果</th></tr></thead><tbody>{data.audit.items.map((item) => <tr key={item.id}><td>{item.createdAt}</td><td>{item.action}</td><td>{item.actor}</td><td>{item.objectType} / {item.objectId ?? "—"}</td><td>{item.result}</td></tr>)}</tbody></table></div></section>}
  </div>;
}
