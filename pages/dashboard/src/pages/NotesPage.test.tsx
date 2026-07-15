import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EN_MAP } from "../mock";
import { ApiRequestError } from "@/types/editing";
import { NotesPage } from "./NotesPage";

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

describe("NotesPage", () => {
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

  it("switches between list and search requests for notes", async () => {
    const localeSpy = vi.spyOn(Date.prototype, "toLocaleDateString");
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/notes") {
        return Promise.resolve(ok({
          notes: [
            {
              note_id: "note-list",
              title: `List ${params.status || "all"}`,
              content: "List note content",
              tags: ["alpha"],
              status: params.status || "active",
              version: 1,
              updated_at: "2026-06-28T12:00:00Z",
            },
          ],
        }));
      }
      if (path === "page/notes/search") {
        return Promise.resolve(ok({
          notes: [
            {
              note_id: "note-search",
              title: `Search ${params.query}`,
              content: "Search note content",
              tags: ["query"],
              status: "active",
              version: 2,
              updated_at: "2026-06-27T12:00:00Z",
            },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<NotesPage showToast={showToast} />);

    expect(screen.getByRole("region").getAttribute("data-layout")).toBe("standard");

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/notes", { limit: "100" });
    });
    expect(localeSpy).toHaveBeenCalledWith("en-US");

    fireEvent.click(screen.getByRole("checkbox", { name: "Select note List all" }));
    expect(screen.getByText("1 selected")).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("Search notes..."), {
      target: { value: "python" },
    });

    expect(screen.queryByText("1 selected")).toBeNull();

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/notes/search", { query: "python" });
    });
    expect(await screen.findByText("Search python")).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("Search notes..."), {
      target: { value: "" },
    });

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/notes", { limit: "100" });
    });
  });

  it("shows the batch bar and archives selected notes", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      notes: [
        {
          note_id: "note-1",
          title: "Alpha note",
          content: "Alpha body",
          tags: ["work"],
          status: "active",
          version: 1,
          updated_at: "2026-06-28T12:00:00Z",
        },
        {
          note_id: "note-2",
          title: "Beta note",
          content: "Beta body",
          tags: ["team"],
          status: "active",
          version: 3,
          updated_at: "2026-06-27T12:00:00Z",
        },
      ],
    }));
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<NotesPage showToast={showToast} />);

    expect(await screen.findByText("Alpha note")).toBeTruthy();
    expect(screen.getByText("Beta note")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Select all notes" }));

    await waitFor(() => {
      expect(screen.getByText((content) => content.includes("2 selected"))).toBeTruthy();
    });
    expect(
      screen.getByRole("checkbox", { name: "Select note Alpha note" })
        .closest("[data-state]")
        ?.getAttribute("data-state"),
    ).toBe("selected");

    fireEvent.click(screen.getByRole("button", { name: /archive|common\.archive/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/notes/batch", {
        note_ids: ["note-1", "note-2"],
        action: "archive",
      });
    });
    expect(showToast).toHaveBeenCalledWith(EN_MAP["toast.batchArchived"].replace("{0}", "2"));
  });

  it("requires an exact confirmation before deleting a large selected-note batch", async () => {
    bridge.apiGet.mockResolvedValue(ok({ notes: Array.from({ length: 20 }, (_, index) => ({ note_id: `note-${index}`, title: `Note ${index}`, status: "active" })) }));
    bridge.apiPost.mockResolvedValue(ok({}));
    render(<NotesPage showToast={showToast} />);
    expect(await screen.findByText("Note 0")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Select all notes" }));
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    const dialog = await screen.findByRole("dialog");
    expect(bridge.apiPost).not.toHaveBeenCalledWith("page/notes/batch", expect.anything());
    const confirmation = within(dialog).getByRole("textbox");
    fireEvent.change(confirmation, { target: { value: "20" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^delete$/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/notes/batch", { note_ids: Array.from({ length: 20 }, (_, index) => `note-${index}`), action: "delete" }));
  });

  it("opens note detail, saves edits, and archives the note from the detail panel", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/notes") {
        return Promise.resolve(ok({
          notes: [
            {
              note_id: "note-9",
              title: "Gamma note",
              content: "Original card preview",
              tags: ["ops", "design"],
              status: "active",
              version: 2,
              updated_at: "2026-06-28T12:00:00Z",
            },
          ],
        }));
      }
      if (path === "page/notes/detail") {
        return Promise.resolve(ok({
          note: {
            note_id: params.note_id,
            title: "Gamma note",
            content: "Detailed note body",
            tags: ["ops", "design"],
            status: "active",
            version: 2,
            updated_at: "2026-06-28T12:00:00Z",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<NotesPage showToast={showToast} />);

    fireEvent.keyDown(await screen.findByRole("button", { name: "Open note Gamma note" }), { key: " " });

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/notes/detail", { note_id: "note-9" });
    });

    const drawer = await screen.findByRole("dialog", { name: "Gamma note" });
    expect(within(drawer).getByText(
      `Updated: ${new Date("2026-06-28T12:00:00Z").toLocaleDateString("en-US")}`,
    )).toBeTruthy();
    expect(within(drawer).getByText("v2")).toBeTruthy();

    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    expect(within(drawer).queryByLabelText("Choose field to edit")).toBeNull();
    expect(within(drawer).getByLabelText("Title")).toBeTruthy();
    expect(within(drawer).getByLabelText("Content")).toBeTruthy();
    expect(within(drawer).getByRole("textbox", { name: "Tags" })).toBeTruthy();
    expect(within(drawer).getByLabelText("Status")).toBeTruthy();

    fireEvent.change(within(drawer).getByLabelText("Title"), {
      target: { value: "Updated gamma note" },
    });
    fireEvent.click(within(drawer).getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/notes/update", {
        note_id: "note-9",
        changes: {
          title: "Updated gamma note",
          content: "Detailed note body",
          tags: ["ops", "design"],
          status: "active",
        },
      });
    });
    expect(showToast).toHaveBeenCalledWith("Note updated");

    fireEvent.click(within(drawer).getByRole("button", { name: /archive|common\.archive/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/notes/archive", {
        note_id: "note-9",
      });
    });
    expect(showToast).toHaveBeenCalledWith("Note archived");
  });

  it("opens the exact note detail for each navigation target request", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/notes") {
        return Promise.resolve(ok({ notes: [], total: 0 }));
      }
      if (path === "page/notes/detail") {
        return Promise.resolve(ok({
          note: {
            note_id: params.note_id,
            title: "Search target note",
            content: "Note target body",
            status: "active",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    const { rerender } = render(
      <NotesPage
        showToast={showToast}
        navigationTarget={{ requestId: 1, id: "note-search-target" }}
      />,
    );

    expect(await screen.findByRole("dialog", {
      name: "Search target note",
    })).toBeTruthy();
    expect(bridge.apiGet).toHaveBeenCalledWith("page/notes/detail", {
      note_id: "note-search-target",
    });

    rerender(
      <NotesPage
        showToast={showToast}
        navigationTarget={{ requestId: 2, id: "note-search-target" }}
      />,
    );

    await waitFor(() => {
      expect(bridge.apiGet.mock.calls.filter(
        ([path]) => path === "page/notes/detail",
      )).toHaveLength(2);
    });
  });

  it("keeps a dirty note draft on a same-entity navigation intent while clean intents refetch", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/notes") return Promise.resolve(ok({ notes: [], total: 0 }));
      if (path === "page/notes/detail") {
        return Promise.resolve(ok({
          note: {
            note_id: params.note_id,
            title: "Original navigation note",
            content: "Original note content",
            status: "active",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    const view = render(
      <NotesPage
        showToast={showToast}
        navigationTarget={{ requestId: 1, id: "note-same-target" }}
      />,
    );
    const drawer = await screen.findByRole("dialog", { name: "Original navigation note" });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Title"), {
      target: { value: "Dirty navigation note" },
    });

    view.rerender(
      <NotesPage
        showToast={showToast}
        navigationTarget={{ requestId: 2, id: "note-same-target" }}
      />,
    );

    expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/notes/detail")).toHaveLength(1);
    expect((within(drawer).getByLabelText("Title") as HTMLInputElement).value).toBe("Dirty navigation note");

    fireEvent.click(within(drawer).getByRole("button", { name: /^cancel$/i }));
    view.rerender(
      <NotesPage
        showToast={showToast}
        navigationTarget={{ requestId: 3, id: "note-same-target" }}
      />,
    );
    await waitFor(() => {
      expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/notes/detail")).toHaveLength(2);
    });
  });

  it("keeps the newest note detail when an older target resolves last", async () => {
    const staleDetail = deferred<ReturnType<typeof ok>>();
    const staleError = deferred<ReturnType<typeof ok>>();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/notes") {
        return Promise.resolve(ok({ notes: [], total: 0 }));
      }
      if (path === "page/notes/detail" && params.note_id === "note-old") {
        return staleDetail.promise;
      }
      if (path === "page/notes/detail" && params.note_id === "note-error") {
        return staleError.promise;
      }
      if (path === "page/notes/detail" && params.note_id === "note-new") {
        return Promise.resolve(ok({
          note: {
            note_id: "note-new",
            title: "Newest note detail",
            content: "Newest note body",
            status: "active",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    const view = render(
      <NotesPage
        showToast={showToast}
        navigationTarget={{ requestId: 1, id: "note-old" }}
      />,
    );
    view.rerender(
      <NotesPage
        showToast={showToast}
        navigationTarget={{ requestId: 2, id: "note-error" }}
      />,
    );
    view.rerender(
      <NotesPage
        showToast={showToast}
        navigationTarget={{ requestId: 3, id: "note-new" }}
      />,
    );

    expect(await screen.findByRole("dialog", {
      name: "Newest note detail",
    })).toBeTruthy();
    await act(async () => {
      staleDetail.resolve(ok({
        note: {
          note_id: "note-old",
          title: "Stale note detail",
          content: "Stale note body",
          status: "active",
        },
      }));
      staleError.reject(new Error("stale note failure"));
      await Promise.allSettled([staleDetail.promise, staleError.promise]);
    });

    expect(screen.getByRole("dialog", {
      name: "Newest note detail",
    })).toBeTruthy();
    expect(screen.queryByRole("dialog", {
      name: "Stale note detail",
    })).toBeNull();
    expect(showToast).not.toHaveBeenCalled();
  });

  it("creates a new note from the modal and normalizes comma-separated tags", async () => {
    bridge.apiGet.mockResolvedValue(ok({ notes: [] }));
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<NotesPage showToast={showToast} />);

    expect(await screen.findByText("No data")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /new note/i }));

    const modal = await screen.findByRole("dialog", { name: "New Note" });
    fireEvent.change(within(modal).getByLabelText("Title"), { target: { value: "Sprint recap" } });
    fireEvent.change(within(modal).getByLabelText("Content"), { target: { value: "Summarize the weekly changes." } });
    const tags = within(modal).getByRole("textbox", { name: "Tags" });
    for (const tag of ["weekly", "summary", "team"]) {
      fireEvent.change(tags, { target: { value: tag } });
      fireEvent.keyDown(tags, { key: "Enter" });
    }
    fireEvent.click(within(modal).getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/notes/create", {
        title: "Sprint recap",
        content: "Summarize the weekly changes.",
        tags: ["weekly", "summary", "team"],
        status: "active",
      });
    });
    expect(showToast).toHaveBeenCalledWith("Note created");
  });

  it("keeps a dirty note edit until click and keyboard selections are discarded", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/notes") {
        return Promise.resolve(ok({
          notes: [
            { note_id: "note-1", title: "First note", content: "First card", status: "active", version: 1 },
            { note_id: "note-2", title: "Second note", content: "Second card", status: "active", version: 2 },
          ],
        }));
      }
      if (path === "page/notes/detail") {
        return Promise.resolve(ok({
          note: {
            note_id: params.note_id,
            title: params.note_id === "note-1" ? "First note" : "Second note",
            content: params.note_id === "note-1" ? "First detail" : "Second detail",
            status: "active",
            version: params.note_id === "note-1" ? 1 : 2,
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<NotesPage showToast={showToast} />);

    const firstNoteButton = await screen.findByRole("button", { name: "Open note First note" });
    const secondNoteButton = screen.getByRole("button", { name: "Open note Second note" });
    fireEvent.click(firstNoteButton);
    const firstDrawer = await screen.findByRole("dialog", { name: "First note" });
    fireEvent.click(within(firstDrawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(firstDrawer).getByLabelText("Title"), {
      target: { value: "Unsaved first title" },
    });

    fireEvent.click(secondNoteButton);
    expect(bridge.apiGet).not.toHaveBeenCalledWith("page/notes/detail", { note_id: "note-2" });
    expect(await screen.findByRole("dialog", { name: /leave configuration without saving/i })).toBeTruthy();
    expect((within(firstDrawer).getByLabelText("Title") as HTMLInputElement).value).toBe("Unsaved first title");

    fireEvent.click(screen.getByRole("button", { name: /keep editing/i }));
    expect((within(firstDrawer).getByLabelText("Title") as HTMLInputElement).value).toBe("Unsaved first title");

    fireEvent.keyDown(secondNoteButton, { key: "Enter" });
    expect(bridge.apiGet).not.toHaveBeenCalledWith("page/notes/detail", { note_id: "note-2" });
    fireEvent.click(await screen.findByRole("button", { name: /discard changes and leave/i }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/notes/detail", { note_id: "note-2" });
    });
    const secondDrawer = await screen.findByRole("dialog", { name: "Second note" });
    expect(within(secondDrawer).getByText("Second detail")).toBeTruthy();
    expect(within(secondDrawer).getByRole("button", { name: /^edit$/i })).toBeTruthy();
    expect(within(secondDrawer).queryByRole("button", { name: /^save$/i })).toBeNull();

    const detailRequestsBeforeCurrentSelection = bridge.apiGet.mock.calls.filter(
      ([path]) => path === "page/notes/detail",
    ).length;
    fireEvent.click(secondNoteButton);
    expect(bridge.apiGet.mock.calls.filter(
      ([path]) => path === "page/notes/detail",
    )).toHaveLength(detailRequestsBeforeCurrentSelection);
    expect(screen.queryByRole("dialog", { name: /leave configuration without saving/i })).toBeNull();
  });

  it("restores the note edit baseline when Cancel is followed by another Edit", async () => {
    const onDirtyChange = vi.fn();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/notes") {
        return Promise.resolve(ok({
          notes: [{ note_id: "note-cancel", title: "Baseline note", content: "Baseline card", status: "active" }],
        }));
      }
      if (path === "page/notes/detail") {
        return Promise.resolve(ok({
          note: {
            note_id: params.note_id,
            title: "Baseline note",
            content: "Baseline content",
            status: "active",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<NotesPage showToast={showToast} onDirtyChange={onDirtyChange} />);

    fireEvent.click(await screen.findByRole("button", { name: "Open note Baseline note" }));
    const drawer = await screen.findByRole("dialog", { name: "Baseline note" });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Title"), {
      target: { value: "Discarded note title" },
    });
    await waitFor(() => {
      expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    });

    fireEvent.click(within(drawer).getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => {
      expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));

    expect((within(drawer).getByLabelText("Title") as HTMLInputElement).value).toBe("Baseline note");
    expect(screen.queryByDisplayValue("Discarded note title")).toBeNull();
  });

  it("reports the logical OR of independent note edit and create dirty owners", async () => {
    const onDirtyChange = vi.fn();
    bridge.apiPost.mockResolvedValue(ok({}));
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/notes") {
        return Promise.resolve(ok({
          notes: [{ note_id: "note-owners", title: "Owner note", content: "Owner card", status: "active" }],
        }));
      }
      if (path === "page/notes/detail") {
        return Promise.resolve(ok({
          note: {
            note_id: params.note_id,
            title: "Owner note",
            content: "Owner content",
            status: "active",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    const firstView = render(<NotesPage showToast={showToast} onDirtyChange={onDirtyChange} />);
    const firstCreateButton = await screen.findByRole("button", { name: /new note/i });
    fireEvent.click(screen.getByRole("button", { name: "Open note Owner note" }));
    const firstDrawer = await screen.findByRole("dialog", { name: "Owner note" });
    fireEvent.click(within(firstDrawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(firstDrawer).getByLabelText("Title"), { target: { value: "Dirty edit" } });
    await waitFor(() => {
      expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    });

    onDirtyChange.mockClear();
    fireEvent.click(firstCreateButton);
    const cleanCreateDialog = await screen.findByRole("dialog", { name: "New Note" });
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
    const secondView = render(<NotesPage showToast={showToast} onDirtyChange={onDirtyChange} />);
    const secondCreateButton = await screen.findByRole("button", { name: /new note/i });
    fireEvent.click(screen.getByRole("button", { name: "Open note Owner note" }));
    const cleanDrawer = await screen.findByRole("dialog", { name: "Owner note" });
    const cleanSheetClose = within(cleanDrawer).getByRole("button", { name: "Close" });
    fireEvent.click(secondCreateButton);
    const dirtyCreateDialog = await screen.findByRole("dialog", { name: "New Note" });
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

  it("keeps a rejected note update open with structured field and form errors", async () => {
    const validationError = new ApiRequestError("Update rejected by the server", "validation_failed", {
      title: "A unique note title is required",
    });
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/notes") {
        return Promise.resolve(ok({
          notes: [{ note_id: "note-update-error", title: "Original note", content: "Original card", status: "active" }],
        }));
      }
      if (path === "page/notes/detail") {
        return Promise.resolve(ok({
          note: {
            note_id: params.note_id,
            title: "Original note",
            content: "Original content",
            status: "active",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockRejectedValue(validationError);

    render(<NotesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("button", { name: "Open note Original note" }));
    const drawer = await screen.findByRole("dialog", { name: "Original note" });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Title"), { target: { value: "Rejected note" } });
    fireEvent.click(within(drawer).getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(within(drawer).getAllByText("A unique note title is required").length).toBeGreaterThan(0);
      expect(within(drawer).getByText("Update rejected by the server").closest('[role="alert"]')).toBeTruthy();
    }, { timeout: 5000 });
    const title = within(drawer).getByLabelText("Title") as HTMLInputElement;
    expect(title.value).toBe("Rejected note");
    expect(title.getAttribute("aria-invalid")).toBe("true");
    expect(title.getAttribute("aria-describedby")).toBeTruthy();
    expect(title.disabled).toBe(false);
    expect(within(drawer).getByRole("button", { name: /^save$/i })).toBeTruthy();
    expect(showToast).not.toHaveBeenCalledWith("Note updated");
  });

  it("keeps a rejected note create open with structured field and form errors", async () => {
    const validationError = new ApiRequestError("Create rejected by the server", "validation_failed", {
      title: "A note with this title already exists",
    });
    bridge.apiGet.mockResolvedValue(ok({ notes: [] }));
    bridge.apiPost.mockRejectedValue(validationError);

    render(<NotesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("button", { name: /new note/i }));
    const dialog = await screen.findByRole("dialog", { name: "New Note" });
    fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "Rejected note" } });
    fireEvent.change(within(dialog).getByLabelText("Content"), { target: { value: "Rejected content" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(within(dialog).getAllByText("A note with this title already exists").length).toBeGreaterThan(0);
      expect(within(dialog).getByText("Create rejected by the server").closest('[role="alert"]')).toBeTruthy();
    }, { timeout: 5000 });
    const title = within(dialog).getByLabelText("Title") as HTMLInputElement;
    expect(title.value).toBe("Rejected note");
    expect(title.getAttribute("aria-invalid")).toBe("true");
    expect(title.getAttribute("aria-describedby")).toBeTruthy();
    expect(title.disabled).toBe(false);
    expect(within(dialog).getByRole("button", { name: /^create$/i })).toBeTruthy();
    expect(showToast).not.toHaveBeenCalledWith("Note created");
  });

  it("locks a pending note create until one successful request closes and resets it", async () => {
    const onDirtyChange = vi.fn();
    const createRequest = deferred<ReturnType<typeof ok>>();
    bridge.apiGet.mockResolvedValue(ok({ notes: [] }));
    bridge.apiPost.mockReturnValue(createRequest.promise);

    render(<NotesPage showToast={showToast} onDirtyChange={onDirtyChange} />);

    fireEvent.click(await screen.findByRole("button", { name: /new note/i }));
    const dialog = await screen.findByRole("dialog", { name: "New Note" });
    fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "Pending note" } });
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
    expect(screen.getByRole("dialog", { name: "New Note" })).toBeTruthy();

    await act(async () => {
      createRequest.resolve(ok({}));
      await createRequest.promise;
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "New Note" })).toBeNull();
      expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    });
    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
  });

  it("locks a pending note update until it completes", async () => {
    const updateRequest = deferred<ReturnType<typeof ok>>();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/notes") {
        return Promise.resolve(ok({
          notes: [{ note_id: "note-pending-update", title: "Pending original", content: "Pending card", status: "active" }],
        }));
      }
      if (path === "page/notes/detail") {
        return Promise.resolve(ok({
          note: {
            note_id: params.note_id,
            title: "Pending original",
            content: "Pending original content",
            status: "active",
          },
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockReturnValue(updateRequest.promise);

    render(<NotesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("button", { name: "Open note Pending original" }));
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
    bridge.apiGet.mockResolvedValue(ok({ notes: [] }));
    render(<NotesPage showToast={showToast} onDirtyChange={onDirtyChange} />);
    fireEvent.click(await screen.findByRole("button", { name: /new note/i }));
    const dialog = await screen.findByRole("dialog", { name: "New Note" });
    fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "discard me" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    fireEvent.click(await screen.findByRole("button", { name: "Discard changes and leave" }));
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    fireEvent.click(screen.getByRole("button", { name: /new note/i }));
    expect((await screen.findByRole("textbox", { name: "Title" }) as HTMLInputElement).value).toBe("");
  });
});
