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
      return instance;
    });
    const initialApiCallCount = bridge.apiGet.mock.calls.length;

    fireEvent.change(screen.getAllByRole("slider")[0], { target: { value: "6" } });
    fireEvent.change(screen.getAllByRole("slider")[1], { target: { value: "24" } });
    expect(bridge.apiGet).toHaveBeenCalledTimes(initialApiCallCount);
    fireEvent.click(screen.getByRole("button", { name: /apply/i }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/graph/search", {
        canvas: "1",
        time_start_hours: "6",
        time_end_hours: "24",
      });
      expect(graph.setData).toHaveBeenLastCalledWith({
        nodes: [{
          id: "recent",
          data: {
            label: "Recent",
            type: "topic",
            weight: 1,
            memory_count: 0,
            degree: 0,
            entry_count: 0,
          },
        }],
        edges: [],
      });
    });
    expect(graph.setElementVisibility).not.toHaveBeenCalled();
  });

  it("重置时间范围时显式请求全部图谱", async () => {
    const { GraphPage } = await loadGraphPage();
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ sessions: {} }));
      if (path === "page/graph/search") return Promise.resolve(ok({ nodes: [], edges: [] }));
      return Promise.resolve(ok({}));
    });

    render(<GraphPage showToast={showToast} theme="light" />);
    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/graph/search", {
        canvas: "1",
        time_end_hours: "168",
      });
    });
    fireEvent.click(screen.getByRole("button", { name: /reset/i }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenLastCalledWith("page/graph/search", { canvas: "1" });
    });
    expect(screen.getByText("All")).toBeTruthy();
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
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
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
    const graph = await waitFor(() => {
      const instance = getGraphMockState().instances[0];
      expect(instance.render).toHaveBeenCalled();
      return instance;
    });
    vi.mocked(graph.render).mockRejectedValueOnce(new Error("range render exploded"));

    fireEvent.change(screen.getAllByRole("slider")[1], { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: /apply/i }));

    expect(await screen.findByText("Failed to load graph data")).toBeTruthy();
    expect(showToast).toHaveBeenCalledWith("Error: range render exploded", true);
    expect(errorSpy).toHaveBeenCalled();
  });
});
