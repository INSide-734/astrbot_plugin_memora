import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { InjectionStrategySummary } from "@/types/injection";

import { useInjectionStrategySummary } from "./useInjectionStrategySummary";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function summary(windowValue: "1h" | "24h" | "7d" | "30d", count = 1): InjectionStrategySummary {
  return {
    window: windowValue,
    decision_count: count,
    payload_chars_p95: 100,
    provider_fallback_rate: 0,
    preset_distribution: { balanced: count },
    cost_trend: [],
    recent_events: [],
  };
}

function ok(data: unknown): ApiResponse {
  return { status: "ok", data } as ApiResponse;
}

describe("useInjectionStrategySummary", () => {
  let bridge: { apiGet: ReturnType<typeof vi.fn>; apiPost: ReturnType<typeof vi.fn> };
  let visibility: DocumentVisibilityState;

  const summaryCalls = () =>
    bridge.apiGet.mock.calls.filter(
      ([endpoint]) => endpoint === "page/injection-strategy/summary"
    );

  const flushMicrotasks = async () => {
    await act(async () => {
      for (let index = 0; index < 8; index += 1) await Promise.resolve();
    });
  };

  beforeEach(() => {
    visibility = "visible";
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => visibility,
    });
    bridge = {
      apiGet: vi.fn((_endpoint: string, params: Record<string, string>) =>
        Promise.resolve(ok(summary(params.window as "24h")))
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
    vi.useRealTimers();
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  it("polls every thirty seconds only while visible and stops after unmount", async () => {
    vi.useFakeTimers();
    const hook = renderHook(() => useInjectionStrategySummary("24h", 30_000));
    await flushMicrotasks();
    expect(summaryCalls()).toHaveLength(1);

    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(summaryCalls()).toHaveLength(2);
    visibility = "hidden";
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(summaryCalls()).toHaveLength(2);

    hook.unmount();
    visibility = "visible";
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(summaryCalls()).toHaveLength(2);
  });

  it("loads a changed window immediately and ignores the older response", async () => {
    const oldResponse = deferred<ApiResponse>();
    const newResponse = deferred<ApiResponse>();
    bridge.apiGet
      .mockImplementationOnce(() => oldResponse.promise)
      .mockImplementationOnce(() => newResponse.promise);
    const hook = renderHook(() => useInjectionStrategySummary("24h", 30_000));

    act(() => hook.result.current.setWindowValue("7d"));
    newResponse.resolve(ok(summary("7d", 7)));
    await waitFor(() => expect(hook.result.current.data?.window).toBe("7d"));
    oldResponse.resolve(ok(summary("24h", 24)));
    await flushMicrotasks();

    expect(hook.result.current.data?.window).toBe("7d");
    expect(hook.result.current.data?.decision_count).toBe(7);
  });
});
