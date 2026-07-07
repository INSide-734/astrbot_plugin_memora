import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type GraphEventHandler = (event?: { target?: { id?: string } }) => void;

function getGraphMockState() {
  const globalState = globalThis as typeof globalThis & {
    __graphMockState__?: {
      instances: Array<{
        config: Record<string, unknown>;
        handlers: Map<string, GraphEventHandler>;
        setData: (...args: unknown[]) => unknown;
        render: (...args: unknown[]) => Promise<void>;
        destroy: (...args: unknown[]) => unknown;
        focusElement: (...args: unknown[]) => unknown;
        getZoom: (...args: unknown[]) => number;
        on: (eventName: string, handler: GraphEventHandler) => void;
        emit: (eventName: string, event?: { target?: { id?: string } }) => void;
      }>;
    };
  };
  if (!globalState.__graphMockState__) {
    globalState.__graphMockState__ = { instances: [] };
  }
  return globalState.__graphMockState__;
}

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

async function loadGraphPage() {
  vi.resetModules();
  getGraphMockState().instances.length = 0;

  vi.doMock("@antv/g6", () => ({
    Graph: class GraphMock {
      config: Record<string, unknown>;
      handlers = new Map<string, GraphEventHandler>();
      setData = vi.fn();
      render = vi.fn().mockResolvedValue(undefined);
      destroy = vi.fn();
      focusElement = vi.fn();
      getZoom = vi.fn().mockReturnValue(1.75);

      constructor(config: Record<string, unknown>) {
        this.config = config;
        getGraphMockState().instances.push(this);
      }

      on(eventName: string, handler: GraphEventHandler) {
        this.handlers.set(eventName, handler);
      }

      emit(eventName: string, event?: { target?: { id?: string } }) {
        const handler = this.handlers.get(eventName);
        handler?.(event);
      }
    },
  }));

  return import("./GraphPage");
}

describe("GraphPage", () => {
  let bridge: BridgeMock;
  let showToast: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    document.documentElement.dataset.theme = "light";

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

    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      writable: true,
      value: null,
    });

    Object.defineProperty(HTMLElement.prototype, "requestFullscreen", {
      configurable: true,
      writable: true,
      value: vi.fn().mockResolvedValue(undefined),
    });

    Object.defineProperty(document, "exitFullscreen", {
      configurable: true,
      writable: true,
      value: vi.fn().mockResolvedValue(undefined),
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.doUnmock("@antv/g6");
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  it("loads overview stats and initial graph data on mount", async () => {
    const { GraphPage } = await loadGraphPage();

    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") {
        return Promise.resolve(ok({
          total_memories: 12,
          graph_nodes: 4,
          graph_edges: 5,
          sessions: { "session-a": { turns: 2 }, "session-b": { turns: 1 } },
        }));
      }
      if (path === "page/graph/search") {
        return Promise.resolve(ok({
          nodes: [
            { id: "node-1", label: "Alpha topic", type: "topic", memory_count: 3, degree: 2, entry_count: 1, weight: 0.9 },
            { id: "node-2", label: "Beta fact", type: "fact", memory_count: 1, degree: 1, entry_count: 2, weight: 0.7 },
          ],
          edges: [
            { source: "node-1", target: "node-2", type: "results_in", weight: 1 },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<GraphPage showToast={showToast} />);

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/stats", {});
      expect(bridge.apiGet).toHaveBeenCalledWith("page/graph/search", {});
    });

    expect(await screen.findByText("12")).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
    expect(screen.getByText("5")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();

    expect(getGraphMockState().instances).toHaveLength(1);
    const graph = getGraphMockState().instances[0];
    await waitFor(() => {
      expect(graph.setData).toHaveBeenCalledWith({
        nodes: [
          {
            id: "node-1",
            data: {
              label: "Alpha topic",
              type: "topic",
              weight: 0.9,
              memory_count: 3,
              degree: 2,
              entry_count: 1,
            },
          },
          {
            id: "node-2",
            data: {
              label: "Beta fact",
              type: "fact",
              weight: 0.7,
              memory_count: 1,
              degree: 1,
              entry_count: 2,
            },
          },
        ],
        edges: [
          {
            id: "e-node-1-node-2-0",
            source: "node-1",
            target: "node-2",
            data: {
              type: "results_in",
              weight: 1,
              label: "results_in",
            },
          },
        ],
      });
    });
    expect(graph.render).toHaveBeenCalled();

    graph.emit("viewport:change");
    expect(await screen.findByText("175%")).toBeTruthy();
  });

  it("submits graph searches, filters invalid edges, and opens the selected node detail panel", async () => {
    const { GraphPage } = await loadGraphPage();

    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/stats") {
        return Promise.resolve(ok({
          total_memories: 8,
          graph_nodes: 2,
          graph_edges: 1,
          sessions: {},
        }));
      }
      if (path === "page/graph/search" && Object.keys(params).length === 0) {
        return Promise.resolve(ok({
          nodes: [{ id: "seed-1", label: "Seed", type: "topic", memory_count: 1, degree: 1, entry_count: 1, weight: 0.4 }],
          edges: [],
        }));
      }
      if (path === "page/graph/search") {
        return Promise.resolve(ok({
          nodes: [
            { id: "42", label: "Deploy topic", type: "topic", memory_count: 6, degree: 4, entry_count: 3, weight: 1.2 },
            { id: "84", label: "Release fact", type: "fact", memory_count: 2, degree: 1, entry_count: 1, weight: 0.5 },
          ],
          edges: [
            { source: "42", target: "84", type: "caused_by", weight: 2 },
            { source: "42", target: "missing-node", type: "before", weight: 1 },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    render(<GraphPage showToast={showToast} />);

    expect(await screen.findByText("8")).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("Search entities, topics, or memories..."), {
      target: { value: "deployment" },
    });
    fireEvent.change(screen.getByPlaceholderText("Memory ID"), {
      target: { value: "mem-42" },
    });
    fireEvent.click(screen.getByRole("button", { name: /search graph/i }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/graph/search", {
        query: "deployment",
        memory_id: "mem-42",
      });
    });

    const graph = getGraphMockState().instances[0];
    await waitFor(() => {
      expect(graph.setData).toHaveBeenLastCalledWith({
        nodes: [
          {
            id: "42",
            data: {
              label: "Deploy topic",
              type: "topic",
              weight: 1.2,
              memory_count: 6,
              degree: 4,
              entry_count: 3,
            },
          },
          {
            id: "84",
            data: {
              label: "Release fact",
              type: "fact",
              weight: 0.5,
              memory_count: 2,
              degree: 1,
              entry_count: 1,
            },
          },
        ],
        edges: [
          {
            id: "e-42-84-0",
            source: "42",
            target: "84",
            data: {
              type: "caused_by",
              weight: 2,
              label: "caused_by",
            },
          },
        ],
      });
    });
    expect(warnSpy).toHaveBeenCalled();

    graph.emit("node:click", { target: { id: "42" } });

    expect(await screen.findByText("Deploy topic")).toBeTruthy();
    expect(screen.getByText("6")).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("1.20")).toBeTruthy();
    expect(graph.focusElement).toHaveBeenCalledWith("42", { duration: 500 });
  });

  it("re-applies the cached graph and removes out-of-range graph items when the time range changes", async () => {
    const { GraphPage } = await loadGraphPage();
    const nowSeconds = Math.floor(Date.now() / 1000);

    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") {
        return Promise.resolve(ok({
          total_memories: 2,
          graph_nodes: 3,
          graph_edges: 2,
          sessions: {},
        }));
      }
      if (path === "page/graph/search") {
        return Promise.resolve(ok({
          nodes: [
            { id: "recent", label: "Recent", type: "topic" },
            { id: "old", label: "Old", type: "fact" },
            { id: "anchor", label: "Anchor", type: "summary" },
          ],
          edges: [
            { source: "recent", target: "anchor", type: "before", weight: 1, timestamp: nowSeconds - 3600 },
            { source: "old", target: "anchor", type: "after", weight: 1, timestamp: nowSeconds - 72 * 3600 },
            { source: "old", target: "recent", type: "related", weight: 1 },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<GraphPage showToast={showToast} />);

    const graph = await waitFor(() => {
      const instance = getGraphMockState().instances[0];
      expect(instance.setData).toHaveBeenCalled();
      return instance;
    });

    const timeSliders = screen.getAllByRole("slider");
    fireEvent.change(timeSliders[1], { target: { value: "24" } });

    await waitFor(() => {
      expect(graph.setData).toHaveBeenLastCalledWith({
        nodes: [
          { id: "recent", data: { label: "Recent", type: "topic", weight: 1, memory_count: 0, degree: 0, entry_count: 0 } },
          { id: "old", data: { label: "Old", type: "fact", weight: 1, memory_count: 0, degree: 0, entry_count: 0 } },
          { id: "anchor", data: { label: "Anchor", type: "summary", weight: 1, memory_count: 0, degree: 0, entry_count: 0 } },
        ],
        edges: [
          {
            id: "e-recent-anchor-0",
            source: "recent",
            target: "anchor",
            data: {
              type: "before",
              weight: 1,
              label: undefined,
            },
          },
          {
            id: "e-old-recent-1",
            source: "old",
            target: "recent",
            data: {
              type: "related",
              weight: 1,
              label: undefined,
            },
          },
        ],
      });
    });
  });

  it("keeps in-range graph items when numeric timestamps are returned in milliseconds", async () => {
    const { GraphPage } = await loadGraphPage();
    const nowMs = Date.now();

    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") {
        return Promise.resolve(ok({
          total_memories: 1,
          graph_nodes: 2,
          graph_edges: 1,
          sessions: {},
        }));
      }
      if (path === "page/graph/search") {
        return Promise.resolve(ok({
          nodes: [
            { id: "memory", label: "Memory", type: "topic" },
            { id: "anchor", label: "Anchor", type: "summary" },
          ],
          edges: [
            {
              source: "memory",
              target: "anchor",
              type: "related",
              weight: 1,
              timestamp: nowMs - 10 * 24 * 3600 * 1000,
            },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<GraphPage showToast={showToast} />);

    const graph = await waitFor(() => {
      const instance = getGraphMockState().instances[0];
      expect(instance.setData).toHaveBeenCalled();
      return instance;
    });

    const timeSliders = screen.getAllByRole("slider");
    fireEvent.change(timeSliders[0], { target: { value: "1" } });

    await waitFor(() => {
      expect(graph.setData).toHaveBeenLastCalledWith({
        nodes: [
          { id: "memory", data: { label: "Memory", type: "topic", weight: 1, memory_count: 0, degree: 0, entry_count: 0 } },
          { id: "anchor", data: { label: "Anchor", type: "summary", weight: 1, memory_count: 0, degree: 0, entry_count: 0 } },
        ],
        edges: [
          {
            id: "e-memory-anchor-0",
            source: "memory",
            target: "anchor",
            data: {
              type: "related",
              weight: 1,
              label: undefined,
            },
          },
        ],
      });
    });
  });

  it("shows the error state on failed graph load, retries successfully, and supports fullscreen toggling", async () => {
    const { GraphPage } = await loadGraphPage();

    let graphRequestCount = 0;
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") {
        return Promise.resolve(ok({
          total_memories: 3,
          graph_nodes: 0,
          graph_edges: 0,
          sessions: {},
        }));
      }
      if (path === "page/graph/search") {
        graphRequestCount += 1;
        if (graphRequestCount === 1) {
          return Promise.resolve({ status: "error", message: "graph offline" });
        }
        return Promise.resolve(ok({
          nodes: [{ id: "recover-1", label: "Recovered node", type: "summary", memory_count: 2, degree: 1, entry_count: 1, weight: 0.8 }],
          edges: [],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<GraphPage showToast={showToast} />);

    expect(await screen.findByText("Failed to load graph data")).toBeTruthy();
    expect(showToast).toHaveBeenCalledWith("Error: graph offline", true);

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/graph/search", {});
      expect(getGraphMockState().instances[0].setData).toHaveBeenCalledWith({
        nodes: [
          {
            id: "recover-1",
            data: {
              label: "Recovered node",
              type: "summary",
              weight: 0.8,
              memory_count: 2,
              degree: 1,
              entry_count: 1,
            },
          },
        ],
        edges: [],
      });
    });
    await waitFor(() => {
      expect(screen.queryByText("Failed to load graph data")).toBeNull();
    });

    const fullscreenButton = screen.getByTitle("全屏");
    fireEvent.click(fullscreenButton);

    expect(HTMLElement.prototype.requestFullscreen).toHaveBeenCalled();
    expect(screen.getByTitle("退出全屏")).toBeTruthy();

    // Simulate browser fullscreen exit notification.
    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      writable: true,
      value: null,
    });
    fireEvent(document, new Event("fullscreenchange"));

    await waitFor(() => {
      expect(screen.getByTitle("全屏")).toBeTruthy();
    });
  });
});
