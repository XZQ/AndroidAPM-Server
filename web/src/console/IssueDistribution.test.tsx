import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import type { IssueDistribution } from "../types";
import { DistributionView } from "./pages/IssueDetailPage";

const partial: IssueDistribution = {
  dimension: "scene",
  state: "DEGRADED",
  reason: "SCENE_EVIDENCE_EXPIRED",
  items: [{ label: "Home", eventCount: 2, affectedInstallationCount: 1 }],
  coverage: {
    totalEventCount: 5, availableEventCount: 2, missingEventCount: 0,
    expiredEventCount: 3, retentionUnknownEventCount: 0,
  },
};

it("keeps surviving scene bars with a degradation reason and exact coverage", () => {
  render(<DistributionView distribution={partial} />);
  expect(screen.getByText("Home")).toBeInTheDocument();
  expect(screen.getByText("数据降级")).toBeInTheDocument();
  expect(screen.getByText("部分或全部场景证据已按保留策略过期")).toBeInTheDocument();
  expect(screen.getByText(/可用 2 \/ 5 事件/)).toHaveTextContent("未提供 0 · 已过期 3 · 历史状态未知 0");
  expect(screen.queryByText("客户端未提供标准场景字段")).not.toBeInTheDocument();
});

it("explains historical retention gaps when no scene remains available", () => {
  render(<DistributionView distribution={{
    ...partial, state: "UNAVAILABLE", reason: "SCENE_RETENTION_UNKNOWN", items: [],
    coverage: { ...partial.coverage!, availableEventCount: 0, expiredEventCount: 0, retentionUnknownEventCount: 5 },
  }} />);
  expect(screen.getByText("历史清理未记录场景是否曾提供，分布覆盖不完整")).toBeInTheDocument();
  expect(screen.getByText(/可用 0 \/ 5 事件/)).toHaveTextContent("历史状态未知 5");
  expect(screen.queryByText("Home")).not.toBeInTheDocument();
});
