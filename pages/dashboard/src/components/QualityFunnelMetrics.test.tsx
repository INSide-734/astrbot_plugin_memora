import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { QualityFunnelMetrics } from "./QualityFunnelMetrics";

interface BridgeMock {
  apiGet: Mock;
  apiPost: Mock;
}

let bridge: BridgeMock;

function ok(data: unknown): ApiResponse {
  return { status: "ok", data } as ApiResponse;
}

function stage(
  id: string,
  state: string,
  reason: string,
  counts: Record<string, number> | null,
  values: Record<string, number> = {},
  rates: Record<string, number> = {},
) {
  return { id, state, reason, counts, values, rates };
}

/** 与后端白名单一致的成功样本（四阶段各含手算值）。 */
const SUMMARY = {
  window: "24h",
  bucket: "utc_day",
  advisory: true,
  stages: [
    stage(
      "candidates",
      "available",
      "ok",
      {
        windows: 6,
        candidates: 22,
        exact_reuse: 4,
        duplicate_topics: 2,
        identity_drops: 2,
        budget_exceeded: 1,
        catalog_degraded: 1,
      },
      {},
      { reuse_rate: 4 / 22, degraded_rate: 1 / 6 },
    ),
    stage(
      "facts",
      "degraded",
      "summary_read_failed",
      {
        windows: 0,
        canonical: 0,
        merged: 0,
        quarantined: 0,
        discarded: 0,
        mark_write: 0,
        failed: 0,
        skipped: 0,
        facts_rejected: 0,
      },
      {},
      { merge_rate: 0, discard_rate: 0 },
    ),
    stage(
      "dedup",
      "unavailable",
      "topic_metrics_key_rotation_gap",
      null,
    ),
    stage(
      "injection",
      "available",
      "ok",
      {
        decisions: 5,
        selected: 12,
        dropped: 3,
        truncated: 1,
        memory_present: 4,
        payload_injected: 4,
      },
      { budget_utilization: 0.62 },
      { memory_present_rate: 0.8, payload_injected_rate: 0.8 },
    ),
  ],
  trend: [
    {
      day: "2025-06-15",
      candidates: 22,
      canonical: 8,
      merged: 3,
      facts_rejected: 2,
      dedup_checked: 10,
      dedup_hit: 3,
      decisions: 5,
      selected: 12,
    },
  ],
};

beforeEach(() => {
  bridge = {
    apiGet: vi.fn(),
    apiPost: vi.fn(),
  };
  Object.defineProperty(window, "AstrBotPluginPage", {
    configurable: true,
    value: bridge,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Object.defineProperty(window, "AstrBotPluginPage", {
    configurable: true,
    value: undefined,
  });
});

describe("QualityFunnelMetrics", () => {
  it("renders every stage with its state, counts, rates and reason code", async () => {
    bridge.apiGet.mockResolvedValue(ok(structuredClone(SUMMARY)));

    render(<QualityFunnelMetrics />);

    expect(await screen.findByText("候选生成")).toBeTruthy();
    expect(screen.getByText("事实准入")).toBeTruthy();
    expect(screen.getByText("跨窗口去重")).toBeTruthy();
    expect(screen.getByText("注入结果")).toBeTruthy();

    // 可用 stage 展示手算计数与派生比率。
    expect(screen.getByText("候选数")).toBeTruthy();
    expect(screen.getByText("18.2%")).toBeTruthy();
    expect(screen.getByText("16.7%")).toBeTruthy();
    expect(screen.getByText("62%")).toBeTruthy();
    // 降级 stage 展示状态与原因码，不隐藏数据。
    expect(screen.getByText("降级")).toBeTruthy();
    expect(screen.getByText(/summary_read_failed/)).toBeTruthy();
    // 不可用 stage 不展示零值，改为显式不可用说明。
    expect(screen.getByText("不可用")).toBeTruthy();
    expect(
      screen.getByText("该阶段当前不可用，计数不返回（不使用零值伪装）。"),
    ).toBeTruthy();
    // 趋势行展示 UTC 日与手算候选数。
    expect(screen.getByText("2025-06-15")).toBeTruthy();
  });

  it("shows the empty state when the payload has no stages", async () => {
    bridge.apiGet.mockResolvedValue(
      ok({ window: "24h", bucket: "utc_day", advisory: true, stages: [], trend: [] })
    );

    render(<QualityFunnelMetrics />);

    expect(
      await screen.findByText("当前窗口没有 funnel 数据"),
    ).toBeTruthy();
  });

  it("shows the error state with retry when the request fails", async () => {
    bridge.apiGet.mockRejectedValue(new Error("panel offline"));

    render(<QualityFunnelMetrics />);

    expect(await screen.findByText("无法加载质量 funnel")).toBeTruthy();
    expect(screen.getByText("panel offline")).toBeTruthy();
    expect(screen.getByRole("button", { name: /重试|Retry/ })).toBeTruthy();
  });

  it("degrades a malformed payload to the empty state instead of crashing", async () => {
    bridge.apiGet.mockResolvedValue(ok({ checked: "nope", trend: undefined }));

    render(<QualityFunnelMetrics />);

    expect(await screen.findByText("当前窗口没有 funnel 数据")).toBeTruthy();
  });
});
