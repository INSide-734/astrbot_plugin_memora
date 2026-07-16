import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RecallTracePanel } from "./RecallTracePanel";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale: ReturnType<typeof vi.fn>;
  getI18n: ReturnType<typeof vi.fn>;
  t: ReturnType<typeof vi.fn>;
}

function ok<T>(data: T) {
  return { status: "ok", data };
}

function persistedTrace(traceId: string, query = "persisted query") {
  return {
    trace_id: traceId,
    query,
    total_ms: 12.3,
    stages: [],
    results: [],
    filtered: [],
    created_at: 1783150200,
    metadata: {},
  };
}

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
    };
    showToast = vi.fn();

    bridge.apiPost.mockResolvedValue(ok({
      trace_id: "trace-coffee",
      query: "用户喜欢喝什么咖啡",
      total_ms: 84.2,
      stages: [
        { name: "search_memories", duration_ms: 4.1, candidate_count: 0, metadata: { tokens: 6 } },
        { name: "bm25", duration_ms: 12.5, candidate_count: 7, metadata: { index: "atom_bm25" } },
      ],
      results: [
        {
          doc_id: "mem-coffee",
          rank: 1,
          initial_score: 0.71,
          final_score: 0.93,
          score_contributions: [
            { source: "bm25", score: 0.62, weight: 0.35, explanation: "keyword match" },
            { source: "emotion_boost", score: 0.21, weight: 0.2, explanation: "情绪偏好提升" },
          ],
          graph_paths: [
            { nodes: ["用户", "咖啡偏好"], edges: ["preference"], score: 0.72, metadata: { hop_count: 1 } },
          ],
          metadata: { type: "preference", session_id: "sess-coffee" },
        },
      ],
      filtered: [
        { doc_id: "mem-stale", reason: "low_score", stage: "rerank", score: 0.12, metadata: { threshold: 0.2 } },
        { doc_id: "mem-empty", reason: "missing_fields", metadata: {} },
      ],
      created_at: 1783150200,
      metadata: { provider: "mock" },
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

  it("submits clamped trace payload and renders stages contributions and filtered reasons", async () => {
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
        session_id: "",
        user_id: "",
        chat_type: "private",
        chain_depth: 5,
      });
    });

    await waitFor(() => {
      expect(screen.getAllByText(/BM25/).length > 0).toBeTruthy();
    });
    expect(screen.getByText("Emotion boost")).toBeTruthy();
    expect(screen.getByText("Memory search")).toBeTruthy();
    expect(screen.getByText(/mem-coffee/)).toBeTruthy();
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
});
