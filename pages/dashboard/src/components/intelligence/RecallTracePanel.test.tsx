import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RecallTracePanel } from "./RecallTracePanel";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale: ReturnType<typeof vi.fn>;
  getI18n: ReturnType<typeof vi.fn>;
  t: ReturnType<typeof vi.fn>;
  onContext: ReturnType<typeof vi.fn>;
}

/** 构造 Dashboard bridge 成功 envelope。 */
function ok<T>(data: T) {
  return { status: "ok", data };
}

/** 构造一条不含查询和 canonical ID 的持久化 trace。 */
function persistedTrace(traceId: string) {
  return {
    trace_id: traceId,
    total_ms: 12.3,
    stages: [],
    results: [],
    filtered: [],
    created_at: 1783150200,
    metadata: {},
  };
}

/** 构造可由测试显式完成的 Promise。 */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

describe("RecallTracePanel", () => {
  let bridge: BridgeMock;
  let showToast: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    bridge = {
      apiGet: vi.fn(),
      apiPost: vi.fn(),
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
      onContext: vi.fn().mockReturnValue(vi.fn()),
    };
    showToast = vi.fn();

    bridge.apiPost.mockResolvedValue(ok({
      trace_id: "trace-coffee",
      total_ms: 84.2,
      stages: [
        { name: "search_memories", duration_ms: 4.1, candidate_count: 0, metadata: {} },
        { name: "bm25", duration_ms: 12.5, candidate_count: 7, metadata: { candidate_count: 7 } },
      ],
      results: [
        {
          rank: 1,
          initial_score: 0.71,
          final_score: 0.93,
          score_contributions: [
            { source: "bm25", score: 0.62, weight: 0.35 },
            { source: "emotion_boost", score: 0.21, weight: 0.2 },
          ],
          metadata: { memory_type: "preference" },
        },
      ],
      filtered: [
        { reason: "low_score", stage: "rerank", score: 0.12 },
        { reason: "missing_fields" },
      ],
      created_at: 1783150200,
      metadata: { debug_trace_available: true },
    }));

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

  it("submits clamped trace payload and renders safe stages, scores, and filtered reasons", async () => {
    const { container } = render(<RecallTracePanel showToast={showToast} />);

    fireEvent.change(screen.getByLabelText(/Query|查询/), {
      target: { value: "用户喜欢喝什么咖啡" },
    });
    fireEvent.change(screen.getByLabelText("k"), {
      target: { value: "99" },
    });
    fireEvent.change(screen.getByLabelText("Chain depth"), {
      target: { value: "9" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Trace|追踪/ }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/recall/trace", {
        query: "用户喜欢喝什么咖啡",
        k: 20,
        chat_type: "private",
        chain_depth: 5,
      });
    });

    await waitFor(() => {
      expect(screen.getAllByText(/BM25/).length > 0).toBeTruthy();
    });
    expect(screen.getByText("Emotion boost")).toBeTruthy();
    expect(screen.getByText("Memory search")).toBeTruthy();
    expect(screen.getByText("#1")).toBeTruthy();
    expect(screen.getByText("Low score")).toBeTruthy();
    expect(screen.getByText("Rerank")).toBeTruthy();
    expect(screen.getByText("Missing fields")).toBeTruthy();
    expect(screen.getByText(/84\.2ms/)).toBeTruthy();
    expect(screen.queryAllByText("n/a")).toHaveLength(0);
    expect(screen.getAllByText("--").length).toBeGreaterThanOrEqual(2);
    expect(container.querySelector("select")).toBe(null);
  });

  it("renders fixed recall trace chrome from dashboard i18n", () => {
    bridge.getLocale.mockReturnValue("zh-CN");

    const { container } = render(<RecallTracePanel showToast={showToast} />);

    expect(screen.getByText("召回链路")).toBeTruthy();
    expect(screen.getByLabelText("查询")).toBeTruthy();
    expect(screen.getByPlaceholderText("输入要追踪的查询...")).toBeTruthy();
    expect(screen.getByText("聊天类型")).toBeTruthy();
    expect(screen.getByRole("button", { name: /追踪/ })).toBeTruthy();
    expect(container.querySelector("select")).toBe(null);
    expect(screen.queryByText("Recall trace")).toBe(null);
  });

  it("does not submit blank queries", () => {
    render(<RecallTracePanel showToast={showToast} />);

    fireEvent.click(screen.getByRole("button", { name: /Trace|追踪/ }));

    expect(bridge.apiPost).not.toHaveBeenCalled();
    expect(showToast).not.toHaveBeenCalled();
  });

  it("loads one persisted trace by id without posting the id as a query", async () => {
    bridge.apiGet.mockResolvedValue(ok(persistedTrace("trace-persisted")));

    render(
      <RecallTracePanel
        showToast={showToast}
        navigationTarget={{
          requestId: 1,
          tab: "recallTrace",
          traceId: "trace&id=unsafe",
        }}
      />,
    );

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith(
        "page/recall/trace/detail",
        { trace_id: "trace&id=unsafe" },
      );
    });
    expect(bridge.apiGet).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("trace-persisted")).toBeTruthy();
    expect(bridge.apiPost).not.toHaveBeenCalled();
  });

  it("loads one persisted trace when the bridge synchronously provides its context", async () => {
    bridge.apiGet.mockResolvedValue(ok(persistedTrace("trace-persisted")));
    bridge.onContext.mockImplementation((handler: (context: AstrBotContext) => void) => {
      handler({
        pluginName: "memora",
        displayName: "Memora",
        locale: "en-US",
        isDark: false,
      });
      return vi.fn();
    });

    render(
      <RecallTracePanel
        showToast={showToast}
        navigationTarget={{
          requestId: 1,
          tab: "recallTrace",
          traceId: "trace-persisted",
        }}
      />,
    );

    expect(await screen.findByText("trace-persisted")).toBeTruthy();
    expect(bridge.apiGet).toHaveBeenCalledTimes(1);
  });

  it("ignores a late persisted trace after the navigation target is replaced", async () => {
    const first = deferred<ReturnType<typeof ok>>();
    bridge.apiGet
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce(ok(persistedTrace("trace-fresh")));

    const { rerender } = render(
      <RecallTracePanel
        showToast={showToast}
        navigationTarget={{ requestId: 1, tab: "recallTrace", traceId: "trace-stale" }}
      />,
    );
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledTimes(1));

    rerender(
      <RecallTracePanel
        showToast={showToast}
        navigationTarget={{ requestId: 2, tab: "recallTrace", traceId: "trace-fresh" }}
      />,
    );
    expect(await screen.findByText("trace-fresh")).toBeTruthy();

    await act(async () => {
      first.resolve(ok(persistedTrace("trace-stale")));
      await first.promise;
    });

    expect(screen.queryByText("trace-stale")).toBeNull();
    expect(screen.getByText("trace-fresh")).toBeTruthy();
  });

  it("renders production results, fact alignment, source state, filter summary, and stage status", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      ...persistedTrace("trace-production"),
      stages: [
        { name: "request", duration_ms: 0, candidate_count: 0, metadata: {}, status: "skipped" },
        { name: "retrieval", duration_ms: 8.2, candidate_count: 6, metadata: {}, status: "completed" },
      ],
      results: [{
        rank: 1,
        initial_score: 0.58,
        final_score: 0.74,
        score_contributions: [],
        metadata: { memory_type: "episodic", status: "active" },
      }],
      metadata: { debug_trace_available: false, trace_kind: "production" },
      injection: {
        candidate_count: 6,
        selected_count: 3,
        injected_count: 2,
        routing_mode: "auto",
        outcome: "injected",
      },
      fact_alignment: { aligned: 2, misaligned: 1, undeterminable: 3 },
      source_status: "not_assessed",
      filter_summary: [
        { stage: "retrieval", reason: "privacy", count: 2 },
        { stage: "query", reason: "mark_write", count: 1 },
        { stage: "recall", reason: "stale", count: 1 },
        { stage: "future_stage", reason: "future_reason", count: 5 },
      ],
    }));

    render(
      <RecallTracePanel
        showToast={showToast}
        navigationTarget={{ requestId: 1, tab: "recallTrace", traceId: "trace-production" }}
      />,
    );

    expect(await screen.findByText("Production snapshot")).toBeTruthy();
    expect(screen.getByText("#1")).toBeTruthy();
    expect(screen.getByText(/memory_type: episodic/)).toBeTruthy();
    expect(screen.getByText(/status: active/)).toBeTruthy();
    expect(screen.getByText("Successfully injected").parentElement?.textContent).toContain("2");
    expect(screen.getByText("Candidates").parentElement?.textContent).toContain("6");
    expect(screen.getByText("Facts aligned")).toBeTruthy();
    expect(screen.getByText("Facts misaligned")).toBeTruthy();
    expect(screen.getByText("Cannot determine")).toBeTruthy();
    expect(screen.getByText("Not assessed")).toBeTruthy();
    expect(screen.getAllByText("Request").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Retrieval").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Query").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Recall").length).toBeGreaterThan(0);
    expect(screen.getByText("future_reason")).toBeTruthy();
    expect(screen.getByText("future_stage")).toBeTruthy();
    expect(screen.getByText("Privacy filter")).toBeTruthy();
    expect(screen.getByText("Marked for write")).toBeTruthy();
    expect(screen.getByText("Stale candidate")).toBeTruthy();
    expect(screen.getAllByText("1").length).toBeGreaterThan(0);
    expect(screen.queryByText(/trace_kind/)).toBeNull();
    expect(bridge.apiPost).not.toHaveBeenCalled();
  });

  it("renders unknown source status distinctly from not assessed", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      ...persistedTrace("trace-source-unknown"),
      metadata: { trace_kind: "production" },
      source_status: "unknown",
    }));

    render(
      <RecallTracePanel
        showToast={showToast}
        navigationTarget={{ requestId: 2, tab: "recallTrace", traceId: "trace-source-unknown" }}
      />,
    );

    expect(await screen.findByText("Unknown")).toBeTruthy();
    expect(screen.queryByText("Not assessed")).toBeNull();
  });

  it("labels a manually traced result as preview without an injection summary", async () => {
    render(<RecallTracePanel showToast={showToast} />);

    fireEvent.change(screen.getByLabelText(/Query|查询/), { target: { value: "coffee" } });
    fireEvent.click(screen.getByRole("button", { name: /Trace|追踪/ }));

    expect(await screen.findByText("Manual preview")).toBeTruthy();
    expect(screen.queryByText("Production snapshot")).toBeNull();
    expect(screen.queryByText("Injection result for this request")).toBeNull();
  });

  it("renders a stable unavailable state instead of an empty trace", async () => {
    bridge.apiGet.mockResolvedValue({
      status: "error",
      message: "trace_unavailable",
      code: "trace_unavailable",
    });

    render(
      <RecallTracePanel
        showToast={showToast}
        navigationTarget={{ requestId: 1, tab: "recallTrace", traceId: "trace-missing" }}
      />,
    );

    expect(
      await screen.findByText("No trace is available for this correlation code"),
    ).toBeTruthy();
    expect(screen.queryByText("No filtered candidates")).toBeNull();
    expect(screen.queryByText("trace-missing")).toBeNull();
    expect(showToast).not.toHaveBeenCalled();
  });

  it("renders a failure state and toast when the detail read fails", async () => {
    bridge.apiGet.mockResolvedValue({
      status: "error",
      message: "recall_trace_detail_failed",
    });

    render(
      <RecallTracePanel
        showToast={showToast}
        navigationTarget={{ requestId: 1, tab: "recallTrace", traceId: "trace-broken" }}
      />,
    );

    expect(await screen.findByText("Failed to load trace detail")).toBeTruthy();
    expect(screen.queryByText("No trace is available for this correlation code")).toBeNull();
    expect(showToast).toHaveBeenCalledTimes(1);
  });
});
