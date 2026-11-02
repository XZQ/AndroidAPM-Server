import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "../App";

const scope = { tenantId: "tenant", appId: "com.example", environment: "test" };
const observation = { state: "UNAVAILABLE", sampleCount: 0, eventCount: 1, missingCount: 1, invalidCount: 0, unavailableCount: 0, coverage: 0, mean: null, minimum: null, maximum: null, p50: null, p75: null, p95: null };
const performance = {
  requestId: "performance", newRelease: "2.0.0", baselineRelease: "1.0.0", fromMs: 1, toMs: 2,
  metrics: [{ id: "launch.cold_start.launchDurationMs", module: "launch", name: "cold_start", field: "launchDurationMs", label: "冷启动耗时", unit: "ms", current: observation, baseline: { ...observation, state: "ZERO", sampleCount: 1, missingCount: 0, p95: 0 }, p95Delta: null }],
  trendMetric: "launch.cold_start.launchDurationMs", trend: [], lateCount: 0, nonOccurrenceCount: 1,
  networkErrorRatio: null, jankRatio: null, eventStatistics: [], dimensions: [],
  weightedFps: { state: "UNAVAILABLE", value: null, sampleCount: 0, eventCount: 0, coverage: null },
  baselineWeightedFps: { state: "UNAVAILABLE", value: null },
};
const rule = { id: 1, name: "Crash threshold", metric: "crash.count", comparison: "gt", threshold: 0, minSamples: 1, windowSeconds: 900, releaseVersion: null, channel: null, enabled: true, state: "FIRING", lastValue: 1, lastSampleCount: 2, activeInstanceId: 1, evaluatedAt: null, silenceUntil: null };
const alerts = { rules: [rule], metrics: [{ id: "crash.count", label: "Java Crash", unit: "events" }], channels: [], historyLimit: 100, instances: [{ id: 1, ruleId: 1, triggeredAt: "2026-11-02T00:00:00Z", resolvedAt: null, acknowledgedAt: null, triggerValue: 1 }], deliveries: [] };
const settings = { displayName: "Local app", role: "admin", pipeline: { pending: 2 }, symbolJobs: {}, operations: { exportEnabled: false }, artifacts: [] };
const summary = { traceId: "1".repeat(32), spanCount: 1, errorSpanCount: 0, durationMs: 100, firstSeenMs: 1000, lastSeenMs: 1100, timingAvailableCount: 1, orphanCount: 0, invalidRelationCount: 0, lateCount: 0, coverageState: "UNKNOWN_COMPLETENESS", state: "PRESENT" };
const posts: { path: string; body: Record<string, unknown>; csrf: string | null }[] = [];

beforeEach(() => {
  vi.restoreAllMocks(); localStorage.clear(); sessionStorage.clear(); posts.length = 0;
  document.cookie = "androidapm_csrf=csrf-test; path=/";
});

function setup(page: string, role = "admin", extra?: (path: string, init?: RequestInit) => Promise<Response> | undefined) {
  window.history.pushState({}, "", `/apps/com.example/${page}`);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const override = extra?.(path, init);
    if (override) return override;
    if (init?.method && init.method !== "GET") {
      const body = JSON.parse(typeof init.body === "string" ? init.body : "{}") as Record<string, unknown>;
      posts.push({ path, body, csrf: new Headers(init.headers).get("X-Apm-Csrf-Token") });
      return json(path === "/v1/manage/keys" ? { id: "new-key", secret: "apmq1_ephemeral-secret" } : path === "/v1/manage/configs" ? { revision: 2 } : {});
    }
    if (path === "/v1/web/session") return json({ requestId: "session", scope, role, expiresAtMs: Date.now() + 60000 });
    if (path.startsWith("/v1/query/performance?")) return json(performance);
    if (path === "/v1/alerts") return json(alerts);
    if (path === "/v1/manage/settings") return json(settings);
    if (path === "/v1/manage/keys") return json({ items: [{ kind: "query", id: "current", description: "current admin", role: "admin", active: true, current: true }], limitPerKind: 200 });
    if (path === "/v1/manage/configs") return json({ signingConfigured: true, publicKey: "public-only", keyId: "test-key", items: [{ revision: 1, payload: { enabled: false }, rolloutBasisPoints: 10000, active: true }] });
    if (path === "/v1/manage/audit") return json({ items: [] });
    if (path.startsWith("/v1/query/events?")) return json({ requestId: "events", scope, items: [], nextCursor: null });
    if (path.startsWith("/v1/query/traces/")) return json({ summary, spans: [{ eventId: "span-event", traceId: summary.traceId, spanId: "2".repeat(16), parentSpanId: null, name: "checkout", status: "OK", durationMs: 100, startMs: 1000, endMs: 1100, timingState: "AVAILABLE", relationState: "ROOT", customAttributeCount: 1, occurrenceTimestampMs: 1100, late: false }] });
    if (path.startsWith("/v1/query/traces?")) return json({ state: "PRESENT", sampleCount: 1, invalidSpanCount: 0, expiredSpanCount: 0, traceCount: 1, durationSampleCount: 1, meanSpanDurationMs: 100, p95SpanDurationMs: 100, items: [summary], truncated: false });
    return json({ message: `unexpected ${path}` }, 404);
  });
  return render(<App />);
}

it("renders performance observations and distinguishes unavailable from observed zero", async () => {
  setup("performance", "viewer");
  await screen.findByRole("heading", { name: "性能分析" });
  expect(screen.getByText("冷启动耗时", { selector: "th" })).toBeInTheDocument();
  expect(document.querySelector('[data-state="ZERO"]')).toBeInTheDocument();
  expect(document.querySelector('[data-state="UNAVAILABLE"]')).toBeInTheDocument();
  expect(globalThis.fetch).toHaveBeenCalledWith(expect.stringContaining("/v1/query/performance?"), expect.anything());
});

it("lets an admin investigate events while a viewer never enumerates L1", async () => {
  const view = setup("explore");
  await screen.findByRole("heading", { name: "事件探索" });
  expect(screen.getByText("管理员")).toBeInTheDocument();
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith(expect.stringContaining("/v1/query/events?"), expect.anything()));
  view.unmount();
  setup("explore", "viewer");
  await screen.findByText("需要调查员权限");
  expect(globalThis.fetch).not.toHaveBeenCalledWith(expect.stringContaining("/v1/query/events?"), expect.anything());
});

it("creates rules and submits acknowledge, silence, toggle and evaluation with CSRF", async () => {
  setup("alerts");
  await screen.findByRole("heading", { name: "告警" });
  fireEvent.change(screen.getByLabelText("规则名称"), { target: { value: "New rule" } });
  fireEvent.click(screen.getByRole("button", { name: "保存规则" }));
  await waitFor(() => expect(posts.some((item) => item.path === "/v1/alerts/rules" && item.body.name === "New rule")).toBe(true));
  for (const label of ["确认实例 #1", "静默 1 小时", "停用", "立即评估"]) {
    fireEvent.click(await screen.findByRole("button", { name: label }));
    await waitFor(() => expect(screen.getByRole("button", { name: "保存规则" })).not.toBeDisabled());
  }
  expect(posts.map((item) => item.path)).toEqual(expect.arrayContaining(["/v1/alerts/instances/1/acknowledge", "/v1/alerts/rules/1/silence", "/v1/alerts/rules/1", "/v1/alerts/evaluate"]));
  expect(posts.every((item) => item.csrf === "csrf-test")).toBe(true);
});

it("keeps viewer alerts and settings read-only without requesting admin resources", async () => {
  const view = setup("alerts", "viewer");
  await screen.findByRole("heading", { name: "告警" });
  expect(screen.queryByRole("button", { name: "保存规则" })).not.toBeInTheDocument();
  view.unmount();
  setup("settings", "viewer");
  await screen.findByRole("heading", { name: "设置" });
  expect(globalThis.fetch).not.toHaveBeenCalledWith("/v1/manage/keys", expect.anything());
  expect(screen.queryByRole("button", { name: "创建凭据" })).not.toBeInTheDocument();
});

it("shows a new secret once without storing it and publishes rollback as a new revision", async () => {
  setup("settings");
  await screen.findByRole("heading", { name: "设置" });
  fireEvent.change(screen.getByLabelText("说明"), { target: { value: "temporary viewer" } });
  fireEvent.click(screen.getByRole("button", { name: "创建凭据" }));
  await screen.findByText("apmq1_ephemeral-secret");
  expect(JSON.stringify(localStorage)).not.toContain("ephemeral-secret");
  expect(JSON.stringify(sessionStorage)).not.toContain("ephemeral-secret");
  expect(screen.getByRole("button", { name: "撤销 current" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("配置 JSON"), { target: { value: '{"enabled":true}' } });
  fireEvent.click(screen.getByRole("button", { name: "发布签名配置" }));
  await screen.findByText("已发布配置 revision 2");
  expect(posts.find((item) => item.path === "/v1/manage/configs")?.body.payload).toEqual({ enabled: true });
  fireEvent.click(screen.getByRole("button", { name: "回滚到 1" }));
  await screen.findByText("已从 1 回滚发布新 revision 2");
  expect(posts.some((item) => item.path === "/v1/manage/configs" && item.body.sourceRevision === 1)).toBe(true);
  fireEvent.click(screen.getByRole("link", { name: "性能分析" }));
  await screen.findByRole("heading", { name: "性能分析" });
  expect(screen.queryByText("apmq1_ephemeral-secret")).not.toBeInTheDocument();
});

it("restores a Trace deep link with actual timing and audited-event navigation", async () => {
  setup(`traces/${summary.traceId}`);
  await screen.findByRole("heading", { name: "Trace 详情" });
  expect(screen.getByLabelText("checkout 起始 0 ms，结束 100 ms")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "事件详情" })).toHaveAttribute("href", "/apps/com.example/events/span-event");
});

it("never sends Trace requests for a viewer opening a deep link", async () => {
  setup(`traces/${summary.traceId}`, "viewer");
  await screen.findByText("需要调查权限");
  expect(globalThis.fetch).not.toHaveBeenCalledWith(expect.stringContaining("/v1/query/traces"), expect.anything());
});

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}
