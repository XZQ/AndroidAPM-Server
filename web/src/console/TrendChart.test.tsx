import { render, screen } from "@testing-library/react";

import { TrendChart } from "./TrendChart";

it("exposes a textual summary for the visual trend", () => {
  render(<TrendChart timestamps={[1000, 2000]} series={[{ label: "Crash", color: "#fff", values: [1, 3] }]} summary="当前窗口 Crash 从 1 上升到 3" />);
  expect(screen.getByRole("img", { name: "当前窗口 Crash 从 1 上升到 3" })).toBeInTheDocument();
  expect(screen.getByText("当前窗口 Crash 从 1 上升到 3")).toHaveClass("sr-only");
});
