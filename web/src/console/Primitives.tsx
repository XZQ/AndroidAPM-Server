import type { ReactNode } from "react";
import { AlertCircle, ArrowLeft, LoaderCircle } from "lucide-react";
import { Link } from "react-router-dom";

import { StateBadge } from "../components/StateBadge";
import { formatMetric, formatPercent, reasonLabel } from "../format";
import type { MetricResult } from "../types";

export function PageHeader({
  eyebrow,
  title,
  description,
  backTo,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  backTo?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="console-page-header">
      <div className="console-title-wrap">
        {backTo !== undefined ? (
          <Link className="console-back" to={backTo} aria-label="返回上一层">
            <ArrowLeft size={16} aria-hidden="true" />
          </Link>
        ) : null}
        <div>
          {eyebrow !== undefined ? <p className="console-eyebrow">{eyebrow}</p> : null}
          <h1>{title}</h1>
          {description !== undefined ? <p>{description}</p> : null}
        </div>
      </div>
      {actions !== undefined ? <div className="console-page-actions">{actions}</div> : null}
    </header>
  );
}

export function LoadingPanel({ label }: { label: string }) {
  return (
    <section className="console-state-panel" role="status">
      <LoaderCircle className="spin" size={20} aria-hidden="true" />
      <span>{label}</span>
    </section>
  );
}

export function ErrorPanel({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <section className="console-state-panel error" role="alert">
      <AlertCircle size={20} aria-hidden="true" />
      <div>
        <strong>当前结果不可用</strong>
        <p>{message}</p>
      </div>
      {onRetry !== undefined ? <button type="button" onClick={onRetry}>重试</button> : null}
    </section>
  );
}

export function EmptyPanel({ title, message }: { title: string; message: string }) {
  return (
    <section className="console-state-panel empty">
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
      </div>
    </section>
  );
}

export function MetricTile({
  label,
  metric,
  kind = "count",
  delta,
}: {
  label: string;
  metric: MetricResult;
  kind?: "count" | "ratio";
  delta?: number | null;
}) {
  const shownDelta = delta == null
    ? null
    : kind === "ratio"
      ? formatPercent(delta)
      : `${delta > 0 ? "+" : ""}${delta.toLocaleString("zh-CN")}`;
  return (
    <article className="console-metric">
      <div>
        <span>{label}</span>
        <StateBadge state={metric.state} />
      </div>
      <strong>{formatMetric(metric, kind)}</strong>
      <small>
        {shownDelta !== null ? `较基线 ${shownDelta}` : `样本 ${metric.sampleCount.toLocaleString("zh-CN")}`}
      </small>
      {metric.reason !== null ? <p>{reasonLabel(metric.reason)}</p> : null}
    </article>
  );
}

export function SectionHeading({ title, meta }: { title: string; meta?: ReactNode }) {
  return (
    <div className="console-section-heading">
      <h2>{title}</h2>
      {meta !== undefined ? <div>{meta}</div> : null}
    </div>
  );
}
