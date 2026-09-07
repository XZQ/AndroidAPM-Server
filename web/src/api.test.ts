import { getIssueDetail, getRawEvent, login } from "./api";

const sessionBody = {
  requestId: "request-1",
  scope: { tenantId: "local", appId: "com.example", environment: "production" },
  role: "investigator",
  expiresAtMs: 1_800_000_000_000,
};

beforeEach(() => {
  vi.restoreAllMocks();
  document.cookie = "androidapm_csrf=; Max-Age=0; path=/";
});

it("exchanges the query key once using same-origin no-store transport", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(sessionBody), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );

  await expect(login("apmq1_key_secret")).resolves.toEqual(sessionBody);
  const [path, init] = fetchMock.mock.calls[0] ?? [];
  expect(path).toBe("/v1/web/session");
  expect(init).toMatchObject({ credentials: "same-origin", cache: "no-store", method: "POST" });
  expect(init?.body).toBe(JSON.stringify({ queryKey: "apmq1_key_secret" }));
});

it("binds unsafe raw access to the CSRF companion cookie", async () => {
  document.cookie = "androidapm_csrf=csrf-test-value; path=/";
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        requestId: "raw-1",
        scope: sessionBody.scope,
        event: {},
        rawPayload: {},
        symbolizedResult: null,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );

  await getRawEvent("event-1", "incident_diagnosis", "Reason long enough");
  const init = fetchMock.mock.calls[0]?.[1];
  const headers = new Headers(init?.headers);
  expect(headers.get("X-Apm-Csrf-Token")).toBe("csrf-test-value");
});

it("preserves the safe request id and code from API failures", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        requestId: "safe-request-id",
        code: "invalid_credential",
        message: "The query credential is invalid",
        retryable: false,
      }),
      { status: 401, headers: { "Content-Type": "application/json" } },
    ),
  );

  await expect(login("bad")).rejects.toMatchObject({
    status: 401,
    code: "invalid_credential",
    requestId: "safe-request-id",
  });
});

it("encodes the stable fingerprint in the Issue detail request", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ fingerprint: "java/main thread", items: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );

  await getIssueDetail("java/main thread", {
    newRelease: "2.0.0",
    baselineRelease: "1.0.0",
    fromMs: 1000,
    toMs: 2000,
  });

  expect(fetchMock.mock.calls[0]?.[0]).toBe(
    "/v1/query/issues/java%2Fmain%20thread?fromMs=1000&toMs=2000",
  );
});
