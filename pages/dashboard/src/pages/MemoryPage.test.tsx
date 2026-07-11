import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => ({
    getTotalSize: () => count * estimateSize(),
    getVirtualItems: () => Array.from({ length: count }, (_, index) => ({
      key: index,
      index,
      size: estimateSize(),
      start: index * estimateSize(),
    })),
  }),
}));

import { MemoryPage } from "./MemoryPage";

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

describe("MemoryPage", () => {
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

  it("refetches memories when filters and pagination change", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path !== "page/memories") return Promise.resolve(ok({}));
      return Promise.resolve(ok({
        items: [
          {
            id: "mem-1",
            summary: `Memory page ${params.page}`,
            type: "fact",
            importance: 0.8,
            status: params.status ?? "active",
            created_at: "2026-06-28T12:00:00Z",
          },
        ],
        total: 25,
      }));
    });

    const { container } = render(<MemoryPage showToast={showToast} />);

    expect(container.querySelector('[data-slot="page-frame"]')?.getAttribute("data-layout")).toBe("dense");
    expect(screen.getByRole("heading", { level: 1, name: "Memories" })).toBeTruthy();

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/memories", {
        page: "1",
        page_size: "20",
      });
    });

    fireEvent.change(screen.getByPlaceholderText("Keyword (ID or content search)"), {
      target: { value: "python" },
    });
    fireEvent.change(screen.getByPlaceholderText("Session ID (optional)"), {
      target: { value: "session-42" },
    });

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/memories", {
        page: "1",
        page_size: "20",
        keyword: "python",
        session_id: "session-42",
      });
    });

    fireEvent.click(screen.getByRole("button", { name: /next/i }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/memories", {
        page: "2",
        page_size: "20",
        keyword: "python",
        session_id: "session-42",
      });
    });

    expect(await screen.findByText("Memory page 2")).toBeTruthy();
    expect(screen.getByText("Page 2/2 · 25 total")).toBeTruthy();
  });

  it("shows the batch bar, archives selected memories, and refreshes the list", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      items: [
        {
          id: "mem-1",
          summary: "Alpha memory",
          type: "fact",
          importance: 0.8,
          status: "active",
          created_at: "2026-06-28T12:00:00Z",
        },
        {
          id: "mem-2",
          summary: "Beta memory",
          type: "note",
          importance: 0.5,
          status: "active",
          created_at: "2026-06-27T12:00:00Z",
        },
      ],
      total: 2,
    }));
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<MemoryPage showToast={showToast} />);

    expect(await screen.findByText("Alpha memory")).toBeTruthy();
    expect(screen.getByText("Beta memory")).toBeTruthy();

    fireEvent.click(screen.getAllByRole("checkbox")[1]);

    await waitFor(() => {
      expect(screen.getByText("1 selected")).toBeTruthy();
    });

    fireEvent.click(screen.getAllByRole("checkbox")[2]);

    await waitFor(() => {
      expect(screen.getByText("2 selected")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /archived/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/memories/batch", {
        memory_ids: ["mem-1", "mem-2"],
        action: "archive",
      });
    });
    expect(showToast).toHaveBeenCalledWith("archived 2 memories");
  });

  it("opens memory detail, saves edits, closes the drawer, and refreshes memories", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/memories") {
        return Promise.resolve(ok({
          items: [
            {
              id: "mem-9",
              summary: "Gamma memory",
              content: "Original content",
              type: "fact",
              importance: 0.9,
              status: "active",
              created_at: "2026-06-28T12:00:00Z",
            },
          ],
          total: 1,
        }));
      }
      if (path === "page/memory/detail") {
        return Promise.resolve(ok({
          memory: {
            id: params.id,
            content: "Detailed content",
            type: "fact",
            importance: 0.9,
            status: "active",
            created_at: "2026-06-28T12:00:00Z",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<MemoryPage showToast={showToast} />);

    fireEvent.click(await screen.findByText("Gamma memory"));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/memory/detail", { id: "mem-9" });
    });

    const detailTitle = await screen.findByText("Memory Detail");
    const drawer = detailTitle.closest("div")?.parentElement;
    if (!drawer) throw new Error("expected detail drawer");

    fireEvent.change(within(drawer).getByPlaceholderText("New content..."), {
      target: { value: "Rewritten content" },
    });
    fireEvent.change(within(drawer).getByPlaceholderText("Reason"), {
      target: { value: "Fix incorrect wording" },
    });
    fireEvent.click(within(drawer).getByRole("button", { name: /save/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/memory/update", {
        memory_id: "mem-9",
        field: "content",
        value: "Rewritten content",
        reason: "Fix incorrect wording",
      });
    });
    expect(showToast).toHaveBeenCalledWith("Edit successful");
    await waitFor(() => {
      expect(screen.queryByText("Memory Detail")).toBeNull();
    });
  });
});
