import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import type { MemoryDedupSummary } from "@/types/memoryDedup";

import { useMemoryDedupMetrics } from "./useMemoryDedupMetrics";

function summary(window: MemoryDedupSummary["window"], checked = 4): MemoryDedupSummary {
  return {
    window,
    checked,
    hit: 2,
    merged: 1,
    fact_mismatch: 0,
    fact_overlap: 0,
    conflict: 0,
    failed: 0,
    hit_rate: checked > 0 ? 2 / checked : 0,
    guard_rate: 0,
    overlap_rate: 0,
    failure_rate: 0,
    by_mode: {
      observe: {
        checked,
        hit: 2,
        merged: 1,
        fact_mismatch: 0,
        fact_overlap: 0,
        conflict: 0,
        failed: 0,
      },
      enforce: {
        checked: 0,
        hit: 0,
        merged: 0,
        fact_mismatch: 0,
        fact_overlap: 0,
        conflict: 0,
        failed: 0,
      },
    },
    trend: [],
  };
}

function ok(data: unknown): ApiResponse {
  return { status: "ok", data } as ApiResponse;
}

describe("useMemoryDedupMetrics", () => {
  let bridge: { apiGet: Mock; apiPost: Mock };

  const metricCalls = () =>
    bridge.apiGet.mock.calls.filter(
      ([endpoint]) => endpoint === "page/memory-dedup/metrics"
    );

  beforeEach(() => {
    bridge = {
      apiGet: vi.fn((_endpoint: string, params: Record<string, string>) =>
        Promise.resolve(ok(summary(params.window as MemoryDedupSummary["window"])))
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

  it("requests the selected summary window and exposes the payload", async () => {
    const hook = renderHook(() => useMemoryDedupMetrics("7d"));
    await waitFor(() => expect(hook.result.current.status).toBe("success"));

    expect(bridge.apiGet).toHaveBeenCalledWith("page/memory-dedup/metrics", {
      window: "7d",
    });
    expect(hook.result.current.data?.window).toBe("7d");
    expect(hook.result.current.data?.checked).toBe(4);
  });

  it("re-requests the page API when the window changes", async () => {
    const hook = renderHook(() => useMemoryDedupMetrics("24h"));
    await waitFor(() => expect(hook.result.current.status).toBe("success"));

    act(() => hook.result.current.setWindowValue("1h"));

    await waitFor(() => expect(hook.result.current.data?.window).toBe("1h"));
    expect(metricCalls().map(([, params]) => params.window)).toEqual([
      "24h",
      "1h",
    ]);
  });

  it("surfaces a single failed request without retrying", async () => {
    bridge.apiGet.mockRejectedValue(new Error("panel offline"));

    const hook = renderHook(() => useMemoryDedupMetrics("24h"));

    await waitFor(() => expect(hook.result.current.status).toBe("error"));
    expect(hook.result.current.data).toBeNull();
    expect(hook.result.current.error).toBe("panel offline");
    expect(metricCalls()).toHaveLength(1);
  });

  it("normalizes a payload missing optional fields instead of throwing", async () => {
    // 桥接对未知端点可能返回空对象；面板必须降级为零值而不是让整页崩掉。
    bridge.apiGet.mockResolvedValue(ok({ checked: "7", trend: undefined }));

    const hook = renderHook(() => useMemoryDedupMetrics("24h"));

    await waitFor(() => expect(hook.result.current.status).toBe("success"));
    const data = hook.result.current.data;
    expect(data?.trend).toEqual([]);
    expect(data?.checked).toBe(0);
    expect(data?.fact_overlap).toBe(0);
    expect(data?.overlap_rate).toBe(0);
    expect(data?.by_mode.observe.checked).toBe(0);
    expect(data?.by_mode.enforce.failed).toBe(0);
  });
});
