import { useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  BellRing,
  Bug,
  ChevronDown,
  DatabaseZap,
  Gauge,
  LogOut,
  Menu,
  Moon,
  Search,
  Settings,
  ShieldCheck,
  Sun,
  X,
} from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

import { formatDateTime } from "../format";
import type { ConsoleContextValue } from "./context";

export function ConsoleLayout({ value }: { value: ConsoleContextValue }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const { session, filters, setFilters, dark, setDark } = value;
  const appPath = `/apps/${encodeURIComponent(session.scope.appId)}`;
  const nav = [
    { to: `${appPath}/overview`, label: "总览", icon: BarChart3 },
    { to: `${appPath}/issues`, label: "Issues", icon: Bug },
    { to: `${appPath}/explore`, label: "事件探索", icon: Search, investigator: true },
    { to: `${appPath}/performance`, label: "性能分析", icon: Gauge },
    { to: `${appPath}/releases`, label: "版本发布", icon: Activity },
    { to: `${appPath}/quality`, label: "数据质量", icon: ShieldCheck },
    { to: `${appPath}/alerts`, label: "告警", icon: BellRing },
    { to: `${appPath}/settings`, label: "设置", icon: Settings },
  ];

  function setRange(hours: number) {
    const toMs = Date.now();
    setFilters((current) => ({ ...current, fromMs: toMs - hours * 60 * 60 * 1_000, toMs }));
  }

  const rangeHours = Math.round((filters.toMs - filters.fromMs) / 3_600_000);
  return (
    <div className="console-shell" data-theme={dark ? "dark" : "light"}>
      <aside className={`console-sidebar ${menuOpen ? "open" : ""}`}>
        <div className="console-brand">
          <div className="brand-mark small" aria-hidden="true"><span /><span /><span /></div>
          <div><strong>AndroidAPM</strong><span>Cloud Console</span></div>
          <button className="mobile-close" type="button" onClick={() => setMenuOpen(false)} aria-label="关闭导航"><X size={18} /></button>
        </div>
        <nav aria-label="主导航">
          {nav.filter((item) => item.investigator !== true || session.role === "investigator").map((item) => {
            const Icon = item.icon;
            const unavailable = item.investigator === true && session.role !== "investigator";
            return (
              <NavLink
                key={item.to}
                to={item.to}
                aria-disabled={unavailable}
                onClick={(event) => {
                  if (unavailable) event.preventDefault();
                  else setMenuOpen(false);
                }}
              >
                <Icon size={17} aria-hidden="true" />
                <span>{item.label}</span>
                {unavailable ? <small>需调查员</small> : null}
              </NavLink>
            );
          })}
        </nav>
        <div className="console-sidebar-status">
          <DatabaseZap size={16} aria-hidden="true" />
          <div><strong>durable inbox</strong><span>scope 由服务端强制</span></div>
        </div>
      </aside>

      <div className="console-workspace">
        <header className="console-topbar">
          <button className="mobile-menu" type="button" onClick={() => setMenuOpen(true)} aria-label="打开导航"><Menu size={19} /></button>
          <button className="scope-selector" type="button" title="应用范围由 Query 凭据固定">
            <span className="scope-signal" />
            <span><strong>{session.scope.appId}</strong><small>{session.scope.environment}</small></span>
            <ChevronDown size={15} aria-hidden="true" />
          </button>
          <div className="console-top-actions">
            <label className="range-select">
              <span className="sr-only">查询时间范围</span>
              <select value={rangeHours} onChange={(event) => setRange(Number(event.target.value))}>
                {[1, 6, 24, 168].includes(rangeHours) ? null : <option value={rangeHours}>自定义时间窗</option>}
                <option value={1}>最近 1 小时</option>
                <option value={6}>最近 6 小时</option>
                <option value={24}>最近 24 小时</option>
                <option value={168}>最近 7 天</option>
              </select>
            </label>
            <span className="session-chip" title={`会话到期：${formatDateTime(session.expiresAtMs)}`}>
              {session.role === "investigator" ? "调查员" : "查看者"}
            </span>
            <button className="console-icon-button" type="button" onClick={() => setDark((current) => !current)} aria-label="切换明暗主题">
              {dark ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <button className="console-icon-button" type="button" onClick={() => void value.logout()} aria-label="退出短时会话"><LogOut size={17} /></button>
          </div>
        </header>
        <main className="console-content">
          <Outlet context={value} />
        </main>
        <footer className="console-footer">
          <span><ShieldCheck size={13} /> fixed tenant/app/environment</span>
          <span><AlertTriangle size={13} /> synthetic preview 不能代表生产部署</span>
        </footer>
      </div>
    </div>
  );
}
