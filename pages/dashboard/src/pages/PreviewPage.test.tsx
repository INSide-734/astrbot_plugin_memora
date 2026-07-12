import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PreviewPage } from "./PreviewPage";

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

function dailyCounts() {
  return Array.from({ length: 90 }, (_, index) => ({
    date: new Date(Date.UTC(2026, 3, 14 + index)).toISOString().slice(0, 10),
    count: index < 60 ? 0 : index - 59,
  }));
}

function statsPayload(total = 20) {
  return {
    total_memories: total,
    active_count: 12,
    archived_count: 5,
    deleted_count: 3,
    graph_nodes: 7,
    graph_edges: 5,
    graph_entries: 4,
    atom_count: 18,
    avg_importance: 0.64,
    status_breakdown: { active: 12, archived: 5, deleted: 3 },
    atom_breakdown: { fact: 6, preference: 5, event: 3, relation: 2, summary: 1, other_type: 1 },
    importance_distribution: {
      "0-1": 0, "1-2": 1, "2-3": 1, "3-4": 2, "4-5": 3,
      "5-6": 4, "6-7": 4, "7-8": 3, "8-9": 1, "9-10": 1,
    },
    recent_sessions: [
      { session_id: "group-alpha-with-a-long-identifier", message_count: 9 },
      { session_id: "group-beta", message_count: 5 },
    ],
    daily_memory_counts: dailyCounts(),
  };
}

describe("PreviewPage", () => {
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
    Object.defineProperty(window, "AstrBotPluginPage", { configurable: true, value: bridge });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", { configurable: true, value: undefined });
  });

  function mockOverview(stats = statsPayload()) {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok(stats));
      if (path === "page/profiles") return Promise.resolve(ok({ total: 3 }));
      if (path === "page/knowledge") return Promise.resolve(ok({ total: 9 }));
      if (path === "page/notes") return Promise.resolve(ok({ total: 6, active_count: 5 }));
      return Promise.resolve(ok({}));
    });
  }

  it("renders the operational overview with six KPIs and accessible analytics", async () => {
    mockOverview();
    const { container } = render(<PreviewPage showToast={showToast} />);

    const growthHeading = await screen.findByRole("heading", { name: "Memory growth" });
    expect(container.querySelectorAll('[data-slot="overview-kpi"]')).toHaveLength(6);
    expect(screen.getByText("60%", { selector: "[data-kpi-value]" })).toBeTruthy();
    expect(screen.getByText("6.4 / 10")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Daily memory growth chart" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Memory status composition" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Memory atom type distribution" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Memory importance distribution" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Module assets" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Active sessions" })).toBeTruthy();
    expect(screen.getByText("group-alpha-with-a-long-identifier")).toBeTruthy();
    expect(screen.getByText("Other")).toBeTruthy();
    expect(screen.getByText(/Updated/)).toBeTruthy();

    const growthCard = growthHeading.closest('[data-slot="growth-panel"]');
    if (!growthCard) throw new Error("expected growth card");
    const growthCardElement = growthCard as HTMLElement;
    expect(within(growthCardElement).getByText("465")).toBeTruthy();
    expect(within(growthCardElement).getByText("15.5")).toBeTruthy();
    expect(within(growthCardElement).getByText("2026-07-12")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Quick links" }));
    expect((await screen.findByRole("menuitem", { name: "Knowledge Graph" })).getAttribute("href")).toBe("#/graph");
    expect(screen.getByRole("menuitem", { name: "Memories" }).getAttribute("href")).toBe("#/memory");
    expect(screen.getByRole("menuitem", { name: "Recall Test" }).getAttribute("href")).toBe("#/recall");
    expect(screen.getByRole("menuitem", { name: "System" }).getAttribute("href")).toBe("#/system");
  });

  it("switches 7, 30 and 90 day ranges without refetching", async () => {
    mockOverview();
    render(<PreviewPage showToast={showToast} />);

    await screen.findByRole("heading", { name: "Memory growth" });
    const statsCallsBefore = bridge.apiGet.mock.calls.filter(([path]) => path === "page/stats").length;

    fireEvent.click(screen.getByRole("button", { name: "7 days" }));
    expect(screen.getByRole("button", { name: "7 days" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "90 days" }));
    expect(screen.getByRole("button", { name: "90 days" }).getAttribute("aria-pressed")).toBe("true");

    expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/stats")).toHaveLength(statsCallsBefore);
  });

  it("marks failed secondary metrics unavailable while keeping successful analytics", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok(statsPayload()));
      if (path === "page/profiles") return Promise.reject(new Error("profiles unavailable"));
      if (path === "page/knowledge") return Promise.resolve(ok({ total: 9 }));
      if (path === "page/notes") return Promise.reject(new Error("notes unavailable"));
      return Promise.resolve(ok({}));
    });

    render(<PreviewPage showToast={showToast} />);

    expect(await screen.findByText("Some data could not be updated")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Memory growth" })).toBeTruthy();
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Knowledge Base")).toBeTruthy();
  });

  it("shows a retryable error when initial statistics fail", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.reject(new Error("stats unavailable"));
      return Promise.resolve(ok({ total: 0 }));
    });

    render(<PreviewPage showToast={showToast} />);

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText("Overview unavailable")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("keeps previous data visible while a manual refresh is pending", async () => {
    let resolveRefresh: ((value: ReturnType<typeof ok>) => void) | undefined;
    let statsCalls = 0;
    mockOverview();
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") {
        statsCalls += 1;
        if (statsCalls === 1) return Promise.resolve(ok(statsPayload(20)));
        return new Promise((resolve) => { resolveRefresh = resolve; });
      }
      if (path === "page/profiles") return Promise.resolve(ok({ total: 3 }));
      if (path === "page/knowledge") return Promise.resolve(ok({ total: 9 }));
      if (path === "page/notes") return Promise.resolve(ok({ total: 6 }));
      return Promise.resolve(ok({}));
    });

    render(<PreviewPage showToast={showToast} />);
    expect(await screen.findByText("20", { selector: "[data-kpi-value]" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(screen.getByText("20", { selector: "[data-kpi-value]" })).toBeTruthy();
    resolveRefresh?.(ok(statsPayload(24)));

    await waitFor(() => {
      expect(screen.getByText("24", { selector: "[data-kpi-value]" })).toBeTruthy();
    });
  });
});
