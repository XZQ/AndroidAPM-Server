import { useState } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";

import { ConsoleLayout } from "./ConsoleLayout";
import { EventDetailPage } from "./pages/EventDetailPage";
import { ReleasesPage } from "./pages/ReleasesPage";
import { defaultFilters } from "./context";

const scope = { tenantId: "test", appId: "app", environment: "test" };
const session = { requestId: "session", scope, role: "investigator" as const, expiresAtMs: 1_800_000_000_000 };
const handleFailure = (_caught: unknown, fallback: string) => fallback;

function Host() {
  const [filters, setFilters] = useState(defaultFilters);
  const [dark, setDark] = useState(false);
  return <><button onClick={() => setFilters((value) => ({ ...value, newRelease: "3.0.0" }))}>切换版本</button>
    <Link to="/events/b">事件 B</Link>
    <ConsoleLayout value={{ session, filters, setFilters, dark, setDark, handleFailure, logout: async () => {} }} />
  </>;
}

function mount(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><Routes><Route element={<Host />}>
    <Route path="/releases" element={<ReleasesPage />} />
    <Route path="/events/:eventId" element={<EventDetailPage />} />
  </Route></Routes></MemoryRouter>);
}

function health(release: string, crashes: number) {
  const metrics = Object.fromEntries(["javaCrashEvents", "anrEvents", "affectedInstallations", "activeInstallations", "affectedInstallationRatio"].map((name) => [name, { value: crashes }]));
  return { requestId: release, scope, window: { fromMs: 1000, toMs: 2000 }, newRelease: { releaseVersion: release, state: "PRESENT", metrics }, baselineRelease: { state: "PRESENT" } };
}

function metadata(id: string) {
  return { requestId: id, scope, schemaVersion: "3", protocol: "protobuf", fieldStates: {}, event: { eventId: id, module: "crash", name: `crash-${id}`, eventFamily: "JAVA_CRASH", occurrenceTimestampMs: 1000, receivedAt: "2026-09-07T00:00:00Z", incidentFingerprint: null, installationHmac: null } };
}

function dataQuality(state = "DEGRADED") {
  return {
    requestId: "dq-1",
    scope,
    window: { fromMs: 1000, toMs: 2000 },
    releaseVersion: "2.0.0",
    state,
    sampleCount: 10,
    protocolCounts: {},
    schemaVersionCounts: {},
    inboxStatusCounts: {},
    releaseIdentity: { state: "PRESENT", value: 1, numerator: 10, denominator: 10, sampleCount: 10, coverage: 1, asOfMs: 2000, source: "durable_inbox", reason: null },
    installationIdentity: { state: "PRESENT", value: 1, numerator: 10, denominator: 10, sampleCount: 10, coverage: 1, asOfMs: 2000, source: "durable_inbox", reason: null },
    sdkHealth: { state: "PRESENT", value: 0, numerator: 0, denominator: 10, sampleCount: 10, coverage: 1, asOfMs: 2000, source: "durable_inbox", reason: null },
    lateData: { state: "ZERO", value: 0, numerator: 0, denominator: 10, sampleCount: 10, coverage: 1, asOfMs: 2000, source: "durable_inbox", reason: null },
  };
}

const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
beforeEach(() => { vi.restoreAllMocks(); document.cookie = "androidapm_csrf=synthetic-test-csrf; Path=/"; });

it("removes old release evidence and decision controls when the changed query fails", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = typeof url === "string" ? url : url instanceof URL ? url.toString() : url.url;
    if (path.includes("release-decisions")) return await Promise.resolve(json({ items: [] }));
    if (path.includes("data-quality")) return await Promise.resolve(json(dataQuality()));
    if (path.includes("newRelease=3.0.0")) throw new Error("offline");
    return await Promise.resolve(json(health("2.0.0", 12345)));
  });
  mount("/releases");
  await screen.findByText("人工发布决策");
  fireEvent.click(screen.getByText("切换版本"));
  await screen.findByText("发布证据查询失败");
  expect(screen.queryByText("人工发布决策")).not.toBeInTheDocument();
  expect(screen.queryByText("12345")).not.toBeInTheDocument();
});

it("records the true data-quality state on release decisions rather than duplicating release state", async () => {
  let submittedBody: Record<string, unknown> | null = null;
  vi.spyOn(globalThis, "fetch").mockImplementation((url, init) => {
    const path = typeof url === "string" ? url : url instanceof URL ? url.toString() : url.url;
    if (path.includes("release-decisions") && init?.method === "POST") {
      submittedBody = JSON.parse(
        typeof init.body === "string" ? init.body : "{}",
      ) as Record<string, unknown>;
      return Promise.resolve(json({
        id: 1, releaseVersion: "2.0.0", decision: "pause", actor: "query-key:test",
        evidenceFromMs: 1000, evidenceToMs: 2000, reason: "confirming degraded data quality snapshot",
        evidence: submittedBody.evidence, createdAt: "2026-11-26T00:00:00Z",
      }));
    }
    if (path.includes("release-decisions")) return Promise.resolve(json({ items: [] }));
    if (path.includes("data-quality")) return Promise.resolve(json(dataQuality("DEGRADED")));
    return Promise.resolve(json(health("2.0.0", 7)));
  });
  mount("/releases");
  await screen.findByText("人工发布决策");
  fireEvent.change(screen.getByPlaceholderText("记录样本、风险、业务窗口和下一步"), {
    target: { value: "confirming degraded data quality snapshot" },
  });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: /记录“暂停发布”/ }));
  await waitFor(() => expect(submittedBody).not.toBeNull());
  const evidence = (submittedBody as unknown as { evidence: { releaseState: string; dataQualityState: string } }).evidence;
  expect(evidence.releaseState).toBe("PRESENT");
  expect(evidence.dataQualityState).toBe("DEGRADED");
});

it("ignores an older release response arriving after the new query completed", async () => {
  let resolveOld!: (response: Response) => void;
  const old = new Promise<Response>((resolve) => { resolveOld = resolve; });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = typeof url === "string" ? url : url instanceof URL ? url.toString() : url.url;
    if (path.includes("release-decisions")) return await Promise.resolve(json({ items: [] }));
    if (path.includes("data-quality")) return await Promise.resolve(json(dataQuality("PRESENT")));
    return path.includes("newRelease=2.0.0") ? old : json(health("3.0.0", 54321));
  });
  mount("/releases");
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
  fireEvent.click(screen.getByText("切换版本"));
  await screen.findByText("人工发布决策");
  await act(async () => { resolveOld(json(health("2.0.0", 12345))); await old; });
  expect(screen.queryByText("12345")).not.toBeInTheDocument();
  expect(screen.getAllByText("54321").length).toBeGreaterThan(0);
});

it("preserves non-windowed event detail state when topbar filters change", async () => {
  let metadataFetches = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = typeof url === "string" ? url : url instanceof URL ? url.toString() : url.url;
    metadataFetches += 1;
    return await Promise.resolve(json(metadata(path.endsWith("/b") ? "b" : "a")));
  });
  mount("/events/a");
  await screen.findByRole("heading", { name: "crash/crash-a" });
  const input = screen.getByLabelText("读取理由（至少 10 字符）");
  fireEvent.change(input, { target: { value: "keep this unsaved reason" } });
  fireEvent.click(screen.getByText("切换版本"));
  expect(screen.getByLabelText("读取理由（至少 10 字符）")).toHaveValue("keep this unsaved reason");
  expect(metadataFetches).toBe(1);
});

it("does not display an outstanding raw response for A under event B", async () => {
  let resolveRaw!: (response: Response) => void;
  const raw = new Promise<Response>((resolve) => { resolveRaw = resolve; });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = typeof url === "string" ? url : url instanceof URL ? url.toString() : url.url;
    if (path.includes("/raw")) return raw;
    return await Promise.resolve(json(metadata(path.endsWith("/b") ? "b" : "a")));
  });
  mount("/events/a");
  await screen.findByRole("heading", { name: "crash/crash-a" });
  fireEvent.change(screen.getByLabelText("读取理由（至少 10 字符）"), { target: { value: "investigate synthetic event A" } });
  fireEvent.click(screen.getByText("审计后读取"));
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith(expect.stringContaining("/raw"), expect.anything()));
  fireEvent.click(screen.getByText("事件 B"));
  await screen.findByRole("heading", { name: "crash/crash-b" });
  await act(async () => { resolveRaw(json({ rawPayload: { secret: "raw-event-A" }, symbolizedResult: null })); await raw; });
  expect(screen.queryByText(/raw-event-A/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("读取理由（至少 10 字符）")).toHaveValue("");
});
