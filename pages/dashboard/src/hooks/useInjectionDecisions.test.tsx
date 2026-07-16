import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  InjectionDecisionDetail,
  InjectionDecisionListItem,
  InjectionDecisionPage,
} from "@/types/injection";

import { useInjectionDecisions } from "./useInjectionDecisions";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function item(id: string): InjectionDecisionListItem {
  return {
    decision_id: id,
    created_at_ms: 1,
    trace_id: null,
    routing_mode: "manual",
    configured_preset: "balanced",
    recommended_preset: "balanced",
    resolved_preset: "balanced",
    preferred_delivery: "auto",
    resolved_delivery: "extra_user_content",
    provider_type: "openai",
    provider_model: "test",
    outcome: "injected",
    primary_reason: "manual_preset",
    fallback_applied: false,
    error_code: null,
    candidate_count: 1,
    selected_count: 1,
    dropped_count: 0,
    truncated_count: 0,
    configured_budget_chars: 4_000,
    effective_budget_chars: 4_000,
    actual_payload_chars: 100,
    context_headroom_chars: 8_000,
    decision_ms: 1,
    format_ms: 1,
    inject_ms: 1,
  };
}

function page(id: string, offset: number, limit: number, total: number): InjectionDecisionPage {
  return { items: [item(id)], offset, limit, total };
}

function detail(id: string): InjectionDecisionDetail {
  return { ...item(id), reason_codes: ["manual_preset"] };
}

function ok(data: unknown): ApiResponse {
  return { status: "ok", data } as ApiResponse;
}

describe("useInjectionDecisions", () => {
  let bridge: { apiGet: ReturnType<typeof vi.fn>; apiPost: ReturnType<typeof vi.fn> };

  const flushMicrotasks = async () => {
    await act(async () => {
      for (let index = 0; index < 8; index += 1) await Promise.resolve();
    });
  };

  beforeEach(() => {
    bridge = {
      apiGet: vi.fn((endpoint: string, params: Record<string, string>) => {
        if (endpoint === "page/injection-strategy/decisions") {
          return Promise.resolve(
            ok(page("initial", Number(params.offset), Number(params.limit), 1))
          );
        }
        if (endpoint === "page/injection-strategy/decisions/detail") {
          return Promise.resolve(ok(detail(params.decision_id)));
        }
        return Promise.reject(new Error(`Unexpected GET endpoint: ${endpoint}`));
      }),
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

  it("omits empty filters and clamps paging controls", async () => {
    const hook = renderHook(() => useInjectionDecisions({ initialLimit: 250 }));
    await waitFor(() => expect(hook.result.current.status).toBe("success"));

    expect(bridge.apiGet).toHaveBeenCalledWith(
      "page/injection-strategy/decisions",
      { offset: "0", limit: "100" }
    );
    act(() => hook.result.current.setOffset(-5));
    expect(hook.result.current.offset).toBe(0);
    act(() => hook.result.current.setLimit(0));
    expect(hook.result.current.limit).toBe(1);
  });

  it("resets offset when filters change and ignores the older response", async () => {
    const hook = renderHook(() => useInjectionDecisions({ initialLimit: 25 }));
    await waitFor(() => expect(hook.result.current.page?.items[0].decision_id).toBe("initial"));

    const first = deferred<ApiResponse>();
    const second = deferred<ApiResponse>();
    bridge.apiGet
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    act(() => hook.result.current.setOffset(50));
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledTimes(2));
    act(() => hook.result.current.setFilter("outcome", "error"));
    expect(hook.result.current.offset).toBe(0);

    second.resolve(ok(page("new", 0, 25, 1)));
    await waitFor(() => expect(hook.result.current.page?.items[0].decision_id).toBe("new"));
    first.resolve(ok(page("old", 50, 25, 100)));
    await flushMicrotasks();
    expect(hook.result.current.page?.items[0].decision_id).toBe("new");
    expect(bridge.apiGet).toHaveBeenLastCalledWith(
      "page/injection-strategy/decisions",
      { offset: "0", limit: "25", outcome: "error" }
    );
  });

  it("keeps detail generations independent from the list and clear invalidates late detail", async () => {
    const lateDetail = deferred<ApiResponse>();
    bridge.apiGet.mockImplementation((endpoint: string, params: Record<string, string>) => {
      if (endpoint === "page/injection-strategy/decisions") {
        return Promise.resolve(ok(page("list", 0, 25, 1)));
      }
      if (endpoint === "page/injection-strategy/decisions/detail") {
        return lateDetail.promise;
      }
      return Promise.reject(new Error(`Unexpected GET endpoint: ${endpoint}`));
    });
    const hook = renderHook(() => useInjectionDecisions({ initialLimit: 25 }));
    await waitFor(() => expect(hook.result.current.status).toBe("success"));

    act(() => void hook.result.current.loadDetail("late"));
    expect(hook.result.current.detailStatus).toBe("loading");
    act(() => hook.result.current.clearDetail());
    lateDetail.resolve(ok(detail("late")));
    await flushMicrotasks();

    expect(hook.result.current.detailStatus).toBe("idle");
    expect(hook.result.current.detail).toBeNull();
    expect(hook.result.current.page?.items[0].decision_id).toBe("list");
  });
});
