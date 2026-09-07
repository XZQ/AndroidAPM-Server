import { useState, type FormEvent } from "react";

import { ApiFailure, login } from "../api";
import type { WebSession } from "../types";

interface LoginPageProps {
  onAuthenticated: (session: WebSession) => void;
  initialMessage?: string | null;
}

export function LoginPage({ onAuthenticated, initialMessage = null }: LoginPageProps) {
  const [queryKey, setQueryKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(initialMessage);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const session = await login(queryKey.trim());
      setQueryKey("");
      onAuthenticated(session);
    } catch (caught) {
      setError(caught instanceof ApiFailure ? caught.message : "无法连接本地 AndroidAPM 服务端");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-brand" aria-label="AndroidAPM 控制台介绍">
        <div className="brand-mark" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <p className="eyebrow">ANDROID RELEASE INTELLIGENCE</p>
        <h1>把遥测变成<br />可审计的发布判断。</h1>
        <p>
          AndroidAPM SDK 负责可靠采集；这里用发生时身份、安装覆盖和原始证据判断新版本 Crash / ANR 风险。
        </p>
        <ul className="login-principles">
          <li><span>01</span> 真零值与无数据严格分开</li>
          <li><span>02</span> tenant / app / environment 由凭据固定</li>
          <li><span>03</span> 原始证据按用途审计后读取</li>
        </ul>
      </section>
      <section className="login-card" aria-labelledby="login-title">
        <div>
          <p className="eyebrow">CONTROL ROOM ACCESS</p>
          <h2 id="login-title">进入诊断控制台</h2>
          <p>粘贴一次性显示的固定 scope 查询凭据。</p>
        </div>
        <form onSubmit={(event) => void submit(event)}>
          <label>
            Query Key
            <input
              type="password"
              name="androidapm-query-key"
              autoComplete="off"
              spellCheck={false}
              value={queryKey}
              required
              placeholder="apmq1_…"
              onChange={(event) => setQueryKey(event.target.value)}
            />
          </label>
          <div className="credential-note">
            <span aria-hidden="true">◆</span>
            长期 Key 只用于本次 HTTPS / loopback 登录请求，不写入 localStorage、sessionStorage 或 URL。
          </div>
          {error !== null && (
            <div className="login-error" role="alert">
              <strong>登录失败</strong>
              <span>{error}</span>
            </div>
          )}
          <button className="primary-button login-button" type="submit" disabled={submitting || queryKey.trim() === ""}>
            {submitting ? "正在建立短时会话…" : "建立短时会话"}
          </button>
        </form>
        <footer>
          登录后使用 HttpOnly / SameSite 短时 Cookie；Query Key 被撤销后会话即时失效。
        </footer>
      </section>
    </main>
  );
}
