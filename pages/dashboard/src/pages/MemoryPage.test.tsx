import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EN_MAP } from "../mock";

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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, reject, resolve };
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
    const localeSpy = vi.spyOn(Date.prototype, "toLocaleDateString");
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
    expect(localeSpy).toHaveBeenCalledWith("en-US");

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

  it("translates known memory types and preserves unknown backend types", async () => {
    bridge.t?.mockImplementation((key: string) => key === "dashboard.memory.type.fact" ? "Fact memory" : key);
    bridge.apiGet.mockResolvedValue(ok({
      items: [
        { id: "known", summary: "Known type", type: "FACT", status: "active" },
        { id: "unknown", summary: "Unknown type", type: "vendor_type", status: "active" },
      ],
      total: 2,
    }));

    render(<MemoryPage showToast={showToast} />);

    expect(await screen.findByText("Fact memory")).toBeTruthy();
    expect(screen.getByText("vendor_type")).toBeTruthy();
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
    expect(
      screen.getByText("Alpha memory").closest('[data-state="selected"]'),
    ).toBeTruthy();

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
    expect(showToast).toHaveBeenCalledWith(EN_MAP["toast.batchArchived"].replace("{0}", "2"));
  });

  it("opens memory detail, saves a full changes object, and keeps the sheet open in view mode", async () => {
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
    expect(within(drawer).getByText(new Date("2026-06-28T12:00:00Z").toLocaleDateString("en-US"))).toBeTruthy();

    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    expect(within(drawer).queryByLabelText("Choose field to edit")).toBeNull();
    expect(within(drawer).getByLabelText("Content")).toBeTruthy();
    expect(within(drawer).getByLabelText("Importance")).toBeTruthy();
    expect(within(drawer).getByLabelText("Type")).toBeTruthy();
    expect(within(drawer).getByLabelText("Status")).toBeTruthy();

    fireEvent.change(within(drawer).getByLabelText("Content"), {
      target: { value: "Rewritten content" },
    });
    fireEvent.change(within(drawer).getByPlaceholderText("Reason"), {
      target: { value: "Fix incorrect wording" },
    });
    fireEvent.click(within(drawer).getByRole("button", { name: /save/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/memory/update", {
        memory_id: "mem-9",
        changes: {
          content: "Rewritten content",
          importance: 0.9,
          type: "fact",
          status: "active",
        },
        reason: "Fix incorrect wording",
      });
    });
    expect(showToast).toHaveBeenCalledWith("Edit successful");
    expect(await screen.findByText("Memory Detail")).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: /^edit$/i })).toBeTruthy();
  });

  it("shows a non-field save failure in the form's only live validation summary", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/memories") {
        return Promise.resolve(ok({
          items: [{ id: "mem-error", summary: "Memory with save error", type: "fact", importance: 0.8, status: "active" }],
          total: 1,
        }));
      }
      if (path === "page/memory/detail") {
        return Promise.resolve(ok({
          memory: {
            id: params.id,
            content: "Original memory content",
            type: "fact",
            importance: 0.8,
            status: "active",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockRejectedValue(new Error("Memory update is offline"));

    render(<MemoryPage showToast={showToast} />);
    fireEvent.click(await screen.findByText("Memory with save error"));
    const detailTitle = await screen.findByText("Memory Detail");
    const drawer = detailTitle.closest("div")?.parentElement;
    if (!drawer) throw new Error("expected detail drawer");

    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Content"), {
      target: { value: "Unsaved memory content" },
    });
    fireEvent.click(within(drawer).getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(within(drawer).getAllByRole("alert")).toHaveLength(1);
    });
    expect(within(drawer).getByRole("alert").textContent).toContain("Memory update is offline");
    expect((within(drawer).getByLabelText("Content") as HTMLTextAreaElement).value).toBe("Unsaved memory content");
  });

  it("keeps a dirty memory edit until local selection changes are discarded", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/memories") {
        return Promise.resolve(ok({
          items: [
            { id: "mem-1", summary: "First memory", type: "fact", importance: 0.8, status: "active" },
            { id: "mem-2", summary: "Second memory", type: "fact", importance: 0.4, status: "active" },
          ],
          total: 2,
        }));
      }
      if (path === "page/memory/detail") {
        return Promise.resolve(ok({
          memory: {
            id: params.id,
            content: params.id === "mem-1" ? "First detail" : "Second detail",
            type: "fact",
            importance: 0.8,
            status: "active",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<MemoryPage showToast={showToast} />);

    fireEvent.click(await screen.findByText("First memory"));
    const detailTitle = await screen.findByText("Memory Detail");
    const drawer = detailTitle.closest("div")?.parentElement;
    if (!drawer) throw new Error("expected detail drawer");

    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Content"), {
      target: { value: "Unsaved first detail" },
    });

    fireEvent.click(screen.getByText("Second memory"));

    expect(bridge.apiGet).not.toHaveBeenCalledWith("page/memory/detail", { id: "mem-2" });
    expect(await screen.findByRole("dialog", { name: /leave configuration without saving/i })).toBeTruthy();
    expect((within(drawer).getByLabelText("Content") as HTMLTextAreaElement).value).toBe("Unsaved first detail");

    fireEvent.click(screen.getByRole("button", { name: /keep editing/i }));
    expect((within(drawer).getByLabelText("Content") as HTMLTextAreaElement).value).toBe("Unsaved first detail");
    expect(screen.queryByRole("dialog", { name: /leave configuration without saving/i })).toBeNull();

    fireEvent.click(screen.getByText("Second memory"));
    fireEvent.click(await screen.findByRole("button", { name: /discard changes and leave/i }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/memory/detail", { id: "mem-2" });
    });
    expect(await screen.findByText("Second detail")).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: /^edit$/i })).toBeTruthy();
    expect(within(drawer).queryByRole("button", { name: /^save$/i })).toBeNull();

    const detailRequestsBeforeCurrentSelection = bridge.apiGet.mock.calls.filter(
      ([path]) => path === "page/memory/detail",
    ).length;
    fireEvent.click(screen.getByText("Second memory"));
    expect(bridge.apiGet.mock.calls.filter(
      ([path]) => path === "page/memory/detail",
    )).toHaveLength(detailRequestsBeforeCurrentSelection);
    expect(screen.queryByRole("dialog", { name: /leave configuration without saving/i })).toBeNull();
  });

  it("loads the replacement memory into the open sheet when an update returns new_memory_id", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/memories") {
        return Promise.resolve(ok({
          items: [{ id: "mem-old", summary: "Old memory", type: "fact", importance: 0.8, status: "active" }],
          total: 1,
        }));
      }
      if (path === "page/memory/detail") {
        const isReplacement = params.id === "mem-new";
        return Promise.resolve(ok({
          memory: {
            id: params.id,
            content: isReplacement ? "Replacement detail" : "Old detail",
            summary: isReplacement ? "Replacement memory" : "Old memory",
            type: "fact",
            importance: 0.8,
            status: "active",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({ new_memory_id: "mem-new" }));

    render(<MemoryPage showToast={showToast} />);

    fireEvent.click(await screen.findByText("Old memory"));
    const detailTitle = await screen.findByText("Memory Detail");
    const drawer = detailTitle.closest("div")?.parentElement;
    if (!drawer) throw new Error("expected detail drawer");

    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Content"), {
      target: { value: "Replacement request content" },
    });
    fireEvent.click(within(drawer).getByRole("button", { name: /save/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/memory/update", expect.objectContaining({
        memory_id: "mem-old",
      }));
      expect(bridge.apiGet).toHaveBeenCalledWith("page/memory/detail", { id: "mem-new" });
    });
    expect(await screen.findByText("Replacement detail")).toBeTruthy();
    expect(within(drawer).getByText("Memory Detail")).toBeTruthy();
    expect(within(drawer).getByText("mem-new")).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: /^edit$/i })).toBeTruthy();
    expect(within(drawer).queryByRole("button", { name: /^save$/i })).toBeNull();
  });

  it("opens the exact memory detail for each navigation target request", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/memories") {
        return Promise.resolve(ok({ items: [], total: 0 }));
      }
      if (path === "page/memory/detail") {
        return Promise.resolve(ok({
          memory: {
            id: params.id,
            content: "Target memory body",
            type: "fact",
            importance: 0.8,
            status: "active",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    const { rerender } = render(
      <MemoryPage
        showToast={showToast}
        navigationTarget={{ requestId: 1, id: "memory-search-target" }}
      />,
    );

    expect(await screen.findByText("Target memory body")).toBeTruthy();
    expect(bridge.apiGet).toHaveBeenCalledWith("page/memory/detail", {
      id: "memory-search-target",
    });

    rerender(
      <MemoryPage
        showToast={showToast}
        navigationTarget={{ requestId: 2, id: "memory-search-target" }}
      />,
    );

    await waitFor(() => {
      expect(bridge.apiGet.mock.calls.filter(
        ([path]) => path === "page/memory/detail",
      )).toHaveLength(2);
    });
  });

  it("keeps a dirty memory draft on a same-entity navigation intent while clean intents refetch", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/memories") return Promise.resolve(ok({ items: [], total: 0 }));
      if (path === "page/memory/detail") {
        return Promise.resolve(ok({
          memory: {
            id: params.id,
            content: "Original navigation memory",
            type: "fact",
            importance: 0.8,
            status: "active",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    const view = render(
      <MemoryPage
        showToast={showToast}
        navigationTarget={{ requestId: 1, id: "memory-same-target" }}
      />,
    );
    const detailTitle = await screen.findByText("Memory Detail");
    const drawer = detailTitle.closest("div")?.parentElement;
    if (!drawer) throw new Error("expected detail drawer");
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Content"), {
      target: { value: "Dirty navigation memory" },
    });

    view.rerender(
      <MemoryPage
        showToast={showToast}
        navigationTarget={{ requestId: 2, id: "memory-same-target" }}
      />,
    );

    expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/memory/detail")).toHaveLength(1);
    expect((within(drawer).getByLabelText("Content") as HTMLTextAreaElement).value).toBe("Dirty navigation memory");

    fireEvent.click(within(drawer).getByRole("button", { name: /^cancel$/i }));
    view.rerender(
      <MemoryPage
        showToast={showToast}
        navigationTarget={{ requestId: 3, id: "memory-same-target" }}
      />,
    );
    await waitFor(() => {
      expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/memory/detail")).toHaveLength(2);
    });
  });

  it("keeps the newest memory detail when an older target resolves last", async () => {
    const staleDetail = deferred<ReturnType<typeof ok>>();
    const staleError = deferred<ReturnType<typeof ok>>();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/memories") {
        return Promise.resolve(ok({ items: [], total: 0 }));
      }
      if (path === "page/memory/detail" && params.id === "memory-old") {
        return staleDetail.promise;
      }
      if (path === "page/memory/detail" && params.id === "memory-error") {
        return staleError.promise;
      }
      if (path === "page/memory/detail" && params.id === "memory-new") {
        return Promise.resolve(ok({
          memory: {
            id: "memory-new",
            content: "Newest memory detail",
            status: "active",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    const view = render(
      <MemoryPage
        showToast={showToast}
        navigationTarget={{ requestId: 1, id: "memory-old" }}
      />,
    );
    view.rerender(
      <MemoryPage
        showToast={showToast}
        navigationTarget={{ requestId: 2, id: "memory-error" }}
      />,
    );
    view.rerender(
      <MemoryPage
        showToast={showToast}
        navigationTarget={{ requestId: 3, id: "memory-new" }}
      />,
    );

    expect(await screen.findByText("Newest memory detail")).toBeTruthy();
    await act(async () => {
      staleDetail.resolve(ok({
        memory: {
          id: "memory-old",
          content: "Stale memory detail",
          status: "active",
        },
      }));
      staleError.reject(new Error("stale memory failure"));
      await Promise.allSettled([staleDetail.promise, staleError.promise]);
    });

    expect(screen.getByText("Newest memory detail")).toBeTruthy();
    expect(screen.queryByText("Stale memory detail")).toBeNull();
    expect(showToast).not.toHaveBeenCalled();
  });
});
