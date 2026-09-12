import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";

import { ApiFailure, logout, restoreSession } from "./api";
import { LoginPage } from "./components/LoginPage";
import { ConsoleLayout } from "./console/ConsoleLayout";
import { defaultFilters, type ConsoleContextValue } from "./console/context";
import { CapabilityPage } from "./console/pages/CapabilityPage";
import { EventDetailPage } from "./console/pages/EventDetailPage";
import { ExplorePage } from "./console/pages/ExplorePage";
import { IssueDetailPage } from "./console/pages/IssueDetailPage";
import { IssuesPage } from "./console/pages/IssuesPage";
import { OverviewPage } from "./console/pages/OverviewPage";
import { QualityPage } from "./console/pages/QualityPage";
import { ReleasesPage } from "./console/pages/ReleasesPage";
import { shortId } from "./format";
import type { QueryFilters, WebSession } from "./types";

const THEME_KEY = "androidapm-console-theme";

export default function App() {
  const [session, setSession] = useState<WebSession | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [loginMessage, setLoginMessage] = useState<string | null>(null);
  const [dark, setDarkState] = useState(() => localStorage.getItem(THEME_KEY) !== "light");
  const [filters, setFilters] = useState<QueryFilters>(defaultFilters);

  const setDark: Dispatch<SetStateAction<boolean>> = useCallback((next) => {
    setDarkState((current) => {
      const value = typeof next === "function" ? next(current) : next;
      localStorage.setItem(THEME_KEY, value ? "dark" : "light");
      return value;
    });
  }, []);

  useEffect(() => {
    let active = true;
    void restoreSession()
      .then((restored) => { if (active) setSession(restored); })
      .catch((caught: unknown) => {
        if (!active || !(caught instanceof ApiFailure) || caught.status === 401) return;
        setLoginMessage("无法恢复短时会话，请重新登录。");
      })
      .finally(() => { if (active) setSessionLoading(false); });
    return () => { active = false; };
  }, []);

  const expireSession = useCallback((message: string) => {
    setSession(null);
    setLoginMessage(message);
  }, []);

  const handleFailure = useCallback((caught: unknown, fallback: string): string => {
    if (caught instanceof ApiFailure) {
      if (caught.status === 401) expireSession("会话已过期或 Query Key 已撤销，请重新登录。");
      const request = caught.requestId === undefined ? "" : ` · request ${shortId(caught.requestId, 6)}`;
      return `${caught.message}${request}`;
    }
    return fallback;
  }, [expireSession]);

  const endSession = useCallback(async () => {
    try { await logout(); }
    catch (caught) {
      if (!(caught instanceof ApiFailure) || caught.status !== 401) {
        setLoginMessage("服务端退出请求未完成；本地视图已清除，请确认服务端会话状态。");
      }
    } finally {
      setSession(null);
    }
  }, []);

  const context = useMemo<ConsoleContextValue | null>(() => session === null ? null : ({
    session,
    filters,
    setFilters,
    dark,
    setDark,
    handleFailure,
    logout: endSession,
  }), [dark, endSession, filters, handleFailure, session, setDark]);

  if (sessionLoading) return <main className="boot-screen" data-theme={dark ? "dark" : "light"} role="status">正在恢复短时会话…</main>;
  if (context === null) {
    return <div data-theme={dark ? "dark" : "light"}><LoginPage initialMessage={loginMessage} onAuthenticated={(authenticated) => { setLoginMessage(null); setSession(authenticated); }} /></div>;
  }

  const home = `/apps/${encodeURIComponent(context.session.scope.appId)}/overview`;
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to={home} replace />} />
        <Route path="/apps/:appId" element={<ScopedConsole value={context} />}>
          <Route path="overview" element={<OverviewPage />} />
          <Route path="issues" element={<IssuesPage />} />
          <Route path="issues/:fingerprint" element={<IssueDetailPage />} />
          <Route path="explore" element={<ExplorePage />} />
          <Route path="events/:eventId" element={<EventDetailPage />} />
          <Route path="performance" element={<CapabilityPage kind="performance" />} />
          <Route path="releases" element={<ReleasesPage />} />
          <Route path="quality" element={<QualityPage />} />
          <Route path="alerts" element={<CapabilityPage kind="alerts" />} />
          <Route path="settings" element={<CapabilityPage kind="settings" />} />
          <Route index element={<Navigate to="overview" replace />} />
          <Route path="*" element={<Navigate to="overview" replace />} />
        </Route>
        <Route path="*" element={<Navigate to={home} replace />} />
      </Routes>
    </BrowserRouter>
  );
}

function ScopedConsole({ value }: { value: ConsoleContextValue }) {
  const { appId } = useParams();
  if (appId !== value.session.scope.appId) {
    return <Navigate to={`/apps/${encodeURIComponent(value.session.scope.appId)}/overview`} replace />;
  }
  return <ConsoleLayout value={value} />;
}
