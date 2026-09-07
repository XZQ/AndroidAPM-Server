import { useCallback, useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";

import { createReleaseDecision, getReleaseDecisions, getReleaseHealth } from "../../api";
import { DecisionPanel } from "../../components/DecisionPanel";
import { StateBadge } from "../../components/StateBadge";
import { formatDateTime } from "../../format";
import type { ReleaseDecision, ReleaseDecisionInput, ReleaseDecisionValue, ReleaseHealth } from "../../types";
import type { ConsoleContextValue } from "../context";
import { EmptyPanel, ErrorPanel, LoadingPanel, PageHeader } from "../Primitives";

export function ReleasesPage() {
  const { filters, session, handleFailure } = useOutletContext<ConsoleContextValue>();
  const [health, setHealth] = useState<ReleaseHealth | null>(null);
  const [decisions, setDecisions] = useState<ReleaseDecision[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decisionError, setDecisionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const result = await getReleaseHealth(filters);
      setHealth(result);
      if (session.role === "investigator") setDecisions((await getReleaseDecisions(filters.newRelease)).items);
    } catch (caught) { setError(handleFailure(caught, "发布证据查询失败")); }
    finally { setLoading(false); }
  }, [filters, handleFailure, session.role]);
  useEffect(() => { void load(); }, [load]);

  async function submit(decision: ReleaseDecisionValue, reason: string) {
    if (health === null) return;
    setSubmitting(true); setDecisionError(null);
    const release = health.newRelease;
    const input: ReleaseDecisionInput = {
      releaseVersion: release.releaseVersion,
      decision,
      evidenceFromMs: health.window.fromMs,
      evidenceToMs: health.window.toMs,
      reason,
      evidence: {
        releaseState: release.state,
        baselineState: health.baselineRelease.state,
        javaCrashEvents: release.metrics.javaCrashEvents.value,
        anrEvents: release.metrics.anrEvents.value,
        affectedInstallations: release.metrics.affectedInstallations.value,
        activeInstallations: release.metrics.activeInstallations.value,
        affectedInstallationRatio: release.metrics.affectedInstallationRatio.value,
        dataQualityState: release.state,
        queryRequestId: health.requestId,
      },
    };
    try {
      const created = await createReleaseDecision(input);
      setDecisions((current) => [created, ...current]);
    } catch (caught) { setDecisionError(handleFailure(caught, "发布决策记录失败")); }
    finally { setSubmitting(false); }
  }

  if (loading && health === null) return <LoadingPanel label="正在读取版本证据…" />;
  if (error !== null && health === null) return <ErrorPanel message={error} onRetry={() => void load()} />;
  if (health === null) return null;
  return <div className="console-page"><PageHeader eyebrow="Release Health / 人工门禁" title="版本发布" description={`${filters.newRelease} 对比 ${filters.baselineRelease} · ${formatDateTime(filters.fromMs)} — ${formatDateTime(filters.toMs)}`} actions={<StateBadge state={health.newRelease.state} />} />
    <section className="release-status-band"><div><span>Java Crash</span><strong>{health.newRelease.metrics.javaCrashEvents.value ?? "—"}</strong></div><div><span>ANR</span><strong>{health.newRelease.metrics.anrEvents.value ?? "—"}</strong></div><div><span>影响安装</span><strong>{health.newRelease.metrics.affectedInstallations.value ?? "—"}</strong></div><div><span>自动发布动作</span><strong>未接入</strong></div></section>
    {session.role === "investigator" ? <DecisionPanel scope={session.scope} health={health} decisions={decisions} submitting={submitting} error={decisionError} onSubmit={submit} /> : <EmptyPanel title="查看者只读" message="你可以查看版本证据，但记录人工继续、暂停或回滚建议需要 investigator 权限。" />}
  </div>;
}
