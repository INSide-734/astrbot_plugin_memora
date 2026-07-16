import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  InjectionDecisionDetail,
  InjectionDecisionListItem,
  InjectionDecisionPage,
} from "@/types/injection";
import { handleApiGet } from "@/mock/server";
import { INJECTION_DECISIONS, INJECTION_MOCK_NOW_MS } from "@/mock/data";

import { useInjectionDecisions } from "./useInjectionDecisions";

const SAFE_INJECTION_DETAIL_KEYS = [
  "decision_id",
  "created_at_ms",
  "trace_id",
  "routing_mode",
  "configured_preset",
  "recommended_preset",
  "resolved_preset",
  "preferred_delivery",
  "resolved_delivery",
  "fallback_applied",
  "outcome",
  "error_code",
  "primary_reason",
  "reason_codes",
  "provider_type",
  "provider_model",
  "candidate_count",
  "selected_count",
  "dropped_count",
  "truncated_count",
  "configured_budget_chars",
  "effective_budget_chars",
  "actual_payload_chars",
  "context_headroom_chars",
  "decision_ms",
  "format_ms",
  "inject_ms",
] as const satisfies readonly (keyof InjectionDecisionDetail)[];

const FORBIDDEN_INJECTION_DETAIL_KEYS = [
  "query",
  "prompt",
  "memory_content",
  "memory_ids",
  "user_id",
  "group_id",
  "persona_id",
  "session_id",
  "api_key",
  "authorization",
  "headers",
  "endpoint",
  "base_url",
  "stack_trace",
] as const;

function expectSanitizedInjectionDetail(value: object) {
  expect(Object.keys(value).sort()).toEqual(
    [...SAFE_INJECTION_DETAIL_KEYS].sort(),
  );
  for (const key of FORBIDDEN_INJECTION_DETAIL_KEYS) {
    expect(value).not.toHaveProperty(key);
  }
}

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

describe("mock injection strategy API", () => {
  it("serves catalog summary true pages and sanitized detail", async () => {
    const catalog = await handleApiGet("page/injection-strategy/catalog", {});
    expect(catalog).toMatchObject({
      status: "ok",
      data: { retention_options: [7, 30, 90, 180, 0] },
    });
    expect((catalog.data as { deliveries: string[] }).deliveries)
      .not.toContain("system_prompt");

    const summary = await handleApiGet("page/injection-strategy/summary", {
      window: "24h",
    });
    expect(summary).toMatchObject({
      status: "ok",
      data: { window: "24h" },
    });

    const pageResponse = await handleApiGet(
      "page/injection-strategy/decisions",
      { offset: "25", limit: "25" },
    );
    expect(pageResponse.status).toBe("ok");
    const pageData = pageResponse.data as InjectionDecisionPage;
    expect(pageData.offset).toBe(25);
    expect(pageData.limit).toBe(25);
    expect(pageData.items).toHaveLength(25);
    expect(pageData.total).toBe(72);

    const detailResponse = await handleApiGet(
      "page/injection-strategy/decisions/detail",
      { decision_id: "00000000-0000-4000-8000-000000000001" },
    );
    expect(detailResponse.status).toBe("ok");
    const detailData = detailResponse.data as InjectionDecisionDetail;
    expectSanitizedInjectionDetail(detailData);
    expect(INJECTION_DECISIONS).toHaveLength(72);
    for (const row of INJECTION_DECISIONS) {
      expectSanitizedInjectionDetail(row);
    }
  });

  it("applies all eight filters before true pagination", async () => {
    const response = await handleApiGet(
      "page/injection-strategy/decisions",
      {
        offset: "0",
        limit: "25",
        from_ms: String(INJECTION_MOCK_NOW_MS),
        to_ms: String(INJECTION_MOCK_NOW_MS),
        routing_mode: "manual",
        resolved_preset: "tool_first",
        provider_type: "openai",
        primary_reason: "PROVIDER_DELIVERY_DOWNGRADED",
        fallback_applied: "true",
        outcome: "injected",
      },
    );
    const data = response.data as InjectionDecisionPage;

    expect(response.status).toBe("ok");
    expect(data.total).toBe(1);
    expect(data.items[0].decision_id)
      .toBe("00000000-0000-4000-8000-000000000001");
  });

  it.each([
    [{ offset: "-1" }, "offset must be non-negative"],
    [{ limit: "101" }, "limit must be between 1 and 100"],
    [{ fallback_applied: "yes" }, "fallback_applied must be true or false"],
    [{ routing_mode: "smart" }, "routing_mode is invalid"],
    [{ resolved_preset: "custom" }, "resolved_preset is invalid"],
    [{ outcome: "success" }, "outcome is invalid"],
    [{ provider_type: " " }, "provider_type must be a non-empty string"],
    [{ primary_reason: " " }, "primary_reason must be a non-empty string"],
    [{ unknown: "1" }, "unknown query field: unknown"],
    [{ from_ms: "20", to_ms: "10" }, "from_ms must not exceed to_ms"],
  ])("matches backend filter validation for %o", async (params, message) => {
    const response = await handleApiGet(
      "page/injection-strategy/decisions",
      params,
    );
    expect(response).toEqual({ status: "error", message });
  });

  it("validates summary windows and decision UUIDs", async () => {
    await expect(handleApiGet("page/injection-strategy/summary", {
      window: "forever",
    })).resolves.toEqual({
      status: "error",
      message: "window must be one of 1h, 24h, 7d, 30d",
    });
    await expect(handleApiGet("page/injection-strategy/decisions/detail", {
      decision_id: "not-a-uuid",
    })).resolves.toEqual({
      status: "error",
      message: "decision_id must be a valid UUID",
    });
    await expect(handleApiGet("page/injection-strategy/decisions/detail", {
      decision_id: "00000000-0000-4000-8000-999999999999",
    })).resolves.toEqual({
      status: "error",
      message: "Injection decision not found",
    });
  });
});
