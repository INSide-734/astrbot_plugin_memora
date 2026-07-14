import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EN_MAP } from "../mock";
import { KnowledgePage } from "./KnowledgePage";

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

describe("KnowledgePage", () => {
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

  it("switches between list and search requests and keeps category filters on list fetches", async () => {
    const localeSpy = vi.spyOn(Date.prototype, "toLocaleDateString");
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/knowledge") {
        return Promise.resolve(ok({
          entries: [
            {
              entry_id: "kb-list",
              title: `Knowledge ${params.category || "all"}`,
              category: params.category || "fact",
              confidence: 0.95,
              updated_at: "2026-06-28T12:00:00Z",
            },
          ],
        }));
      }
      if (path === "page/knowledge/search") {
        return Promise.resolve(ok({
          total: 250,
          entries: [
            {
              entry_id: "kb-search",
              title: `Search ${params.query}`,
              category: "concept",
              confidence: 0.88,
              updated_at: "2026-06-27T12:00:00Z",
            },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<KnowledgePage showToast={showToast} />);

    expect(screen.getByRole("region").getAttribute("data-layout")).toBe("dense");

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge", { limit: "100", offset: "0" });
    });
    expect(localeSpy).toHaveBeenCalledWith("en-US");

    fireEvent.click(screen.getByRole("combobox"));
    const conceptOption = await screen.findByRole("option", { name: "Concept" });
    fireEvent.pointerDown(conceptOption, { button: 0, pointerType: "mouse" });
    fireEvent.pointerUp(conceptOption, { button: 0, pointerType: "mouse" });
    fireEvent.click(conceptOption);

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge", {
        limit: "100",
        offset: "0",
        category: "concept",
      });
    });

    fireEvent.change(screen.getByPlaceholderText("Search knowledge base..."), {
      target: { value: "python" },
    });

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge/search", {
        query: "python",
        limit: "100",
        category: "concept",
      });
    });
    expect(await screen.findByText("Search python")).toBeTruthy();
    expect(screen.getByText("Showing 1 of 250 search results")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Next page" })).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("Search knowledge base..."), {
      target: { value: "" },
    });

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge", {
        limit: "100",
        offset: "0",
        category: "concept",
      });
    });
  });

  it("paginates list results and resets the offset when the category changes", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/knowledge") {
        return Promise.resolve(ok({
          total: 201,
          entries: [{
            entry_id: `kb-${params.offset}`,
            title: `Page offset ${params.offset}`,
            category: params.category || "fact",
            confidence: 0.8,
          }],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<KnowledgePage showToast={showToast} />);

    expect(await screen.findByText("Page offset 0")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select knowledge entry Page offset 0" }));
    expect(screen.getByText("1 selected")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));

    expect(screen.queryByText("1 selected")).toBeNull();

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge", { limit: "100", offset: "100" });
    });

    fireEvent.click(screen.getByRole("combobox"));
    const conceptOption = await screen.findByRole("option", { name: "Concept" });
    fireEvent.pointerDown(conceptOption, { button: 0, pointerType: "mouse" });
    fireEvent.pointerUp(conceptOption, { button: 0, pointerType: "mouse" });
    fireEvent.click(conceptOption);

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge", {
        limit: "100",
        offset: "0",
        category: "concept",
      });
    });
    expect(screen.getByText("Page 1 of 3")).toBeTruthy();
  });

  it("returns to the previous valid page when deletion empties the current page", async () => {
    let offsetPageReads = 0;
    let deleted = false;
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path !== "page/knowledge") return Promise.resolve(ok({}));
      if (params.offset === "100") {
        offsetPageReads += 1;
        return Promise.resolve(ok(offsetPageReads === 1
          ? { total: 101, entries: [{ entry_id: "kb-last", title: "Last entry", category: "fact", confidence: 0.8 }] }
          : { total: 100, entries: [] }));
      }
      return Promise.resolve(ok({ total: deleted ? 100 : 101, entries: [{ entry_id: "kb-first", title: "First entry", category: "fact", confidence: 0.8 }] }));
    });
    bridge.apiPost.mockImplementation(() => { deleted = true; return Promise.resolve(ok({})); });

    render(<KnowledgePage showToast={showToast} />);
    expect(await screen.findByText("First entry")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Last entry")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select knowledge entry Last entry" }));
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(screen.getByText("Page 1 of 1")).toBeTruthy();
      expect(screen.getByText("First entry")).toBeTruthy();
    });
    expect(screen.queryByText("1 selected")).toBeNull();
  });

  it("shows the batch bar and deletes selected knowledge entries", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      entries: [
        {
          entry_id: "kb-1",
          title: "Alpha entry",
          category: "fact",
          confidence: 0.91,
          updated_at: "2026-06-28T12:00:00Z",
        },
        {
          entry_id: "kb-2",
          title: "Beta entry",
          category: "rule",
          confidence: 0.67,
          updated_at: "2026-06-27T12:00:00Z",
        },
      ],
    }));
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<KnowledgePage showToast={showToast} />);

    expect(await screen.findByText("Alpha entry")).toBeTruthy();
    expect(screen.getByText("Beta entry")).toBeTruthy();

    const alphaCheckbox = screen.getByRole("checkbox", { name: "Select knowledge entry Alpha entry" });
    fireEvent.click(alphaCheckbox);

    await waitFor(() => {
      expect(screen.getByText("1 selected")).toBeTruthy();
    });
    expect(alphaCheckbox.closest("tr")?.getAttribute("data-state")).toBe("selected");

    fireEvent.click(screen.getByRole("checkbox", { name: "Select knowledge entry Beta entry" }));

    await waitFor(() => {
      expect(screen.getByText("2 selected")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/knowledge/batch", {
        entry_ids: ["kb-1", "kb-2"],
        action: "delete",
      });
    });
    expect(showToast).toHaveBeenCalledWith(EN_MAP["toast.batchDeleted"].replace("{0}", "2"));
  });

  it("opens detail, updates an entry, and supports deleting it from the detail panel", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/knowledge") {
        return Promise.resolve(ok({
          entries: [
            {
              entry_id: "kb-9",
              title: "Gamma entry",
              category: "concept",
              confidence: 0.73,
              updated_at: "2026-06-28T12:00:00Z",
            },
          ],
        }));
      }
      if (path === "page/knowledge/detail") {
        return Promise.resolve(ok({
          entry: {
            entry_id: params.entry_id,
            title: "Gamma entry",
            content: "Original knowledge body",
            category: "concept",
            confidence: 0.73,
            access_count: 4,
            updated_at: "2026-06-28T12:00:00Z",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<KnowledgePage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("button", { name: "Open knowledge entry Gamma entry" }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge/detail", { entry_id: "kb-9" });
    });

    const drawer = await screen.findByRole("dialog", { name: "Gamma entry" });

    fireEvent.change(within(drawer).getByPlaceholderText("New title"), {
      target: { value: "Renamed entry" },
    });
    fireEvent.click(within(drawer).getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/knowledge/update", {
        entry_id: "kb-9",
        field: "title",
        value: "Renamed entry",
      });
    });
    expect(showToast).toHaveBeenCalledWith("Entry updated");

    fireEvent.click(await screen.findByText("Gamma entry"));
    const reopenedDrawer = await screen.findByRole("dialog", { name: "Gamma entry" });

    fireEvent.click(within(reopenedDrawer).getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/knowledge/delete", {
        entry_id: "kb-9",
      });
    });
    expect(showToast).toHaveBeenCalledWith("Entry deleted");
  });

  it("opens the exact knowledge detail for each navigation target request", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/knowledge") {
        return Promise.resolve(ok({ entries: [], total: 0 }));
      }
      if (path === "page/knowledge/detail") {
        return Promise.resolve(ok({
          entry: {
            entry_id: params.entry_id,
            title: "Search target knowledge",
            content: "Knowledge target body",
            category: "fact",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    const { rerender } = render(
      <KnowledgePage
        showToast={showToast}
        navigationTarget={{ requestId: 1, id: "knowledge-search-target" }}
      />,
    );

    expect(await screen.findByRole("dialog", {
      name: "Search target knowledge",
    })).toBeTruthy();
    expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge/detail", {
      entry_id: "knowledge-search-target",
    });

    rerender(
      <KnowledgePage
        showToast={showToast}
        navigationTarget={{ requestId: 2, id: "knowledge-search-target" }}
      />,
    );

    await waitFor(() => {
      expect(bridge.apiGet.mock.calls.filter(
        ([path]) => path === "page/knowledge/detail",
      )).toHaveLength(2);
    });
  });

  it("keeps the newest knowledge detail when an older target resolves last", async () => {
    const staleDetail = deferred<ReturnType<typeof ok>>();
    const staleError = deferred<ReturnType<typeof ok>>();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/knowledge") {
        return Promise.resolve(ok({ entries: [], total: 0 }));
      }
      if (path === "page/knowledge/detail" && params.entry_id === "knowledge-old") {
        return staleDetail.promise;
      }
      if (path === "page/knowledge/detail" && params.entry_id === "knowledge-error") {
        return staleError.promise;
      }
      if (path === "page/knowledge/detail" && params.entry_id === "knowledge-new") {
        return Promise.resolve(ok({
          entry: {
            entry_id: "knowledge-new",
            title: "Newest knowledge detail",
            content: "Newest knowledge body",
            category: "fact",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    const view = render(
      <KnowledgePage
        showToast={showToast}
        navigationTarget={{ requestId: 1, id: "knowledge-old" }}
      />,
    );
    view.rerender(
      <KnowledgePage
        showToast={showToast}
        navigationTarget={{ requestId: 2, id: "knowledge-error" }}
      />,
    );
    view.rerender(
      <KnowledgePage
        showToast={showToast}
        navigationTarget={{ requestId: 3, id: "knowledge-new" }}
      />,
    );

    expect(await screen.findByRole("dialog", {
      name: "Newest knowledge detail",
    })).toBeTruthy();
    await act(async () => {
      staleDetail.resolve(ok({
        entry: {
          entry_id: "knowledge-old",
          title: "Stale knowledge detail",
          content: "Stale knowledge body",
          category: "fact",
        },
      }));
      staleError.reject(new Error("stale knowledge failure"));
      await Promise.allSettled([staleDetail.promise, staleError.promise]);
    });

    expect(screen.getByRole("dialog", {
      name: "Newest knowledge detail",
    })).toBeTruthy();
    expect(screen.queryByRole("dialog", {
      name: "Stale knowledge detail",
    })).toBeNull();
    expect(showToast).not.toHaveBeenCalled();
  });

  it("creates a new knowledge entry from the modal and refreshes the list", async () => {
    bridge.apiGet.mockResolvedValue(ok({ entries: [] }));
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<KnowledgePage showToast={showToast} />);

    expect(await screen.findByText("No data")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /new entry/i }));

    const modalTitle = await screen.findByText("New Knowledge Entry");
    const modal = modalTitle.closest("div")?.parentElement;
    if (!modal) throw new Error("expected create modal");

    const inputs = within(modal).getAllByRole("textbox");
    fireEvent.change(inputs[0], { target: { value: "Fresh knowledge" } });
    fireEvent.change(inputs[1], { target: { value: "Created from modal flow" } });
    fireEvent.click(within(modal).getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/knowledge/create", {
        title: "Fresh knowledge",
        content: "Created from modal flow",
        category: "fact",
      });
    });
    expect(showToast).toHaveBeenCalledWith("Entry created");
  });
});
