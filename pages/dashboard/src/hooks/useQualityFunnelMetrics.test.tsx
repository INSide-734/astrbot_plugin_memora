import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import type {
  QualityFunnelStage,
  QualityFunnelSummary,
} from "@/types/qualityFunnel";

import {
  normalizeQualityFunnelSummary,
  useQualityFunnelMetrics,
} from "./useQualityFunnelMetrics";

function stage(id: QualityFunnelStage["id"]): QualityFunnelStage {
  return {
    id,
    state: "available",
    reason: "ok",
    counts: { checked: 6, hit: 3 },
    values: {},
    rates: { hit_rate: 0.5 },
  };
}

function summary(window: QualityFunnelSummary["window"]): QualityFunnelSummary {
  return {
    window,
    bucket: "utc_day",
    advisory: true,
    stages: [stage("candidates"), stage("facts"), stage("dedup"), stage("injection")],
    trend: [
      {
        day: "2026-09-19",
        candidates: 8,
        canonical: 2,
        merged: 1,
        facts_rejected: 1,
        dedup_checked: 6,
        dedup_hit: 3,
        decisions: 4,
        selected: 5,
      },
    ],
  };
}

function ok(data: unknown): ApiResponse {
  return { status: "ok", data } as ApiResponse;
}

describe("useQualityFunnelMetrics", () => {
  let bridge: { apiGet: Mock; apiPost: Mock };

  const funnelCalls = () =>
    bridge.apiGet.mock.calls.filter(
      ([endpoint]) => endpoint === "page/metrics/quality-funnel"
    );

  beforeEach(() => {
    bridge = {
      apiGet: vi.fn((_endpoint: string, params: Record<string, string>) =>
        Promise.resolve(ok(summary(params.window as QualityFunnelSummary["window"])))
      ),
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

  it("requests the selected funnel window and exposes normalized stages", async () => {
    const hook = renderHook(() => useQualityFunnelMetrics("7d"));
    await waitFor(() => expect(hook.result.current.status).toBe("success"));

    expect(bridge.apiGet).toHaveBeenCalledWith("page/metrics/quality-funnel", {
      window: "7d",
    });
    expect(hook.result.current.data?.window).toBe("7d");
    expect(hook.result.current.data?.bucket).toBe("utc_day");
    expect(hook.result.current.data?.stages.map((item) => item.id)).toEqual([
      "candidates",
      "facts",
      "dedup",
      "injection",
    ]);
    expect(hook.result.current.data?.trend[0].dedup_checked).toBe(6);
  });

  it("re-requests the page API when the window changes", async () => {
    const hook = renderHook(() => useQualityFunnelMetrics("24h"));
    await waitFor(() => expect(hook.result.current.status).toBe("success"));

    act(() => hook.result.current.setWindowValue("30d"));

    await waitFor(() => expect(hook.result.current.data?.window).toBe("30d"));
    expect(funnelCalls().map(([, params]) => params.window)).toEqual([
      "24h",
      "30d",
    ]);
  });

  it("surfaces a single failed request without retrying", async () => {
    bridge.apiGet.mockRejectedValue(new Error("panel offline"));

    const hook = renderHook(() => useQualityFunnelMetrics("24h"));

    await waitFor(() => expect(hook.result.current.status).toBe("error"));
    expect(hook.result.current.data).toBeNull();
    expect(hook.result.current.error).toBe("panel offline");
    expect(funnelCalls()).toHaveLength(1);
  });

  it("degrades a bad payload to the closed contract instead of throwing", () => {
    // 桥接对未知端点可能返回空对象或异形载荷；面板必须降级而不是让整页崩掉。
    const empty = normalizeQualityFunnelSummary(undefined);
    expect(empty.stages).toEqual([]);
    expect(empty.trend).toEqual([]);
    expect(empty.window).toBe("24h");

    const malformed = normalizeQualityFunnelSummary({
      window: "12h",
      advisory: "yes",
      stages: [
        { id: "dedup", state: "weird", reason: 7, counts: "nope" },
        { id: "unknown-stage", state: "available" },
        { id: "injection", state: "unavailable", reason: "topic_metrics_key_missing" },
      ],
      trend: [{ day: "bad", candidates: 3 }, { candidates: 9 }, "nope"],
    });
    expect(malformed.window).toBe("24h");
    expect(malformed.advisory).toBe(true);
    expect(malformed.stages.map((item) => item.id)).toEqual(["dedup", "injection"]);
    expect(malformed.stages[0]).toEqual({
      id: "dedup",
      state: "unavailable",
      reason: "read_failed",
      counts: null,
      values: null,
      rates: null,
    });
    // 不可用 stage 永远不携带计数，避免把不可用伪装成零值。
    expect(malformed.stages[1].counts).toBeNull();
    expect(malformed.trend).toEqual([]);

    const normalized = normalizeQualityFunnelSummary({
      window: "1h",
      stages: [
        {
          id: "dedup",
          state: "degraded",
          reason: "dedup_read_failed",
          counts: { checked: 2, hit: "3", scope_key: "canary" },
          values: {},
          rates: { hit_rate: 0.5, failure_rate: -1 },
        },
      ],
      trend: [{ day: "2026-09-19", candidates: 3, session_id: "canary" }],
    });
    expect(normalized.stages[0]).toEqual({
      id: "dedup",
      state: "degraded",
      reason: "dedup_read_failed",
      counts: { checked: 2, hit: 0, merged: 0, fact_mismatch: 0, fact_overlap: 0, conflict: 0, failed: 0 },
      values: {},
      rates: { hit_rate: 0.5, guard_rate: 0, overlap_rate: 0, failure_rate: 0 },
    });
    expect(Object.keys(normalized.trend[0])).toEqual([
      "day",
      "candidates",
      "canonical",
      "merged",
      "facts_rejected",
      "dedup_checked",
      "dedup_hit",
      "decisions",
      "selected",
    ]);
  });
});
