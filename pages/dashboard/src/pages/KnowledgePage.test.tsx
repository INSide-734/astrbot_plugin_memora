import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EN_MAP } from "../mock";
import { ApiRequestError } from "@/types/editing";
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

async function knowledgeRow(title: string): Promise<HTMLTableRowElement> {
  const row = (await screen.findByText(title, { selector: "span[title]" })).closest("tr");
  if (!(row instanceof HTMLTableRowElement)) throw new Error(`Missing knowledge row: ${title}`);
  return row;
}

describe("KnowledgePage", () => {
  let bridge: BridgeMock;
  let showToast: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
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
    localStorage.clear();
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
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge", {
        limit: "100",
        offset: "0",
        sort_by: "updated_at",
        sort_order: "desc",
      });
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
        sort_by: "updated_at",
        sort_order: "desc",
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
        sort_by: "updated_at",
        sort_order: "desc",
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
        sort_by: "updated_at",
        sort_order: "desc",
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
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge", {
        limit: "100",
        offset: "100",
        sort_by: "updated_at",
        sort_order: "desc",
      });
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
        sort_by: "updated_at",
        sort_order: "desc",
        category: "concept",
      });
    });
    expect(screen.getByText("Page 1 of 3")).toBeTruthy();
  });

  it("sorts on the server, resets paging and selection, and only persists table view state", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path !== "page/knowledge") return Promise.resolve(ok({}));
      return Promise.resolve(ok({
        total: 201,
        entries: [{
          entry_id: `kb-${params.offset}`,
          title: `Entry ${params.offset}`,
          category: "fact",
          confidence: 0.8,
          access_count: 3,
        }],
      }));
    });

    render(<KnowledgePage showToast={showToast} />);

    expect(await screen.findByText("Entry 0")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Entry 100")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select knowledge entry Entry 100" }));
    expect(screen.getByText("1 selected")).toBeTruthy();

    bridge.apiGet.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Sort Title ascending" }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge", {
        limit: "100",
        offset: "0",
        sort_by: "title",
        sort_order: "asc",
      });
    });
    expect(screen.queryByText("1 selected")).toBeNull();
    expect(screen.getByText("Page 1 of 3")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Table view" }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "Category" }));

    const preferences = JSON.parse(
      localStorage.getItem("memora.table.knowledge.v1") ?? "null",
    );
    expect(preferences.columnVisibility.category).toBe(false);
    expect(preferences).not.toHaveProperty("sort");
    expect(localStorage.getItem("memora.table.knowledge.sort")).toBeNull();
  });

  it("ignores a stale list response while the latest sorted request is pending", async () => {
    const olderRequest = deferred<ReturnType<typeof ok>>();
    const latestRequest = deferred<ReturnType<typeof ok>>();
    bridge.apiGet
      .mockImplementationOnce(() => olderRequest.promise)
      .mockImplementationOnce(() => latestRequest.promise);

    render(<KnowledgePage showToast={showToast} />);
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "Sort Title ascending" }));
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledTimes(2));

    await act(async () => {
      olderRequest.resolve(ok({
        total: 1,
        entries: [{ entry_id: "kb-old", title: "Old result", category: "fact" }],
      }));
      await olderRequest.promise;
    });

    expect(screen.queryByText("Old result")).toBeNull();
    expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
    expect(showToast).not.toHaveBeenCalled();

    await act(async () => {
      latestRequest.resolve(ok({
        total: 1,
        entries: [{ entry_id: "kb-new", title: "New result", category: "fact" }],
      }));
      await latestRequest.promise;
    });

    expect(await screen.findByText("New result")).toBeTruthy();
    expect(screen.queryByText("Old result")).toBeNull();
    expect(showToast).not.toHaveBeenCalled();
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

  it("opens detail, submits full knowledge changes, and confirms deletion", async () => {
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

    fireEvent.click(await knowledgeRow("Gamma entry"));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge/detail", { entry_id: "kb-9" });
    });

    const drawer = await screen.findByRole("dialog", { name: "Gamma entry" });
    const footer = within(drawer).getByTestId("entity-editor-footer");
    const body = within(drawer).getByTestId("entity-editor-body");
    expect(within(footer).getByRole("button", { name: /^delete$/i })).toBeTruthy();
    expect(within(body).queryByRole("button", { name: /^delete$/i })).toBeNull();
    expect(within(drawer).queryByText(EN_MAP["detail.unsaved"])).toBeNull();

    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    expect(screen.getByRole("dialog", { name: "Gamma entry" })).toBe(drawer);
    expect(within(drawer).queryByLabelText("Choose field to edit")).toBeNull();
    expect(within(drawer).getByLabelText("Title")).toBeTruthy();
    expect(within(drawer).getByLabelText("Content")).toBeTruthy();
    expect(within(drawer).getByLabelText("Category")).toBeTruthy();
    expect(within(drawer).getByLabelText("Confidence")).toBeTruthy();

    fireEvent.change(within(drawer).getByLabelText("Title"), {
      target: { value: "Renamed entry" },
    });
    expect(within(drawer).getByText(EN_MAP["detail.unsaved"])).toBeTruthy();
    fireEvent.click(within(drawer).getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/knowledge/update", {
        entry_id: "kb-9",
        changes: {
          title: "Renamed entry",
          content: "Original knowledge body",
          category: "concept",
          confidence: 0.73,
          tags: [],
        },
      });
    });
    expect(showToast).toHaveBeenCalledWith("Entry updated");

    const reopenedDrawer = await screen.findByRole("dialog", { name: "Gamma entry" });
    fireEvent.click(within(reopenedDrawer).getByRole("button", { name: /^delete$/i }));
    fireEvent.click(await screen.findByRole("button", { name: /confirm|delete/i }));

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

  it("keeps a dirty knowledge draft on a same-entity navigation intent while clean intents refetch", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/knowledge") return Promise.resolve(ok({ entries: [], total: 0 }));
      if (path === "page/knowledge/detail") {
        return Promise.resolve(ok({
          entry: {
            entry_id: params.entry_id,
            title: "Original navigation knowledge",
            content: "Original knowledge content",
            category: "fact",
            confidence: 0.8,
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    const view = render(
      <KnowledgePage
        showToast={showToast}
        navigationTarget={{ requestId: 1, id: "knowledge-same-target" }}
      />,
    );
    const drawer = await screen.findByRole("dialog", { name: "Original navigation knowledge" });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Title"), {
      target: { value: "Dirty navigation knowledge" },
    });

    view.rerender(
      <KnowledgePage
        showToast={showToast}
        navigationTarget={{ requestId: 2, id: "knowledge-same-target" }}
      />,
    );

    expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/knowledge/detail")).toHaveLength(1);
    expect((within(drawer).getByLabelText("Title") as HTMLInputElement).value).toBe("Dirty navigation knowledge");

    fireEvent.click(within(drawer).getByRole("button", { name: /^cancel$/i }));
    view.rerender(
      <KnowledgePage
        showToast={showToast}
        navigationTarget={{ requestId: 3, id: "knowledge-same-target" }}
      />,
    );
    await waitFor(() => {
      expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/knowledge/detail")).toHaveLength(2);
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

    const modal = await screen.findByRole("dialog", { name: "New Knowledge Entry" });
    fireEvent.change(within(modal).getByLabelText("Title"), { target: { value: "Fresh knowledge" } });
    fireEvent.change(within(modal).getByLabelText("Content"), { target: { value: "Created from modal flow" } });
    fireEvent.click(within(modal).getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/knowledge/create", {
        title: "Fresh knowledge",
        content: "Created from modal flow",
        category: "fact",
        confidence: 0,
        tags: [],
      });
    });
    expect(showToast).toHaveBeenCalledWith("Entry created");
  });

  it("keeps a dirty knowledge edit until a different row selection is discarded", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/knowledge") {
        return Promise.resolve(ok({
          entries: [
            { entry_id: "kb-1", title: "First entry", category: "fact", confidence: 0.8 },
            { entry_id: "kb-2", title: "Second entry", category: "concept", confidence: 0.6 },
          ],
        }));
      }
      if (path === "page/knowledge/detail") {
        return Promise.resolve(ok({
          entry: {
            entry_id: params.entry_id,
            title: params.entry_id === "kb-1" ? "First entry" : "Second entry",
            content: params.entry_id === "kb-1" ? "First detail" : "Second detail",
            category: "fact",
            confidence: 0.8,
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<KnowledgePage showToast={showToast} />);

    const firstEntryRow = await knowledgeRow("First entry");
    const secondEntryRow = await knowledgeRow("Second entry");
    fireEvent.click(firstEntryRow);
    const firstDrawer = await screen.findByRole("dialog", { name: "First entry" });
    fireEvent.click(within(firstDrawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(firstDrawer).getByLabelText("Title"), {
      target: { value: "Unsaved first title" },
    });

    fireEvent.click(secondEntryRow);

    expect(bridge.apiGet).not.toHaveBeenCalledWith("page/knowledge/detail", { entry_id: "kb-2" });
    expect(await screen.findByRole("dialog", { name: /leave configuration without saving/i })).toBeTruthy();
    expect((within(firstDrawer).getByLabelText("Title") as HTMLInputElement).value).toBe("Unsaved first title");

    fireEvent.click(screen.getByRole("button", { name: /keep editing/i }));
    expect((within(firstDrawer).getByLabelText("Title") as HTMLInputElement).value).toBe("Unsaved first title");

    fireEvent.click(secondEntryRow);
    fireEvent.click(await screen.findByRole("button", { name: /discard changes and leave/i }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge/detail", { entry_id: "kb-2" });
    });
    const secondDrawer = await screen.findByRole("dialog", { name: "Second entry" });
    expect(within(secondDrawer).getByText("Second detail")).toBeTruthy();
    expect(within(secondDrawer).getByRole("button", { name: /^edit$/i })).toBeTruthy();
    expect(within(secondDrawer).queryByRole("button", { name: /^save$/i })).toBeNull();

    const detailRequestsBeforeCurrentSelection = bridge.apiGet.mock.calls.filter(
      ([path]) => path === "page/knowledge/detail",
    ).length;
    fireEvent.click(secondEntryRow);
    expect(bridge.apiGet.mock.calls.filter(
      ([path]) => path === "page/knowledge/detail",
    )).toHaveLength(detailRequestsBeforeCurrentSelection);
    expect(screen.queryByRole("dialog", { name: /leave configuration without saving/i })).toBeNull();
  });

  it("restores the knowledge edit baseline when Cancel is followed by another Edit", async () => {
    const onDirtyChange = vi.fn();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/knowledge") {
        return Promise.resolve(ok({
          entries: [{ entry_id: "kb-cancel", title: "Baseline title", category: "fact", confidence: 0.8 }],
        }));
      }
      if (path === "page/knowledge/detail") {
        return Promise.resolve(ok({
          entry: {
            entry_id: params.entry_id,
            title: "Baseline title",
            content: "Baseline content",
            category: "fact",
            confidence: 0.8,
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<KnowledgePage showToast={showToast} onDirtyChange={onDirtyChange} />);

    fireEvent.click(await knowledgeRow("Baseline title"));
    const drawer = await screen.findByRole("dialog", { name: "Baseline title" });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Title"), {
      target: { value: "Discarded title" },
    });
    await waitFor(() => {
      expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    });

    fireEvent.click(within(drawer).getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => {
      expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));

    expect((within(drawer).getByLabelText("Title") as HTMLInputElement).value).toBe("Baseline title");
    expect(screen.queryByDisplayValue("Discarded title")).toBeNull();
  });

  it("reports the logical OR of independent knowledge edit and create dirty owners", async () => {
    const onDirtyChange = vi.fn();
    bridge.apiPost.mockResolvedValue(ok({}));
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/knowledge") {
        return Promise.resolve(ok({
          entries: [{ entry_id: "kb-owners", title: "Owner baseline", category: "fact", confidence: 0.8 }],
        }));
      }
      if (path === "page/knowledge/detail") {
        return Promise.resolve(ok({
          entry: {
            entry_id: params.entry_id,
            title: "Owner baseline",
            content: "Owner content",
            category: "fact",
            confidence: 0.8,
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    const firstView = render(<KnowledgePage showToast={showToast} onDirtyChange={onDirtyChange} />);
    const firstCreateButton = await screen.findByRole("button", { name: /new entry/i });
    fireEvent.click(await knowledgeRow("Owner baseline"));
    const firstDrawer = await screen.findByRole("dialog", { name: "Owner baseline" });
    fireEvent.click(within(firstDrawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(firstDrawer).getByLabelText("Title"), { target: { value: "Dirty edit" } });
    await waitFor(() => {
      expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    });

    onDirtyChange.mockClear();
    fireEvent.click(firstCreateButton);
    const cleanCreateDialog = await screen.findByRole("dialog", { name: "New Knowledge Entry" });
    fireEvent.click(within(cleanCreateDialog).getByRole("button", { name: /^cancel$/i }));
    expect(onDirtyChange).not.toHaveBeenCalled();
    expect((within(firstDrawer).getByLabelText("Title") as HTMLInputElement).value).toBe("Dirty edit");

    fireEvent.click(within(firstDrawer).getByRole("button", { name: /^save$/i }));
    await waitFor(() => {
      expect(onDirtyChange).toHaveBeenCalledTimes(1);
      expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    });
    firstView.unmount();
    expect(onDirtyChange).toHaveBeenCalledTimes(2);
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);

    onDirtyChange.mockClear();
    const secondView = render(<KnowledgePage showToast={showToast} onDirtyChange={onDirtyChange} />);
    const secondCreateButton = await screen.findByRole("button", { name: /new entry/i });
    fireEvent.click(await knowledgeRow("Owner baseline"));
    const cleanDrawer = await screen.findByRole("dialog", { name: "Owner baseline" });
    const cleanSheetClose = within(cleanDrawer).getByRole("button", { name: "Close" });
    fireEvent.click(secondCreateButton);
    const dirtyCreateDialog = await screen.findByRole("dialog", { name: "New Knowledge Entry" });
    fireEvent.change(within(dirtyCreateDialog).getByLabelText("Title"), { target: { value: "Dirty create" } });
    await waitFor(() => {
      expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    });

    onDirtyChange.mockClear();
    fireEvent.click(cleanSheetClose);
    expect(onDirtyChange).not.toHaveBeenCalled();
    expect((within(dirtyCreateDialog).getByLabelText("Title") as HTMLInputElement).value).toBe("Dirty create");

    fireEvent.click(within(dirtyCreateDialog).getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => {
      expect(onDirtyChange).toHaveBeenCalledTimes(1);
      expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    });
    secondView.unmount();
    expect(onDirtyChange).toHaveBeenCalledTimes(2);
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });

  it("keeps a rejected knowledge update open in one linked validation summary", async () => {
    const validationError = new ApiRequestError("Update rejected by the server", "validation_failed", {
      title: "A unique title is required",
      "tags.0": "The first tag is invalid",
      unsupported: "The server rejected an unsupported field",
    });
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/knowledge") {
        return Promise.resolve(ok({
          entries: [{ entry_id: "kb-update-error", title: "Original title", category: "fact", confidence: 0.8 }],
        }));
      }
      if (path === "page/knowledge/detail") {
        return Promise.resolve(ok({
          entry: {
            entry_id: params.entry_id,
            title: "Original title",
            content: "Original content",
            category: "fact",
            confidence: 0.8,
            tags: ["old-tag"],
          },
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockRejectedValue(validationError);

    render(<KnowledgePage showToast={showToast} />);

    fireEvent.click(await knowledgeRow("Original title"));
    const drawer = await screen.findByRole("dialog", { name: "Original title" });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Title"), { target: { value: "Rejected title" } });
    fireEvent.click(within(drawer).getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(within(drawer).getAllByText("A unique title is required").length).toBeGreaterThan(0);
      expect(within(drawer).getAllByRole("alert")).toHaveLength(1);
    }, { timeout: 5000 });
    const title = within(drawer).getByLabelText("Title") as HTMLInputElement;
    expect(title.value).toBe("Rejected title");
    expect(title.getAttribute("aria-invalid")).toBe("true");
    expect(title.getAttribute("aria-describedby")).toBeTruthy();
    const tags = within(drawer).getByRole("textbox", { name: "Tags" });
    const tagErrorLink = within(drawer).getByRole("link", { name: "The first tag is invalid" });
    const tagErrorId = tagErrorLink.getAttribute("href")?.slice(1) ?? "";
    expect(tags.getAttribute("aria-describedby")?.split(/\s+/)).toContain(tagErrorId);
    expect(document.querySelectorAll(`[id="${tagErrorId}"]`)).toHaveLength(1);
    expect(within(drawer).getByText("The server rejected an unsupported field")).toBeTruthy();
    expect(within(drawer).queryByRole("link", { name: "The server rejected an unsupported field" })).toBeNull();
    expect(within(drawer).queryByText("Update rejected by the server")).toBeNull();
    expect(title.disabled).toBe(false);
    expect(within(drawer).getByRole("button", { name: /^save$/i })).toBeTruthy();
    expect(showToast).not.toHaveBeenCalledWith("Entry updated");
  });

  it("keeps a rejected knowledge create open with one validation summary", async () => {
    const validationError = new ApiRequestError("Create rejected by the server", "validation_failed", {
      title: "A title with this value already exists",
    });
    bridge.apiGet.mockResolvedValue(ok({ entries: [] }));
    bridge.apiPost.mockRejectedValue(validationError);

    render(<KnowledgePage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("button", { name: /new entry/i }));
    const dialog = await screen.findByRole("dialog", { name: "New Knowledge Entry" });
    fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "Rejected entry" } });
    fireEvent.change(within(dialog).getByLabelText("Content"), { target: { value: "Rejected content" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(within(dialog).getAllByText("A title with this value already exists").length).toBeGreaterThan(0);
      expect(within(dialog).getAllByRole("alert")).toHaveLength(1);
    }, { timeout: 5000 });
    expect(within(dialog).queryByText("Create rejected by the server")).toBeNull();
    const title = within(dialog).getByLabelText("Title") as HTMLInputElement;
    expect(title.value).toBe("Rejected entry");
    expect(title.getAttribute("aria-invalid")).toBe("true");
    expect(title.getAttribute("aria-describedby")).toBeTruthy();
    expect(title.disabled).toBe(false);
    expect(within(dialog).getByRole("button", { name: /^create$/i })).toBeTruthy();
    expect(showToast).not.toHaveBeenCalledWith("Entry created");
  });

  it("locks a pending knowledge create until one successful request closes and resets it", async () => {
    const onDirtyChange = vi.fn();
    const createRequest = deferred<ReturnType<typeof ok>>();
    bridge.apiGet.mockResolvedValue(ok({ entries: [] }));
    bridge.apiPost.mockReturnValue(createRequest.promise);

    render(<KnowledgePage showToast={showToast} onDirtyChange={onDirtyChange} />);

    fireEvent.click(await screen.findByRole("button", { name: /new entry/i }));
    const dialog = await screen.findByRole("dialog", { name: "New Knowledge Entry" });
    fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "Pending entry" } });
    fireEvent.change(within(dialog).getByLabelText("Content"), { target: { value: "Pending content" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));
    fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledTimes(1);
    });
    const title = within(dialog).getByLabelText("Title") as HTMLInputElement;
    expect(title.disabled).toBe(true);
    expect((within(dialog).getByRole("button", { name: /^cancel$/i }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(dialog).getByRole("button", { name: "Close" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(dialog).getByRole("button", { name: /saving/i }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.getByRole("dialog", { name: "New Knowledge Entry" })).toBeTruthy();

    await act(async () => {
      createRequest.resolve(ok({}));
      await createRequest.promise;
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "New Knowledge Entry" })).toBeNull();
      expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    });
    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
  });

  it("locks a pending knowledge update until it completes", async () => {
    const updateRequest = deferred<ReturnType<typeof ok>>();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/knowledge") {
        return Promise.resolve(ok({
          entries: [{ entry_id: "kb-pending-update", title: "Pending original", category: "fact", confidence: 0.8 }],
        }));
      }
      if (path === "page/knowledge/detail") {
        return Promise.resolve(ok({
          entry: {
            entry_id: params.entry_id,
            title: "Pending original",
            content: "Pending original content",
            category: "fact",
            confidence: 0.8,
          },
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockReturnValue(updateRequest.promise);

    render(<KnowledgePage showToast={showToast} />);

    fireEvent.click(await knowledgeRow("Pending original"));
    const drawer = await screen.findByRole("dialog", { name: "Pending original" });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Title"), { target: { value: "Pending update" } });
    fireEvent.click(within(drawer).getByRole("button", { name: /^save$/i }));
    fireEvent.click(within(drawer).getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledTimes(1);
    });
    expect((within(drawer).getByLabelText("Title") as HTMLInputElement).disabled).toBe(true);
    expect((within(drawer).getByRole("button", { name: /^cancel$/i }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(drawer).getByRole("button", { name: "Close" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(drawer).getByRole("button", { name: /saving/i }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      updateRequest.resolve(ok({}));
      await updateRequest.promise;
    });
    await waitFor(() => {
      expect(within(drawer).getByRole("button", { name: /^edit$/i })).toBeTruthy();
      expect(within(drawer).queryByRole("button", { name: /^save$/i })).toBeNull();
    });
    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
  });

  it("resets a discarded create draft before reopening", async () => {
    const onDirtyChange = vi.fn();
    bridge.apiGet.mockResolvedValue(ok({ entries: [] }));
    render(<KnowledgePage showToast={showToast} onDirtyChange={onDirtyChange} />);
    fireEvent.click(await screen.findByRole("button", { name: /new entry/i }));
    const dialog = await screen.findByRole("dialog", { name: "New Knowledge Entry" });
    fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "discard me" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    fireEvent.click(await screen.findByRole("button", { name: "Discard changes and leave" }));
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    fireEvent.click(screen.getByRole("button", { name: /new entry/i }));
    expect((await screen.findByRole("textbox", { name: "Title" }) as HTMLInputElement).value).toBe("");
  });
});
