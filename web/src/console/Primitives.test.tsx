import { render, screen } from "@testing-library/react";

import type { MetricResult } from "../types";
import { MetricTile } from "./Primitives";

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
  const { rerender } = render(<MetricTile label="Java Crash" metric={base} />);
  expect(screen.getByText("真实零值")).toBeInTheDocument();
  expect(screen.getByText("0")).toBeInTheDocument();

  rerender(
    <MetricTile
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

it("keeps a missing ratio distinct from a zero ratio", () => {
  render(
    <MetricTile
      label="安装影响率"
      kind="ratio"
      metric={{ ...base, state: "NO_DATA", value: null, coverage: null }}
    />,
  );
  expect(screen.getByText("无合格样本")).toBeInTheDocument();
  expect(screen.getByText("—")).toBeInTheDocument();
});
