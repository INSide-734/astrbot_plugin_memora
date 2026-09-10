import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LearningPage } from "./LearningPage";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale?: ReturnType<typeof vi.fn>;
  getI18n?: ReturnType<typeof vi.fn>;
  t?: ReturnType<typeof vi.fn>;
}

function ok<T>(data: T) {
  return { status: "ok", data };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

/** 构造生产 learning/status DTO，并允许单测覆盖状态计数。 */
function learningStatus(overrides: Record<string, unknown> = {}) {
  return {
    enabled: true,
    available: true,
    candidate_count: 1,
    ready_count: 1,
    rejected_count: 0,
    published_count: 0,
    reasons: ["candidate"],
    current: { document_route_weight: 0.65, graph_route_weight: 0.35 },
    baseline: { document_route_weight: 0.65, graph_route_weight: 0.35 },
    candidates: [{
      proposed_document_weight: 0.69,
      proposed_graph_weight: 0.31,
      delta_from_baseline: 0.04,
      accepted_count: 6,
      independent_window_count: 3,
      decayed_support: 0.82,
      status: "ready_for_review",
      reason_code: "candidate",
    }],
    ...overrides,
  };
}

describe("LearningPage", () => {
  let bridge: BridgeMock;
  let showToast: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    bridge = {
      apiGet: vi.fn(),
      apiPost: vi.fn(),
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
    };
    showToast = vi.fn();

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

  it("loads learning stats, groups, and expression patterns", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [
            { group_id: "group-1", message_count: 12 },
            { group_id: "group-2", message_count: 8 },
          ],
        }));
      }
      if (path === "page/learning/status") {
        return Promise.resolve(ok(learningStatus({
          candidate_count: 2,
          ready_count: 1,
          rejected_count: 1,
          current: { document_route_weight: 0.61, graph_route_weight: 0.39 },
          baseline: { document_route_weight: 0.65, graph_route_weight: 0.35 },
          reasons: ["candidate", "insufficient_evidence"],
          candidates: [
            {
              proposed_document_weight: 0.69,
              proposed_graph_weight: 0.31,
              delta_from_baseline: 0.04,
              accepted_count: 6,
              independent_window_count: 3,
              decayed_support: 0.82,
              status: "ready_for_review",
              reason_code: "candidate",
            },
            {
              proposed_document_weight: 0.65,
              proposed_graph_weight: 0.35,
              delta_from_baseline: 0,
              accepted_count: 1,
              independent_window_count: 1,
              decayed_support: 0.2,
              status: "rejected",
              reason_code: "insufficient_evidence",
            },
          ],
        })));
      }
      if (path === "page/expression/patterns") {
        return Promise.resolve(ok({
          patterns: [
            {
              pattern_id: 1,
              group_id: params.group_id,
              situation: "Greeting",
              expression: "Formal greeting",
              weight: 0.8,
              usage_count: 6,
            },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<LearningPage showToast={showToast} />);

    const page = screen.getByRole("region", { name: /Learning|学习/ });
    expect(page.getAttribute("data-layout")).toBe("standard");
    expect(page.querySelector('[data-slot="page-header"]')).toBeTruthy();
    expect(page.querySelector('[data-state="loading"]')).toBeTruthy();

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/groups", {});
      expect(bridge.apiGet).toHaveBeenCalledWith("page/learning/status", {});
      expect(bridge.apiGet).toHaveBeenCalledWith("page/expression/patterns", {
        group_id: "group-1",
        sort_by: "weight",
        sort_order: "desc",
      });
    });

    expect(await screen.findByText("Current runtime weights")).toBeTruthy();
    expect(page.querySelector('[data-slot="metric-grid"]')).toBeTruthy();
    expect(screen.getByText("Shadow baseline")).toBeTruthy();
    expect(screen.getByText("Shadow candidates")).toBeTruthy();
    expect(screen.getByText("Ready for manual review")).toBeTruthy();
    expect(screen.getByText("Insufficient evidence")).toBeTruthy();
    expect(screen.getByText("61%")).toBeTruthy();
    expect(screen.getAllByText("65%").length).toBeGreaterThan(0);
    expect(screen.getByText("82%")).toBeTruthy();
    expect(screen.getByText("Expression Patterns")).toBeTruthy();
    expect(screen.getByText("Greeting")).toBeTruthy();
    expect(screen.getByText("Formal greeting")).toBeTruthy();
    expect(screen.getByText("80%")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: /Greeting.*weight/i })).toBeTruthy();
    expect(document.querySelector('[data-slot="learning-details"]')?.className).toContain("xl:grid-cols-2");
    expect(screen.getAllByRole("progressbar").every((meter) => meter.getAttribute("data-slot") === "progress")).toBe(true);
    expect(screen.getAllByText("6").length).toBeGreaterThan(0);
  });

  it("sorts expression patterns on the server without persisting transient state", async () => {
    const initial = deferred<ReturnType<typeof ok<{ patterns: Array<Record<string, unknown>> }>>>();
    const sorted = deferred<ReturnType<typeof ok<{ patterns: Array<Record<string, unknown>> }>>>();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({ groups: [{ group_id: "group-1", message_count: 1 }] }));
      }
      if (path === "page/learning/status") return Promise.resolve(ok(learningStatus()));
      if (path === "page/expression/patterns") {
        return params.sort_by === "usage_count" ? sorted.promise : initial.promise;
      }
      return Promise.resolve(ok({}));
    });

    render(<LearningPage showToast={showToast} />);
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith(
      "page/expression/patterns",
      { group_id: "group-1", sort_by: "weight", sort_order: "desc" },
    ));

    const storedPreferences = localStorage.getItem("memora.table.expression-patterns.v1");
    fireEvent.click(screen.getByRole("button", { name: /Sort Uses ascending/i }));
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith(
      "page/expression/patterns",
      { group_id: "group-1", sort_by: "usage_count", sort_order: "desc" },
    ));
    expect(localStorage.getItem("memora.table.expression-patterns.v1")).toBe(storedPreferences);

    await act(async () => {
      sorted.resolve(ok({ patterns: [{
        pattern_id: 2,
        group_id: "group-1",
        situation: "Sorted",
        expression: "latest sorted response",
        weight: 0.7,
        usage_count: 8,
        created_at: 10,
        last_used_at: 20,
      }] }));
    });
    expect(await screen.findByText("latest sorted response")).toBeTruthy();

    await act(async () => {
      initial.resolve(ok({ patterns: [{
        pattern_id: 1,
        group_id: "group-1",
        situation: "Stale",
        expression: "stale default response",
        weight: 0.9,
        usage_count: 1,
        created_at: 5,
        last_used_at: 6,
      }] }));
    });
    expect(screen.getByText("latest sorted response")).toBeTruthy();
    expect(screen.queryByText("stale default response")).toBeNull();
    expect(screen.queryByRole("button", { name: /Row actions/i })).toBeNull();
    expect(screen.queryByRole("navigation", { name: /pagination/i })).toBeNull();
  });

  it("requires confirmation before resetting learning and refreshes stats after success", async () => {
    let statusCalls = 0;
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [{ group_id: "group-1", message_count: 12 }],
        }));
      }
      if (path === "page/learning/status") {
        statusCalls += 1;
        if (statusCalls === 1) {
          return Promise.resolve(ok(learningStatus({ candidate_count: 1 })));
        }
        return Promise.resolve(ok(learningStatus({
          candidate_count: 0,
          ready_count: 0,
          candidates: [],
          reasons: [],
        })));
      }
      if (path === "page/expression/patterns") {
        return Promise.resolve(ok({ patterns: [] }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<LearningPage showToast={showToast} />);

    expect(await screen.findByText("Shadow candidates")).toBeTruthy();
    expect(screen.getByText("No expression patterns")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /reset learning/i }));

    const dialog = await screen.findByRole("dialog", { name: /reset learning/i });
    expect(dialog.textContent).toContain("Production weights will not change.");
    expect(bridge.apiPost).not.toHaveBeenCalledWith("page/learning/reset", {});

    const confirm = within(dialog).getByRole("button", { name: /reset learning/i });
    act(() => {
      confirm.click();
      confirm.click();
    });

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledTimes(1);
      expect(bridge.apiPost).toHaveBeenCalledWith("page/learning/reset", {});
    });
    expect(showToast).toHaveBeenCalledWith("Shadow learning state reset.");

    await waitFor(() => {
      expect(screen.getByText("No shadow candidates yet")).toBeTruthy();
    });
  });

  it("switches groups and refetches expression patterns for the selected group", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [
            { group_id: "group-1", message_count: 12 },
            { group_id: "group-2", message_count: 8 },
          ],
        }));
      }
      if (path === "page/learning/status") {
        return Promise.resolve(ok(learningStatus()));
      }
      if (path === "page/expression/patterns" && params.group_id === "group-1") {
        return Promise.resolve(ok({
          patterns: [
            {
              pattern_id: 1,
              group_id: "group-1",
              situation: "Greeting",
              expression: "Formal greeting",
              weight: 0.8,
              usage_count: 6,
            },
          ],
        }));
      }
      if (path === "page/expression/patterns" && params.group_id === "group-2") {
        return Promise.resolve(ok({
          patterns: [
            {
              pattern_id: 2,
              group_id: "group-2",
              situation: "Closing",
              expression: "Casual wrap-up",
              weight: 0.55,
              usage_count: 3,
            },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<LearningPage showToast={showToast} />);

    expect(await screen.findByText("Formal greeting")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Sort Uses ascending/i }));
    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/expression/patterns", {
        group_id: "group-1",
        sort_by: "usage_count",
        sort_order: "desc",
      });
    });

    const hiddenSelectInput = document.querySelector('input[aria-hidden="true"]') as HTMLInputElement | null;
    if (!hiddenSelectInput) throw new Error("expected hidden group select input");
    fireEvent.change(hiddenSelectInput, { target: { value: "group-2" } });

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/expression/patterns", {
        group_id: "group-2",
        sort_by: "weight",
        sort_order: "desc",
      });
    });

    expect(await screen.findByText("Casual wrap-up")).toBeTruthy();
    expect(screen.getByText("Closing")).toBeTruthy();
    expect(screen.getByText("55%")).toBeTruthy();
    expect(screen.getAllByText("3").length).toBeGreaterThan(0);
  });

  it("recovers autonomous learning status after retry", async () => {
    let statusCalls = 0;
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [] }));
      if (path === "page/learning/status") {
        statusCalls += 1;
        return statusCalls === 1
          ? Promise.reject(new Error("status unavailable"))
          : Promise.resolve(ok(learningStatus()));
      }
      return Promise.resolve(ok({ patterns: [] }));
    });

    render(<LearningPage showToast={showToast} />);

    expect(await screen.findByText("Could not load autonomous learning status")).toBeTruthy();
    expect(screen.getByText("status unavailable")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Shadow candidates")).toBeTruthy();
    expect(statusCalls).toBe(2);
    expect(screen.queryByText("status unavailable")).toBe(null);
  });

  it("keeps learning stats and confirmation context when reset fails", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [] }));
      if (path === "page/learning/status") return Promise.resolve(ok(learningStatus()));
      return Promise.resolve(ok({ patterns: [] }));
    });
    let resolveReset!: (value: { status: "error"; message: string }) => void;
    bridge.apiPost.mockReturnValue(new Promise((resolve) => { resolveReset = resolve; }));

    render(<LearningPage showToast={showToast} />);
    expect(await screen.findByText("Shadow candidates")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /reset learning/i }));
    const dialog = await screen.findByRole("dialog", { name: /reset learning/i });
    const confirm = within(dialog).getByRole("button", { name: /reset learning/i });
    act(() => {
      confirm.click();
      confirm.click();
    });
    expect(confirm).toHaveProperty("disabled", true);
    expect(confirm.textContent).toMatch(/reset learning…/i);

    await act(async () => { resolveReset({ status: "error", message: "reset unavailable" }); });
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("alert").textContent).toContain("reset unavailable");
    expect(screen.getByRole("dialog", { name: /reset learning/i })).toBeTruthy();
    expect(screen.getByText("Shadow candidates")).toBeTruthy();
  });

  it("keeps the latest group expressions when responses resolve out of order", async () => {
    const group1 = deferred<ReturnType<typeof ok<{ patterns: Array<Record<string, unknown>> }>>>();
    const group2 = deferred<ReturnType<typeof ok<{ patterns: Array<Record<string, unknown>> }>>>();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [{ group_id: "group-1", message_count: 1 }, { group_id: "group-2", message_count: 1 }] }));
      if (path === "page/learning/status") return Promise.resolve(ok(learningStatus()));
      if (path === "page/expression/patterns") return params.group_id === "group-1" ? group1.promise : group2.promise;
      return Promise.resolve(ok({}));
    });
    render(<LearningPage showToast={showToast} />);
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith("page/expression/patterns", {
      group_id: "group-1",
      sort_by: "weight",
      sort_order: "desc",
    }));
    const input = document.querySelector('input[aria-hidden="true"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: "group-2" } });
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith("page/expression/patterns", {
      group_id: "group-2",
      sort_by: "weight",
      sort_order: "desc",
    }));
    await act(async () => { group2.resolve(ok({ patterns: [{ pattern_id: 2, group_id: "group-2", situation: "Second", expression: "group two latest", weight: 0.7, usage_count: 2 }] })); });
    expect(await screen.findByText("group two latest")).toBeTruthy();
    await act(async () => { group1.resolve(ok({ patterns: [{ pattern_id: 1, group_id: "group-1", situation: "First", expression: "group one stale", weight: 0.4, usage_count: 1 }] })); });
    expect(screen.getByText("group two latest")).toBeTruthy();
    expect(screen.queryByText("group one stale")).toBe(null);
  });

  it("ignores a stats response started before a successful reset refresh", async () => {
    const staleStats = deferred<ReturnType<typeof ok<Record<string, unknown>>>>();
    const freshStats = deferred<ReturnType<typeof ok<Record<string, unknown>>>>();
    let statusCalls = 0;
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [] }));
      if (path === "page/learning/status") return ++statusCalls === 1 ? staleStats.promise : freshStats.promise;
      return Promise.resolve(ok({ patterns: [] }));
    });
    bridge.apiPost.mockResolvedValue(ok({}));
    render(<LearningPage showToast={showToast} />);
    fireEvent.click(screen.getByRole("button", { name: /reset learning/i }));
    const dialog = await screen.findByRole("dialog", { name: /reset learning/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /reset learning/i }));
    await waitFor(() => expect(statusCalls).toBe(2));
    await act(async () => { staleStats.resolve(ok(learningStatus({ candidate_count: 7 }))); });
    expect(screen.queryByText("7")).toBe(null);
    expect(screen.getByText(/Loading|加载|Загрузка/i)).toBeTruthy();
    await act(async () => { freshStats.resolve(ok(learningStatus({ candidate_count: 2 }))); });
    expect(await screen.findByText("2")).toBeTruthy();
    expect(screen.queryByText("7")).toBe(null);
  });
});
