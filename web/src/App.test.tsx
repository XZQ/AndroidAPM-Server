import { render, screen, waitFor } from "@testing-library/react";

import App from "./App";

const scope = { tenantId: "tenant-a", appId: "com.example", environment: "production" };

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

it("restores a viewer session directly into the Issues route without an event-enumeration entry", async () => {
  window.history.pushState({}, "", "/apps/com.example/issues");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = requestPath(input);
    if (path === "/v1/web/session") return json({ requestId: "session-1", scope, role: "viewer", expiresAtMs: Date.now() + 60_000 });
    if (path.startsWith("/v1/query/fingerprints?")) return json({ requestId: "fingerprints-1", scope, window: { fromMs: 1, toMs: 2 }, releaseVersion: "2.0.0", state: "ZERO", sampleCount: 0, fingerprintCoverage: null, items: [] });
    return json({ message: `unexpected ${path}` }, 404);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Issues" })).toBeInTheDocument();
  expect(screen.queryByText("事件探索")).not.toBeInTheDocument();
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith(expect.stringContaining("/v1/query/fingerprints?"), expect.any(Object)));
});

it("restores an investigator session into a recoverable Issue detail route", async () => {
  window.history.pushState({}, "", "/apps/com.example/issues/fingerprint-1");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = requestPath(input);
    if (path === "/v1/web/session") return json({ requestId: "session-2", scope, role: "investigator", expiresAtMs: Date.now() + 60_000 });
    if (path.startsWith("/v1/query/issues/fingerprint-1?")) return json({ requestId: "issue-1", scope, window: { fromMs: 1, toMs: 2 }, fingerprint: "canonical-fingerprint", state: "PRESENT", reason: null, eventFamily: "JAVA_CRASH", eventCount: 2, affectedInstallationCount: 1, firstSeenMs: 1000, lastSeenMs: 2000, trend: [{ bucketStartMs: 1000, bucketEndMs: 2000, eventCount: 2, affectedInstallationCount: 1 }], releases: distribution("release_version", [{ label: "2.0.0", eventCount: 2, affectedInstallationCount: 1 }]), scenes: distribution("scene", []), deviceModels: distribution("device_model", [], "UNKNOWN_COVERAGE"), androidVersions: distribution("android_version", [], "UNKNOWN_COVERAGE") });
    if (path.startsWith("/v1/query/events?")) return json({ requestId: "events-1", scope, window: { fromMs: 1, toMs: 2 }, items: [], nextCursor: null });
    return json({ message: `unexpected ${path}` }, 404);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: /JAVA_CRASH/ })).toBeInTheDocument();
  expect(screen.getByText("发生趋势")).toBeInTheDocument();
  expect(screen.getByText("版本", { selector: "button" })).toHaveAttribute("aria-selected", "true");
  expect(globalThis.fetch).toHaveBeenCalledWith(expect.stringContaining("fingerprint=canonical-fingerprint"), expect.any(Object));
});

function distribution(dimension: string, items: unknown[], state = "PRESENT") {
  return { dimension, state, reason: state === "PRESENT" ? null : "NOT_PROVIDED", items };
}

function json(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.toString() : input.url;
}

it.each(["new", "baseline"])("keeps the trust banner unavailable when the %s release fails quality gates", async (side) => {
  window.history.pushState({}, "", "/apps/com.example/overview");
  const metric = { state: "ZERO", value: 0, numerator: null, denominator: null, sampleCount: 1, coverage: 1, asOfMs: 2000, source: "durable_inbox", reason: null };
  const metrics = Object.fromEntries(["javaCrashEvents", "anrEvents", "affectedInstallations", "activeInstallations", "affectedInstallationRatio", "sdkDropRate"].map((name) => [name, metric]));
  const slice = (name: string) => ({ releaseVersion: name, state: side === name ? "UNAVAILABLE" : "PRESENT", eligibleSampleCount: 1, declaredSampleCount: 1, metrics });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = requestPath(input);
    if (path === "/v1/web/session") return json({ requestId: "session", scope, role: "viewer", expiresAtMs: Date.now() + 60_000 });
    if (path.startsWith("/v1/query/release-health?")) return json({ requestId: "health", newRelease: slice("new"), baselineRelease: slice("baseline"), comparison: {}, trend: [] });
    if (path.startsWith("/v1/query/data-quality?")) return json({ state: "PRESENT", releaseIdentity: { coverage: 1 }, installationIdentity: { coverage: 1 } });
    if (path.startsWith("/v1/query/fingerprints?")) return json({ items: [] });
    return json({}, 404);
  });
  const { container } = render(<App />);
  await screen.findByRole("heading", { name: "应用总览" });
  expect(container.querySelector(".trust-banner .state-badge")).toHaveAttribute("data-state", "UNAVAILABLE");
  expect(screen.getByRole("status")).toHaveTextContent("没有可绘制的趋势数据");
});
