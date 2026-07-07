import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SearchBar } from "./SearchBar";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale?: ReturnType<typeof vi.fn>;
  getI18n?: ReturnType<typeof vi.fn>;
  t?: ReturnType<typeof vi.fn>;
}

describe("SearchBar", () => {
  let bridge: BridgeMock;
  let onNavigate: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();

    bridge = {
      apiGet: vi.fn(),
      apiPost: vi.fn(),
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
    };
    onNavigate = vi.fn();

    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: bridge,
    });
  });

  afterEach(() => {
    cleanup();
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  it("opens the search overlay with Ctrl+K and closes it with Escape", async () => {
    render(<SearchBar onNavigate={onNavigate} />);

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    expect(screen.getByPlaceholderText("Search memories, knowledge, notes...")).toBeTruthy();

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByPlaceholderText("Search memories, knowledge, notes...")).toBeNull();
  });

  it("performs debounced search across memories, knowledge, and notes", async () => {
    bridge.apiGet
      .mockResolvedValueOnce({
        status: "ok",
        data: [{ id: 1, content: "Python memory", importance: 0.7 }],
      })
      .mockResolvedValueOnce({
        status: "ok",
        data: [{ entry_id: "k-1", title: "Python knowledge", category: "fact" }],
      })
      .mockResolvedValueOnce({
        status: "ok",
        data: [{ note_id: "n-1", title: "Python note", status: "active" }],
      });

    render(<SearchBar onNavigate={onNavigate} />);

    fireEvent.click(screen.getByText("Search..."));
    fireEvent.change(
      screen.getByPlaceholderText("Search memories, knowledge, notes..."),
      { target: { value: "python" } }
    );

    await act(async () => {
      vi.advanceTimersByTime(300);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(bridge.apiGet).toHaveBeenCalledTimes(3);
    expect(bridge.apiGet).toHaveBeenNthCalledWith(1, "page/memories", {
      keyword: "python",
      page_size: "5",
    });
    expect(bridge.apiGet).toHaveBeenNthCalledWith(2, "page/knowledge/search", {
      query: "python",
      limit: "5",
    });
    expect(bridge.apiGet).toHaveBeenNthCalledWith(3, "page/notes/search", {
      query: "python",
      limit: "5",
    });

    expect(screen.getByText("Python memory")).toBeTruthy();
    expect(screen.getByText("Python knowledge")).toBeTruthy();
    expect(screen.getByText("Python note")).toBeTruthy();
  });

  it("navigates to the target page with highlight state when a result is clicked", async () => {
    bridge.apiGet
      .mockResolvedValueOnce({
        status: "ok",
        data: [{ id: 11, content: "Memory hit", importance: 0.9 }],
      })
      .mockResolvedValueOnce({ status: "ok", data: [] })
      .mockResolvedValueOnce({ status: "ok", data: [] });

    render(<SearchBar onNavigate={onNavigate} />);

    fireEvent.click(screen.getByText("Search..."));
    fireEvent.change(
      screen.getByPlaceholderText("Search memories, knowledge, notes..."),
      { target: { value: "memory" } }
    );

    await act(async () => {
      vi.advanceTimersByTime(300);
      await Promise.resolve();
      await Promise.resolve();
    });

    fireEvent.click(screen.getByText("Memory hit"));

    expect(onNavigate).toHaveBeenCalledWith("memory", { highlightId: "11" });
    expect(screen.queryByPlaceholderText("Search memories, knowledge, notes...")).toBeNull();
  });
});
