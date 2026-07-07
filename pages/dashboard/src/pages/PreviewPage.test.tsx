import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PreviewPage } from "./PreviewPage";

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

function getStorageMetricValue(label: string) {
  const metricLabel = screen.getByText(label);
  const metricContainer = metricLabel.parentElement;
  if (!metricContainer) {
    throw new Error(`expected storage metric container for ${label}`);
  }

  const valueNode = metricContainer.querySelector(".text-lg");
  if (!valueNode) {
    throw new Error(`expected storage metric value node for ${label}`);
  }

  return valueNode.textContent;
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

  it("loads aggregated preview cards and renders quick navigation links", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") {
        return Promise.resolve(ok({
          total_memories: 12,
          active_count: 8,
          archived_count: 4,
          graph_nodes: 5,
          graph_edges: 7,
        }));
      }
      if (path === "page/profiles") {
        return Promise.resolve(ok({ total: 3 }));
      }
      if (path === "page/knowledge") {
        return Promise.resolve(ok({ total: 9 }));
      }
      if (path === "page/notes") {
        return Promise.resolve(ok({ total: 6, active_count: 5 }));
      }
      return Promise.resolve(ok({}));
    });

    render(<PreviewPage showToast={showToast} />);

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/stats", {});
      expect(bridge.apiGet).toHaveBeenCalledWith("page/profiles", { limit: "1" });
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge", { limit: "1" });
      expect(bridge.apiGet).toHaveBeenCalledWith("page/notes", { limit: "1" });
    });

    expect(await screen.findByText("Quick Actions")).toBeTruthy();
    expect(screen.getByText("Storage Summary")).toBeTruthy();

    const storageCard = screen.getByText("Storage Summary").closest("div");
    if (!storageCard) throw new Error("expected storage summary card");

    expect(within(storageCard).getByText("Memory Total")).toBeTruthy();
    expect(within(storageCard).getByText("Graph Edges")).toBeTruthy();
    expect(within(storageCard).getByText("12")).toBeTruthy();
    expect(within(storageCard).getByText("8")).toBeTruthy();
    expect(within(storageCard).getByText("4")).toBeTruthy();
    expect(within(storageCard).getByText("5")).toBeTruthy();
    expect(within(storageCard).getByText("7")).toBeTruthy();

    expect(screen.getByRole("link", { name: /knowledge graph/i }).getAttribute("href")).toBe("#/graph");
    expect(screen.getByRole("link", { name: /^memories$/i }).getAttribute("href")).toBe("#/memory");
    expect(screen.getByRole("link", { name: /recall test/i }).getAttribute("href")).toBe("#/recall");
    expect(screen.getByRole("link", { name: /^system$/i }).getAttribute("href")).toBe("#/system");
  });

  it("keeps rendering available preview sections when some endpoints fail and fills missing data with zeroes", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") {
        return Promise.resolve(ok({
          total_memories: 4,
          active_count: 3,
          archived_count: 1,
          graph_nodes: 2,
          graph_edges: 1,
        }));
      }
      if (path === "page/profiles") {
        return Promise.reject(new Error("profiles unavailable"));
      }
      if (path === "page/knowledge") {
        return Promise.resolve(ok({ total: 0 }));
      }
      if (path === "page/notes") {
        return Promise.reject(new Error("notes unavailable"));
      }
      return Promise.resolve(ok({}));
    });

    render(<PreviewPage showToast={showToast} />);

    expect(await screen.findByText("Quick Actions")).toBeTruthy();
    expect(screen.getByText("User Profiles")).toBeTruthy();
    expect(screen.getByText("Knowledge Base")).toBeTruthy();
    expect(screen.getByText("Notes")).toBeTruthy();

    const zeroValues = screen.getAllByText("0");
    expect(zeroValues.length).toBeGreaterThan(1);
    expect(showToast).not.toHaveBeenCalled();
  });

  it("refreshes preview data on demand and updates the rendered stats", async () => {
    let statsCallCount = 0;
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") {
        statsCallCount += 1;
        if (statsCallCount === 1) {
          return Promise.resolve(ok({
            total_memories: 5,
            active_count: 4,
            archived_count: 1,
            graph_nodes: 2,
            graph_edges: 3,
          }));
        }
        return Promise.resolve(ok({
          total_memories: 9,
          active_count: 7,
          archived_count: 2,
          graph_nodes: 6,
          graph_edges: 8,
        }));
      }
      if (path === "page/profiles") return Promise.resolve(ok({ total: 1 }));
      if (path === "page/knowledge") return Promise.resolve(ok({ total: 2 }));
      if (path === "page/notes") return Promise.resolve(ok({ total: 3, active_count: 3 }));
      return Promise.resolve(ok({}));
    });

    render(<PreviewPage showToast={showToast} />);

    await screen.findByText("Storage Summary");
    expect(getStorageMetricValue("Memory Total")).toBe("5");
    expect(getStorageMetricValue("Graph Edges")).toBe("3");

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => {
      expect(getStorageMetricValue("Memory Total")).toBe("9");
      expect(getStorageMetricValue("Graph Edges")).toBe("8");
    });
  });
});
