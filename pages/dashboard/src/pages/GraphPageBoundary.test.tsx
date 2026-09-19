import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, type Mock } from "vitest";
import {
  cleanupGraphPageTestEnvironment,
  deferred,
  getGraphMockState,
  loadGraphPage,
  ok,
  setupGraphPageTestEnvironment,
  type BridgeMock,
} from "./GraphPage.test-support";

describe("GraphPage optional source focus", () => {
  let bridge: BridgeMock;
  let showToast: Mock;
  beforeEach(() => {
    ({ bridge, showToast } = setupGraphPageTestEnvironment());
  });
  afterEach(cleanupGraphPageTestEnvironment);

  it("keeps the administrator graph and selection while source inputs are only drafts", async () => {
    const { GraphPage } = await loadGraphPage();
    bridge.apiGet.mockImplementation((path: string) => Promise.resolve(ok(
      path === "page/stats" ? { sessions: {} }
        : { nodes: [{ id: "visible", label: "Visible topic", type: "topic" }], edges: [] },
    )));
    render(<GraphPage showToast={showToast} theme="light" />);
    const graph = getGraphMockState().instances[0];
    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
    graph.emit("node:click", { target: { id: "visible" } });
    expect(await screen.findByText("Visible topic")).toBeTruthy();
    const requestCount = bridge.apiGet.mock.calls.length;
    const renderCount = graph.render.mock.calls.length;
    const memoryInput = screen.getByRole("textbox", { name: /memory id.*optional/i });

    fireEvent.change(memoryInput, { target: { value: "42" } });
    fireEvent.change(memoryInput, { target: { value: "43" } });
    fireEvent.change(memoryInput, { target: { value: "" } });
    fireEvent.change(screen.getByRole("textbox", { name: /search entities/i }), {
      target: { value: "draft topic" },
    });

    expect(screen.getByText("Visible topic")).toBeTruthy();
    expect(graph.getElementState("visible")).toContain("selected");
    expect(bridge.apiGet).toHaveBeenCalledTimes(requestCount);
    expect(graph.render).toHaveBeenCalledTimes(renderCount);
  });

  it("focuses on a trimmed optional memory ID when Enter is pressed", async () => {
    const { GraphPage } = await loadGraphPage();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/stats") return Promise.resolve(ok({ sessions: {} }));
      return Promise.resolve(ok({
        nodes: params.memory_id === "42"
          ? [{ id: "focused", label: "Focused topic", type: "topic" }] : [],
        edges: [],
      }));
    });
    render(<GraphPage showToast={showToast} theme="light" />);
    const input = screen.getByRole("textbox", { name: /memory id.*optional/i });
    fireEvent.change(input, { target: { value: " 42 " } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenLastCalledWith("page/graph/search", {
        memory_id: "42", time_end_hours: "168",
      });
      expect(screen.queryByText("Loading...")).toBeNull();
    });
    getGraphMockState().instances[0].emit("node:click", { target: { id: "focused" } });
    expect(await screen.findByText("Focused topic")).toBeTruthy();
  });

  it("clears hover and selection on Overview and ignores an older focused response", async () => {
    const { GraphPage } = await loadGraphPage();
    const pending = deferred<unknown>();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/stats") return Promise.resolve(ok({ sessions: {} }));
      if (params.query === "pending") return pending.promise;
      return Promise.resolve(ok({
        nodes: [{ id: "visible", label: "Visible node", type: "topic" }], edges: [],
      }));
    });
    render(<GraphPage showToast={showToast} theme="light" />);
    const graph = getGraphMockState().instances[0];
    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
    act(() => {
      graph.emit("node:click", { target: { id: "visible" } });
      graph.emit("node:pointerover", { target: { id: "visible" } });
    });
    expect(screen.getAllByText("Visible node")).toHaveLength(2);
    fireEvent.change(screen.getByRole("textbox", { name: /search entities/i }), {
      target: { value: "pending" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: /memory id.*optional/i }), {
      target: { value: "42" },
    });
    fireEvent.click(screen.getByRole("button", { name: /search graph/i }));
    expect(screen.queryByText("Visible node")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /overview/i }));
    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());

    await act(async () => {
      pending.resolve(ok({ nodes: [{ id: "late", label: "Late node", type: "topic" }], edges: [] }));
      await pending.promise;
    });

    expect(graph.setData.mock.lastCall?.[0].nodes.map((node: { id: string }) => node.id))
      .toEqual(["visible"]);
    graph.emit("node:click", { target: { id: "visible" } });
    expect(await screen.findByText("Visible node")).toBeTruthy();
    expect(screen.queryByText("Late node")).toBeNull();
  });

  it("serializes layout renders and skips superseded queued search results", async () => {
    const { GraphPage } = await loadGraphPage();
    const initialRender = deferred<void>();
    const queuedSearch = deferred<unknown>();
    const latestSearch = deferred<unknown>();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/stats") return Promise.resolve(ok({ sessions: {} }));
      if (params.query === "queued") return queuedSearch.promise;
      if (params.query === "latest") return latestSearch.promise;
      return Promise.resolve(ok({
        nodes: [{ id: "seed", label: "Initial topic", type: "topic" }], edges: [],
      }));
    });
    render(<GraphPage showToast={showToast} theme="light" />);
    const graph = getGraphMockState().instances[0];
    let layoutPending = true;
    graph.render.mockImplementation(async () => {
      if (layoutPending) throw new Error("a previous layout is still using the graph model");
    }).mockImplementationOnce(() => initialRender.promise);
    await waitFor(() => expect(graph.render).toHaveBeenCalled());
    const queryInput = screen.getByRole("textbox", { name: /search entities/i });
    fireEvent.change(queryInput, { target: { value: "queued" } });
    fireEvent.click(screen.getByRole("button", { name: /search graph/i }));
    await act(async () => {
      queuedSearch.resolve(ok({
        nodes: [{ id: "queued", label: "Obsolete topic", type: "topic" }], edges: [],
      }));
      await queuedSearch.promise;
    });
    fireEvent.change(queryInput, { target: { value: "latest" } });
    fireEvent.click(screen.getByRole("button", { name: /search graph/i }));
    await act(async () => {
      latestSearch.resolve(ok({
        nodes: [{ id: "latest", label: "Latest topic", type: "topic" }], edges: [],
      }));
      await latestSearch.promise;
    });
    expect(screen.getByText("Loading...")).toBeTruthy();

    await act(async () => {
      layoutPending = false;
      initialRender.resolve(undefined);
      await initialRender.promise;
    });

    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
    expect(graph.setData.mock.calls.map(([data]) => data.nodes.map((node: { id: string }) => node.id)))
      .toEqual([["seed"], ["latest"]]);
    graph.emit("node:click", { target: { id: "latest" } });
    expect(await screen.findByText("Latest topic")).toBeTruthy();
    expect(showToast).not.toHaveBeenCalled();
  });

  it("clears in-flight G6 selection and ignores old click completions after Overview", async () => {
    const { GraphPage } = await loadGraphPage();
    bridge.apiGet.mockImplementation((path: string) => Promise.resolve(ok(
      path === "page/stats" ? { sessions: {} } : {
        nodes: [
          { id: "old", label: "Old selection", type: "topic" },
          { id: "new", label: "New selection", type: "topic" },
        ], edges: [],
      },
    )));
    render(<GraphPage showToast={showToast} theme="light" />);
    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
    const graph = getGraphMockState().instances[0];
    const pendingNodeClick = deferred<void>();
    graph.selectionAnimation = pendingNodeClick.promise;
    graph.emit("node:click", { target: { id: "old" } });
    expect(graph.getElementState("old")).toContain("selected");
    expect(screen.queryByText("Old selection")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /overview/i }));
    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
    await act(async () => {
      pendingNodeClick.resolve(undefined);
      await pendingNodeClick.promise;
    });
    expect(graph.getElementState("old")).not.toContain("selected");
    expect(screen.queryByText("Old selection")).toBeNull();

    const pendingCanvasClick = deferred<void>();
    graph.selectionAnimation = pendingCanvasClick.promise;
    graph.emit("canvas:click");
    fireEvent.click(screen.getByRole("button", { name: /overview/i }));
    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
    graph.selectionAnimation = null;
    graph.emit("node:click", { target: { id: "new" } });
    expect(await screen.findByText("New selection")).toBeTruthy();
    await act(async () => {
      pendingCanvasClick.resolve(undefined);
      await pendingCanvasClick.promise;
    });

    expect(screen.getByText("New selection")).toBeTruthy();
    expect(graph.getElementState("new")).toContain("selected");
  });

  it("keeps refreshed overview statistics when the initial response arrives late", async () => {
    const { GraphPage } = await loadGraphPage();
    const initialStats = deferred<unknown>();
    let statsRequests = 0;
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") {
        statsRequests += 1;
        return statsRequests === 1 ? initialStats.promise
          : Promise.resolve(ok({ total_memories: 22, sessions: {} }));
      }
      return Promise.resolve(ok({ nodes: [], edges: [] }));
    });
    render(<GraphPage showToast={showToast} theme="light" />);
    fireEvent.click(screen.getByRole("button", { name: /overview/i }));
    expect(await screen.findByText("22")).toBeTruthy();

    await act(async () => {
      initialStats.resolve(ok({ total_memories: 21, sessions: {} }));
      await initialStats.promise;
    });

    expect(screen.getByText("22")).toBeTruthy();
    expect(screen.queryByText("21")).toBeNull();
  });

  it("displays a rejected source envelope instead of successful empty data", async () => {
    const { GraphPage } = await loadGraphPage();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => Promise.resolve(
      path === "page/stats" ? ok({ sessions: {} })
        : params.memory_id ? { status: "error", code: "graph_boundary_required", message: "boundary unavailable" }
          : ok({ nodes: [], edges: [] }),
    ));
    render(<GraphPage showToast={showToast} theme="light" />);
    fireEvent.change(screen.getByRole("textbox", { name: /memory id.*optional/i }), {
      target: { value: "42" },
    });
    fireEvent.click(screen.getByRole("button", { name: /search graph/i }));
    expect(await screen.findByText("Failed to load graph data")).toBeTruthy();
    expect(showToast).toHaveBeenCalledWith("Error: boundary unavailable", true);
    expect(screen.queryByText(/no graph data/i)).toBeNull();
  });

  it("shows an actionable empty state for a source with no graph data", async () => {
    const { GraphPage } = await loadGraphPage();
    bridge.apiGet.mockResolvedValue(ok({ sessions: {}, nodes: [], edges: [] }));
    render(<GraphPage showToast={showToast} theme="light" />);
    expect((await screen.findByRole("status")).textContent).toMatch(/no graph data.*filters/i);
    fireEvent.change(screen.getByRole("textbox", { name: /memory id.*optional/i }), {
      target: { value: "42" },
    });
    fireEvent.click(screen.getByRole("button", { name: /search graph/i }));

    expect((await screen.findByRole("status")).textContent).toMatch(/no graph data.*filters/i);
    expect(screen.queryByText("Failed to load graph data")).toBeNull();
    expect(showToast).not.toHaveBeenCalled();
  });
});
