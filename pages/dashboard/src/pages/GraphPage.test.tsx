import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EN_MAP } from "../mock";
import {
  cleanupGraphPageTestEnvironment,
  deferred,
  getGraphMockState,
  loadGraphPage,
  ok,
  setupGraphPageTestEnvironment,
  type BridgeMock,
  type ClickSelectBehaviorMock,
} from "./GraphPage.test-support";

describe("GraphPage", () => {
  let bridge: BridgeMock;
  let showToast: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    const environment = setupGraphPageTestEnvironment();
    bridge = environment.bridge;
    showToast = environment.showToast;
  });

  afterEach(() => {
    cleanupGraphPageTestEnvironment();
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

    const { container } = render(<GraphPage showToast={showToast} theme="light" />);

    expect(container.querySelector('[data-slot="page-frame"]')?.getAttribute("data-layout")).toBe("workspace");
    expect(screen.getByRole("heading", { level: 1, name: "Knowledge Graph" })).toBeTruthy();
    const workspace = container.querySelector('[data-workspace-grid="stable"]');
    const canvas = container.querySelector('[data-slot="graph-canvas"]');
    const statsScroll = container.querySelector('[data-slot="graph-stats-scroll"]');
    const statsGrid = statsScroll?.querySelector('[data-slot="metric-grid"]');
    const toolbar = screen.getByRole("toolbar");
    expect(workspace?.classList.contains("grid-rows-[auto_minmax(320px,1fr)_auto_auto_auto]")).toBe(true);
    expect(canvas?.classList.contains("min-h-[320px]")).toBe(true);
    expect(statsScroll?.classList.contains("w-full")).toBe(true);
    expect(statsScroll?.classList.contains("overflow-x-auto")).toBe(true);
    expect(statsGrid?.classList.contains("min-w-[32rem]")).toBe(true);
    expect(statsGrid?.classList.contains("overflow-x-auto")).toBe(false);
    expect(toolbar.classList.contains("flex-nowrap")).toBe(true);
    expect(toolbar.classList.contains("overflow-x-auto")).toBe(true);

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/stats", {});
      expect(bridge.apiGet).toHaveBeenCalledWith("page/graph/search", {
        canvas: "1",
        time_end_hours: "168",
      });
    });

    expect(await screen.findByText("12")).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
    expect(screen.getByText("5")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();

    expect(getGraphMockState().instances).toHaveLength(1);
    const graph = getGraphMockState().instances[0];
    const behaviors = graph.config.behaviors as ClickSelectBehaviorMock[];
    const clickSelect = behaviors.find((behavior) => behavior.type === "click-select");
    expect(clickSelect).toMatchObject({
      type: "click-select",
      multiple: false,
      state: "selected",
      degree: 0,
      animation: true,
    });
    expect(clickSelect?.enable).toEqual(expect.any(Function));
    expect(clickSelect?.onClick).toEqual(expect.any(Function));
    const layout = graph.config.layout as { alphaDecay?: number };
    expect(layout.alphaDecay).toBe(0.03);
    const nodeConfig = graph.config.node as {
      state: {
        hover: { stroke: string; lineWidth: number };
        selected: { stroke: string; lineWidth: number };
      };
    };
    expect(nodeConfig.state.selected.stroke).not.toBe("#4dabf7");
    expect(nodeConfig.state.selected.lineWidth).toBe(3);
    expect(nodeConfig.state.hover.lineWidth).toBeLessThan(
      nodeConfig.state.selected.lineWidth,
    );
    expect(nodeConfig.state.hover.stroke).not.toBe(
      nodeConfig.state.selected.stroke,
    );
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
      if (path === "page/graph/search" && params.canvas === "1") {
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

    render(<GraphPage showToast={showToast} theme="light" />);

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
        time_end_hours: "168",
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

    vi.mocked(graph.setElementState).mockClear();
    graph.emit("node:click", { target: { id: "42" } });

    expect(await screen.findByText("Deploy topic")).toBeTruthy();
    expect(screen.getByText("6")).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("1.20")).toBeTruthy();
    expect(graph.focusElement).toHaveBeenCalledWith("42", { duration: 500 });
    expect(graph.getElementState("42")).toContain("selected");
    expect(graph.setElementState).not.toHaveBeenCalled();

    graph.emit("edge:click", {
      target: { id: "e-42-84-0" },
      targetType: "edge",
    });
    expect(graph.getElementState("42")).toContain("selected");
    expect(graph.getElementState("e-42-84-0")).not.toContain("selected");
    expect(screen.getByText("Deploy topic")).toBeTruthy();

    graph.emit("node:click", { target: { id: "42" } });

    await waitFor(() => {
      expect(screen.queryByText("Deploy topic")).toBeNull();
    });
    expect(graph.getElementState("42")).not.toContain("selected");
    expect(graph.setElementState).not.toHaveBeenCalled();

    graph.emit("node:click", { target: { id: "42" } });
    expect(await screen.findByText("Deploy topic")).toBeTruthy();

    graph.emit("canvas:click");

    await waitFor(() => {
      expect(screen.queryByText("Deploy topic")).toBeNull();
    });
    expect(graph.getElementState("42")).not.toContain("selected");
    expect(graph.setElementState).not.toHaveBeenCalled();

    graph.emit("node:click", { target: { id: "42" } });
    expect(await screen.findByText("Deploy topic")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /search graph/i }));

    await waitFor(() => {
      expect(screen.queryByText("Deploy topic")).toBeNull();
    });
    expect(graph.setElementState).toHaveBeenLastCalledWith("42", [], false);
  });

  it("restores the selected node state after an in-place theme redraw", async () => {
    const { GraphPage } = await loadGraphPage();

    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") {
        return Promise.resolve(ok({
          total_memories: 1,
          graph_nodes: 1,
          graph_edges: 0,
          sessions: {},
        }));
      }
      if (path === "page/graph/search") {
        return Promise.resolve(ok({
          nodes: [
            {
              id: "theme-node",
              label: "Theme node",
              type: "topic",
              memory_count: 1,
              degree: 0,
              entry_count: 1,
              weight: 0.8,
            },
          ],
          edges: [],
        }));
      }
      return Promise.resolve(ok({}));
    });

    const view = render(<GraphPage showToast={showToast} theme="light" />);

    const firstGraph = await waitFor(() => {
      const instance = getGraphMockState().instances[0];
      expect(instance.setData).toHaveBeenCalled();
      return instance;
    });
    firstGraph.emit("node:click", { target: { id: "theme-node" } });
    expect(await screen.findByText("Theme node")).toBeTruthy();
    vi.mocked(firstGraph.setElementState).mockClear();

    view.rerender(<GraphPage showToast={showToast} theme="dark" />);

    await waitFor(() => {
      expect(firstGraph.setElementState).toHaveBeenCalledWith(
        "theme-node",
        ["selected"],
        false,
      );
    });
    expect(getGraphMockState().instances).toHaveLength(1);
    expect(screen.getByText("Theme node")).toBeTruthy();
  });

  it("ignores an older manual search response after a newer search completes", async () => {
    const { GraphPage } = await loadGraphPage();
    const oldSearch = deferred<unknown>();
    const newSearch = deferred<unknown>();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/stats") return Promise.resolve(ok({ sessions: {} }));
      if (path === "page/graph/search" && params.query === "old") return oldSearch.promise;
      if (path === "page/graph/search" && params.query === "new") return newSearch.promise;
      if (path === "page/graph/search") {
        return Promise.resolve(ok({
          nodes: [{ id: "seed", label: "Seed", type: "topic" }],
          edges: [],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<GraphPage showToast={showToast} theme="light" />);
    const graph = await waitFor(() => {
      const instance = getGraphMockState().instances[0];
      expect(instance.setData).toHaveBeenCalled();
      return instance;
    });

    const queryInput = screen.getByPlaceholderText("Search entities, topics, or memories...");
    fireEvent.change(queryInput, { target: { value: "old" } });
    fireEvent.click(screen.getByRole("button", { name: /search graph/i }));
    fireEvent.change(queryInput, { target: { value: "new" } });
    fireEvent.click(screen.getByRole("button", { name: /search graph/i }));

    newSearch.resolve(ok({
      nodes: [{ id: "new", label: "Newest result", type: "fact" }],
      edges: [],
    }));
    await waitFor(() => {
      const dataCalls = vi.mocked(graph.setData).mock.calls;
      const data = dataCalls[dataCalls.length - 1]?.[0] as {
        nodes: Array<{ id: string }>;
      };
      expect(data.nodes.map((node) => node.id)).toEqual(["new"]);
    });

    oldSearch.resolve(ok({
      nodes: [{ id: "old", label: "Stale result", type: "fact" }],
      edges: [],
    }));
    await oldSearch.promise;
    await Promise.resolve();
    await Promise.resolve();

    const finalDataCalls = vi.mocked(graph.setData).mock.calls;
    const finalData = finalDataCalls[finalDataCalls.length - 1]?.[0] as {
      nodes: Array<{ id: string }>;
    };
    expect(finalData.nodes.map((node) => node.id)).toEqual(["new"]);
  });

  it("enters the error state when the initial G6 render rejects", async () => {
    const { GraphPage } = await loadGraphPage();
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    let resolveGraphRequest!: (value: unknown) => void;
    const graphRequest = new Promise<unknown>((resolve) => {
      resolveGraphRequest = resolve;
    });
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ sessions: {} }));
      if (path === "page/graph/search") return graphRequest;
      return Promise.resolve(ok({}));
    });

    render(<GraphPage showToast={showToast} theme="light" />);
    const graph = getGraphMockState().instances[0];
    vi.mocked(graph.render).mockRejectedValueOnce(new Error("G6 render exploded"));
    resolveGraphRequest(ok({
      nodes: [{ id: "broken", label: "Broken", type: "topic" }],
      edges: [],
    }));

    expect(await screen.findByText("Failed to load graph data")).toBeTruthy();
    expect(showToast).toHaveBeenCalledWith("Error: G6 render exploded", true);
    expect(errorSpy).toHaveBeenCalled();
  });

  it("ignores a pending theme draw rejection after unmount", async () => {
    const { GraphPage } = await loadGraphPage();
    const pendingDraw = deferred<void>();
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ sessions: {} }));
      if (path === "page/graph/search") {
        return Promise.resolve(ok({
          nodes: [{ id: "theme-node", label: "Theme node", type: "topic" }],
          edges: [],
        }));
      }
      return Promise.resolve(ok({}));
    });

    const view = render(<GraphPage showToast={showToast} theme="light" />);
    const graph = await waitFor(() => {
      const instance = getGraphMockState().instances[0];
      expect(instance.render).toHaveBeenCalled();
      return instance;
    });
    vi.mocked(graph.draw).mockImplementationOnce(() => pendingDraw.promise);
    view.rerender(<GraphPage showToast={showToast} theme="dark" />);
    await waitFor(() => expect(graph.draw).toHaveBeenCalledTimes(1));
    showToast.mockClear();

    view.unmount();
    pendingDraw.reject(new Error("late draw failure"));
    await pendingDraw.promise.catch(() => undefined);
    await Promise.resolve();

    expect(showToast).not.toHaveBeenCalled();
    expect(graph.setElementState).not.toHaveBeenCalled();
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

    render(<GraphPage showToast={showToast} theme="light" />);

    expect(await screen.findByText("Failed to load graph data")).toBeTruthy();
    expect(showToast).toHaveBeenCalledWith("Error: graph offline", true);

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/graph/search", {
        canvas: "1",
        time_end_hours: "168",
      });
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

    const fullscreenButton = screen.getByTitle(EN_MAP["graph.fullscreen"]);
    fireEvent.click(fullscreenButton);

    expect(HTMLElement.prototype.requestFullscreen).toHaveBeenCalled();
    expect(screen.getByTitle(EN_MAP["graph.exitFullscreen"])).toBeTruthy();

    // Simulate browser fullscreen exit notification.
    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      writable: true,
      value: null,
    });
    fireEvent(document, new Event("fullscreenchange"));

    await waitFor(() => {
      expect(screen.getByTitle(EN_MAP["graph.fullscreen"])).toBeTruthy();
    });
  });

  it("updates graph theme styles in place without rebuilding data or layout", async () => {
    const { GraphPage } = await loadGraphPage();

    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") {
        return Promise.resolve(ok({
          total_memories: 1,
          graph_nodes: 1,
          graph_edges: 0,
          sessions: {},
        }));
      }
      if (path === "page/graph/search") {
        return Promise.resolve(ok({
          nodes: [{ id: "theme-node", label: "Theme node", type: "topic" }],
          edges: [],
        }));
      }
      return Promise.resolve(ok({}));
    });

    const view = render(<GraphPage showToast={showToast} theme="light" />);
    const graph = await waitFor(() => {
      const instance = getGraphMockState().instances[0];
      expect(instance.setData).toHaveBeenCalled();
      return instance;
    });
    const apiCallCount = bridge.apiGet.mock.calls.length;
    const dataRenderCount = vi.mocked(graph.render).mock.calls.length;

    view.rerender(<GraphPage showToast={showToast} theme="dark" />);

    await waitFor(() => expect(graph.setOptions).toHaveBeenCalled());
    const optionCalls = vi.mocked(graph.setOptions).mock.calls;
    const options = optionCalls[optionCalls.length - 1]?.[0] as {
      node?: { style?: { labelFill?: string }; animation?: Record<string, unknown> };
      edge?: { style?: { labelFill?: string }; animation?: Record<string, unknown> };
    };

    expect(getGraphMockState().instances).toHaveLength(1);
    expect(graph.destroy).not.toHaveBeenCalled();
    expect(graph.draw).toHaveBeenCalledTimes(1);
    expect(graph.render).toHaveBeenCalledTimes(dataRenderCount);
    expect(bridge.apiGet).toHaveBeenCalledTimes(apiCallCount);
    expect(options.node?.style?.labelFill).toBe("#e8eaed");
    expect(options.edge?.style?.labelFill).toBe("#94a3b8");
    expect(options.node?.animation).toMatchObject({
      update: [{ fields: ["fill"], shape: "label", duration: 200, easing: "ease-out" }],
    });
    expect(options.edge?.animation).toMatchObject({
      update: [{ fields: ["fill"], shape: "label", duration: 200, easing: "ease-out" }],
    });
  });

  it("disables every graph animation path when reduced motion is requested", async () => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: vi.fn().mockReturnValue({ matches: true }),
    });
    const { GraphPage } = await loadGraphPage();
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ sessions: {} }));
      if (path === "page/graph/search") {
        return Promise.resolve(ok({
          nodes: [{ id: "reduced", label: "Reduced node", type: "topic" }],
          edges: [],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<GraphPage showToast={showToast} theme="light" />);
    const graph = await waitFor(() => {
      const instance = getGraphMockState().instances[0];
      expect(instance.setData).toHaveBeenCalled();
      return instance;
    });
    const clickSelect = (graph.config.behaviors as ClickSelectBehaviorMock[])
      .find((behavior) => behavior.type === "click-select");
    const layout = graph.config.layout as { animation?: boolean };
    const node = graph.config.node as { animation?: unknown };
    const edge = graph.config.edge as { animation?: unknown };

    expect(graph.config.animation).toBe(false);
    expect(layout.animation).toBe(false);
    expect(clickSelect?.animation).toBe(false);
    expect(node.animation).toBe(false);
    expect(edge.animation).toBe(false);

    vi.mocked(graph.focusElement).mockRejectedValueOnce(new Error("focus failed"));
    graph.emit("node:click", { target: { id: "reduced" } });

    expect(graph.focusElement).toHaveBeenCalledWith("reduced", { duration: 0 });
    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith("Error: focus failed", true);
    });
  });
});
