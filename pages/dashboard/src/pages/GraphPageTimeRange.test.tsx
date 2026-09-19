import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  cleanupGraphPageTestEnvironment,
  getGraphMockState,
  loadGraphPage,
  ok,
  setupGraphPageTestEnvironment,
  type BridgeMock,
} from "./GraphPage.test-support";

describe("GraphPage 时间范围", () => {
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

  it("应用时间范围后重新请求后端并以新响应重建画布", async () => {
    const { GraphPage } = await loadGraphPage();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/stats") return Promise.resolve(ok({ sessions: {} }));
      if (path === "page/graph/search" && params.time_end_hours === "24") {
        return Promise.resolve(ok({
          nodes: [{ id: "recent", label: "Recent", type: "topic" }],
          edges: [],
        }));
      }
      if (path === "page/graph/search") {
        return Promise.resolve(ok({
          nodes: [{ id: "initial", label: "Initial", type: "summary" }],
          edges: [],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<GraphPage showToast={showToast} theme="light" />);

    const graph = await waitFor(() => {
      const instance = getGraphMockState().instances[0];
      expect(instance.setData).toHaveBeenCalled();
      expect(screen.queryByText("Loading...")).toBeNull();
      return instance;
    });
    const initialApiCallCount = bridge.apiGet.mock.calls.length;

    fireEvent.change(screen.getAllByRole("slider")[0], { target: { value: "6" } });
    fireEvent.change(screen.getAllByRole("slider")[1], { target: { value: "24" } });
    expect(bridge.apiGet).toHaveBeenCalledTimes(initialApiCallCount);
    fireEvent.click(screen.getByRole("button", { name: /apply/i }));

    await waitFor(() => {
      expect(screen.queryByText("Loading...")).toBeNull();
      expect(bridge.apiGet).toHaveBeenCalledWith("page/graph/search", {
        canvas: "1",
        time_start_hours: "6",
        time_end_hours: "24",
      });
    });
    graph.emit("node:click", { target: { id: "recent" } });
    expect(await screen.findByText("Recent")).toBeTruthy();
  });

  it("重置时间范围时显式请求全部图谱", async () => {
    const { GraphPage } = await loadGraphPage();
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ sessions: {} }));
      if (path === "page/graph/search") return Promise.resolve(ok({ nodes: [], edges: [] }));
      return Promise.resolve(ok({}));
    });

    render(<GraphPage showToast={showToast} theme="light" />);
    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: /reset/i }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenLastCalledWith("page/graph/search", {
        canvas: "1",
      });
    });
    expect(screen.getByText("All")).toBeTruthy();
  });

  it("最近概览清除查询和记忆聚焦，恢复最近七天并刷新图谱与统计", async () => {
    const { GraphPage } = await loadGraphPage();
    let statsRequests = 0;
    let overviewRequests = 0;
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/stats") {
        statsRequests += 1;
        return Promise.resolve(ok({ total_memories: statsRequests === 1 ? 21 : 22, sessions: {} }));
      }
      if (params.canvas === "1") {
        overviewRequests += 1;
        const node = overviewRequests === 1
          ? { id: "initial", label: "Initial topic", type: "topic" }
          : { id: "recent", label: "Refreshed recent topic", type: "topic" };
        return Promise.resolve(ok({ nodes: [node], edges: [] }));
      }
      return Promise.resolve(ok({
        nodes: [{ id: "focused", label: "Focused fact", type: "fact" }], edges: [],
      }));
    });
    render(<GraphPage showToast={showToast} theme="light" />);
    expect(await screen.findByText("21")).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
    const queryInput = screen.getByRole("textbox", { name: /search entities/i });
    const memoryInput = screen.getByRole("textbox", { name: /memory id.*optional/i });
    fireEvent.change(queryInput, { target: { value: "release" } });
    fireEvent.change(memoryInput, { target: { value: "42" } });
    fireEvent.change(screen.getAllByRole("slider")[0], { target: { value: "6" } });
    fireEvent.change(screen.getAllByRole("slider")[1], { target: { value: "24" } });
    fireEvent.click(screen.getByRole("button", { name: /apply/i }));
    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenLastCalledWith("page/graph/search", {
        query: "release", memory_id: "42", time_start_hours: "6", time_end_hours: "24",
      });
      expect(screen.queryByText("Loading...")).toBeNull();
    });
    const graph = getGraphMockState().instances[0];
    graph.emit("node:click", { target: { id: "focused" } });
    expect(await screen.findByText("Focused fact")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /overview/i }));

    expect(await screen.findByText("22")).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("Loading...")).toBeNull());
    expect(bridge.apiGet).toHaveBeenLastCalledWith("page/graph/search", {
      canvas: "1", time_end_hours: "168",
    });
    expect((queryInput as HTMLInputElement).value).toBe("");
    expect((memoryInput as HTMLInputElement).value).toBe("");
    expect(screen.getAllByRole("slider").map((slider) => (slider as HTMLInputElement).value))
      .toEqual(["0", "168"]);
    expect(screen.queryByText("Focused fact")).toBeNull();
    graph.emit("node:click", { target: { id: "recent" } });
    expect(await screen.findByText("Refreshed recent topic")).toBeTruthy();
  });

  it("后端筛选响应移除节点时清除详情和 G6 选择状态", async () => {
    const { GraphPage } = await loadGraphPage();

    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/stats") return Promise.resolve(ok({ sessions: {} }));
      if (path === "page/graph/search" && params.time_end_hours === "12") {
        return Promise.resolve(ok({
          nodes: [{ id: "recent", label: "Recent node", type: "topic" }],
          edges: [],
        }));
      }
      if (path === "page/graph/search") {
        return Promise.resolve(ok({
          nodes: [
            { id: "old", label: "Old selected node", type: "fact" },
            { id: "anchor", label: "Anchor", type: "summary" },
          ],
          edges: [{
            source: "old",
            target: "anchor",
            type: "after",
          }],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<GraphPage showToast={showToast} theme="light" />);
    const graph = await waitFor(() => {
      const instance = getGraphMockState().instances[0];
      expect(instance.setData).toHaveBeenCalled();
      expect(screen.queryByText("Loading...")).toBeNull();
      return instance;
    });

    graph.emit("node:click", { target: { id: "old" } });
    expect(await screen.findByText("Old selected node")).toBeTruthy();
    vi.mocked(graph.setElementState).mockClear();

    fireEvent.change(screen.getAllByRole("slider")[1], { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: /apply/i }));

    await waitFor(() => {
      expect(screen.queryByText("Old selected node")).toBeNull();
    });
    expect(graph.setElementState).toHaveBeenCalledWith("old", [], false);
    expect(graph.getElementState("old")).not.toContain("selected");
  });

  it("应用时间范围后的 G6 重绘失败时进入错误状态", async () => {
    const { GraphPage } = await loadGraphPage();
    vi.spyOn(console, "error").mockImplementation(() => {});
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ sessions: {} }));
      if (path === "page/graph/search") {
        return Promise.resolve(ok({
          nodes: [{ id: "recent", label: "Recent", type: "topic" }],
          edges: [],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<GraphPage showToast={showToast} theme="light" />);
    const graph = getGraphMockState().instances[0];
    await waitFor(() => {
      expect(graph.render).toHaveBeenCalled();
      expect(screen.queryByText("Loading...")).toBeNull();
    });
    vi.mocked(graph.render).mockRejectedValueOnce(new Error("range render exploded"));
    fireEvent.change(screen.getAllByRole("slider")[1], { target: { value: "12" } });

    fireEvent.click(screen.getByRole("button", { name: /apply/i }));

    expect(await screen.findByText("Failed to load graph data")).toBeTruthy();
    expect(showToast).toHaveBeenCalledWith("Error: range render exploded", true);
  });
});
