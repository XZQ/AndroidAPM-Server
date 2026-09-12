import { render, screen } from "@testing-library/react";

import type { MetricResult } from "../types";
import { MetricCard } from "./MetricCard";

const base: MetricResult = {
  state: "ZERO",
  value: 0,
  numerator: null,
  denominator: null,
  sampleCount: 42,
  coverage: 1,
  asOfMs: 1_700_000_000_000,
  source: "durable_inbox",
  reason: null,
};

it("renders a proven zero separately from an unavailable metric", () => {
  const { rerender } = render(<MetricCard label="Java Crash" metric={base} />);
  expect(screen.getByText("真实零值")).toBeInTheDocument();
  expect(screen.getByText("0")).toBeInTheDocument();

  rerender(
    <MetricCard
      label="Crash-free sessions"
      metric={{
        ...base,
        state: "UNAVAILABLE",
        value: null,
        coverage: null,
        reason: "SESSION_ID_NOT_PROVIDED",
      }}
    />,
  );
  expect(screen.getByText("不可计算")).toBeInTheDocument();
  expect(screen.getByText("客户端尚未提供标准会话身份")).toBeInTheDocument();
  expect(screen.getAllByText("—").length).toBeGreaterThan(0);
});
