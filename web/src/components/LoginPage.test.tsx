import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { LoginPage } from "./LoginPage";

it("does not persist the long-lived query credential in browser storage", async () => {
  const localStorageSpy = vi.spyOn(Storage.prototype, "setItem");
  const authenticated = vi.fn();
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        requestId: "request-1",
        scope: { tenantId: "local", appId: "com.example", environment: "production" },
        role: "investigator",
        expiresAtMs: Date.now() + 3_600_000,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );

  render(<LoginPage onAuthenticated={authenticated} />);
  const input = screen.getByLabelText("Query Key");
  fireEvent.change(input, { target: { value: "apmq1_once_only" } });
  fireEvent.click(screen.getByRole("button", { name: "建立短时会话" }));

  await waitFor(() => expect(authenticated).toHaveBeenCalledTimes(1));
  expect(input).toHaveValue("");
  expect(localStorageSpy).not.toHaveBeenCalled();
});
