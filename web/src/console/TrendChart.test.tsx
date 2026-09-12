import { render, screen } from "@testing-library/react";

import { TrendChart } from "./TrendChart";

it("exposes a textual summary for the visual trend", () => {
  render(<TrendChart timestamps={[1000, 2000]} series={[{ label: "Crash", color: "#fff", values: [1, 3] }]} summary="当前窗口 Crash 从 1 上升到 3" />);
  expect(screen.getByRole("img", { name: "当前窗口 Crash 从 1 上升到 3" })).toBeInTheDocument();
  expect(screen.getByText("当前窗口 Crash 从 1 上升到 3")).toHaveClass("sr-only");
});

it("keeps gaps disconnected and shows isolated observed points", () => {
  const { container } = render(<TrendChart timestamps={[1, 2, 3, 4, 5]} series={[{ label: "Crash", color: "#fff", values: [1, null, 2, 3, null] }]} summary="存在缺口" />);
  expect(container.querySelectorAll("polyline")).toHaveLength(1);
  expect(container.querySelector("polyline")?.getAttribute("points")?.split(" ")).toHaveLength(2);
  expect(container.querySelectorAll("circle")).toHaveLength(1);
  expect(screen.getByText("Crash · 有数据缺口")).toBeInTheDocument();
});

it("does not draw a zero line for absent data but preserves observed zero", () => {
  const { container, rerender } = render(<TrendChart timestamps={[1, 2]} series={[{ label: "Crash", color: "#fff", values: [null, null] }]} summary="不可用" />);
  expect(screen.getByRole("status")).toHaveTextContent("没有可绘制的趋势数据");
  expect(container.querySelector("svg")).toBeNull();
  rerender(<TrendChart timestamps={[1, 2]} series={[{ label: "Crash", color: "#fff", values: [0, 0] }]} summary="观测零值" />);
  expect(screen.queryByRole("status")).toBeNull();
  expect(container.querySelectorAll("polyline")).toHaveLength(1);
});
