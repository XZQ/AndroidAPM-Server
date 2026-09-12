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

const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
beforeEach(() => { vi.restoreAllMocks(); document.cookie = "androidapm_csrf=synthetic-test-csrf; Path=/"; });

it("removes old release evidence and decision controls when the changed query fails", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = typeof url === "string" ? url : url instanceof URL ? url.toString() : url.url;
    if (path.includes("release-decisions")) return await Promise.resolve(json({ items: [] }));
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

it("ignores an older release response arriving after the new query completed", async () => {
  let resolveOld!: (response: Response) => void;
  const old = new Promise<Response>((resolve) => { resolveOld = resolve; });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
    const path = typeof url === "string" ? url : url instanceof URL ? url.toString() : url.url;
    if (path.includes("release-decisions")) return await Promise.resolve(json({ items: [] }));
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
