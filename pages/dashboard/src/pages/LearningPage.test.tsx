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

describe("LearningPage", () => {
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
    const localeSpy = vi.spyOn(Date.prototype, "toLocaleString");
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
        return Promise.resolve(ok({
          hit_rate: 0.83,
          avg_quality: 0.81234,
          total_trials: 18,
          total_corrections: 4,
          parameters: {
            retrieval_weight: 0.8,
            style_bias: 0.35,
          },
          history: [
            { timestamp: "2026-06-28T10:30:00Z", action: "weight_adjust", detail: "Raised retrieval weight" },
            { timestamp: "2026-06-27T10:30:00Z", action: "vendor_action", detail: "Vendor-defined action" },
          ],
        }));
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
    expect(page.querySelector('[data-slot="metric-grid"]')).toBeTruthy();

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/groups", {});
      expect(bridge.apiGet).toHaveBeenCalledWith("page/learning/status", {});
      expect(bridge.apiGet).toHaveBeenCalledWith("page/expression/patterns", { group_id: "group-1" });
    });

    expect(await screen.findByText("83.0%")).toBeTruthy();
    expect(screen.getByText("0.812")).toBeTruthy();
    expect(screen.getByText("18")).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
    expect(screen.getByText("Learned Parameters")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Learned Parameters" })).toBeTruthy();
    expect(screen.getByText("retrieval_weight")).toBeTruthy();
    expect(screen.getByText("0.80")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "retrieval_weight" })).toBeTruthy();
    expect(screen.getByText("Learning History")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Learning History" })).toBeTruthy();
    expect(screen.getByText("Weight adjustment")).toBeTruthy();
    expect(screen.getByText("vendor_action")).toBeTruthy();
    expect(screen.getByText("Raised retrieval weight")).toBeTruthy();
    expect(localeSpy).toHaveBeenCalledWith("en-US");
    expect(screen.getByText("Expression Patterns")).toBeTruthy();
    expect(screen.getByText("Greeting")).toBeTruthy();
    expect(screen.getByText("Formal greeting")).toBeTruthy();
    expect(screen.getByText("80%")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: /Greeting.*weight/i })).toBeTruthy();
    expect(document.querySelector('[data-slot="learning-details"]')?.className).toContain("xl:grid-cols-2");
    expect(screen.getAllByRole("progressbar").every((meter) => meter.getAttribute("data-slot") === "progress")).toBe(true);
    expect(screen.getByText("6")).toBeTruthy();
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
          return Promise.resolve(ok({
            hit_rate: 0.5,
            avg_quality: 0.6,
            total_trials: 10,
            total_corrections: 2,
          }));
        }
        return Promise.resolve(ok({
          hit_rate: 0.92,
          avg_quality: 0.95,
          total_trials: 11,
          total_corrections: 2,
        }));
      }
      if (path === "page/expression/patterns") {
        return Promise.resolve(ok({ patterns: [] }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<LearningPage showToast={showToast} />);

    expect(await screen.findByText("50.0%")).toBeTruthy();
    expect(screen.getByText("No expression patterns")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /reset learning/i }));

    const dialog = await screen.findByRole("dialog", { name: /reset learning/i });
    expect(dialog.textContent).toContain("Reset all learned parameters to defaults?");
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
    expect(showToast).toHaveBeenCalledWith("Learning parameters reset!");

    await waitFor(() => {
      expect(screen.getByText("92.0%")).toBeTruthy();
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
        return Promise.resolve(ok({
          hit_rate: 0.75,
          avg_quality: 0.81,
          total_trials: 9,
          total_corrections: 1,
        }));
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

    const hiddenSelectInput = document.querySelector('input[aria-hidden="true"]') as HTMLInputElement | null;
    if (!hiddenSelectInput) throw new Error("expected hidden group select input");
    fireEvent.change(hiddenSelectInput, { target: { value: "group-2" } });

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/expression/patterns", { group_id: "group-2" });
    });

    expect(await screen.findByText("Casual wrap-up")).toBeTruthy();
    expect(screen.getByText("Closing")).toBeTruthy();
    expect(screen.getByText("55%")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
  });

  it("keeps learning stats and confirmation context when reset fails", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [] }));
      if (path === "page/learning/status") return Promise.resolve(ok({ hit_rate: 0.5, avg_quality: 0.6, total_trials: 10, total_corrections: 2 }));
      return Promise.resolve(ok({ patterns: [] }));
    });
    let resolveReset!: (value: { status: "error"; message: string }) => void;
    bridge.apiPost.mockReturnValue(new Promise((resolve) => { resolveReset = resolve; }));

    render(<LearningPage showToast={showToast} />);
    expect(await screen.findByText("50.0%")).toBeTruthy();
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
    expect(screen.getByText("50.0%")).toBeTruthy();
  });

  it("keeps the latest group expressions when responses resolve out of order", async () => {
    const group1 = deferred<ReturnType<typeof ok<{ patterns: Array<Record<string, unknown>> }>>>();
    const group2 = deferred<ReturnType<typeof ok<{ patterns: Array<Record<string, unknown>> }>>>();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [{ group_id: "group-1", message_count: 1 }, { group_id: "group-2", message_count: 1 }] }));
      if (path === "page/learning/status") return Promise.resolve(ok({ hit_rate: 0.5 }));
      if (path === "page/expression/patterns") return params.group_id === "group-1" ? group1.promise : group2.promise;
      return Promise.resolve(ok({}));
    });
    render(<LearningPage showToast={showToast} />);
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith("page/expression/patterns", { group_id: "group-1" }));
    const input = document.querySelector('input[aria-hidden="true"]') as HTMLInputElement;
    fireEvent.change(input, { target: { value: "group-2" } });
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith("page/expression/patterns", { group_id: "group-2" }));
    await act(async () => { group2.resolve(ok({ patterns: [{ pattern_id: 2, group_id: "group-2", situation: "Second", expression: "group two latest", weight: 0.7, usage_count: 2 }] })); });
    expect(await screen.findByText("group two latest")).toBeTruthy();
    await act(async () => { group1.resolve(ok({ patterns: [{ pattern_id: 1, group_id: "group-1", situation: "First", expression: "group one stale", weight: 0.4, usage_count: 1 }] })); });
    expect(screen.getByText("group two latest")).toBeTruthy();
    expect(screen.queryByText("group one stale")).toBe(null);
  });

  it("ignores a stats response started before a successful reset refresh", async () => {
    const staleStats = deferred<ReturnType<typeof ok<Record<string, number>>>>();
    const freshStats = deferred<ReturnType<typeof ok<Record<string, number>>>>();
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
    await act(async () => { staleStats.resolve(ok({ hit_rate: 0.5, avg_quality: 0.6, total_trials: 1, total_corrections: 0 })); });
    expect(screen.queryByText("50.0%")).toBe(null);
    expect(screen.getByText(/Loading|加载|Загрузка/i)).toBeTruthy();
    await act(async () => { freshStats.resolve(ok({ hit_rate: 0.92, avg_quality: 0.95, total_trials: 2, total_corrections: 0 })); });
    expect(await screen.findByText("92.0%")).toBeTruthy();
    expect(screen.queryByText("50.0%")).toBe(null);
  });
});
