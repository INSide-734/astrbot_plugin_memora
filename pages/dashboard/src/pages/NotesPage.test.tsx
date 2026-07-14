import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EN_MAP } from "../mock";
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

    fireEvent.change(within(drawer).getByPlaceholderText("New title"), {
      target: { value: "Updated gamma note" },
    });
    fireEvent.click(within(drawer).getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/notes/update", {
        note_id: "note-9",
        field: "title",
        value: "Updated gamma note",
      });
    });
    expect(showToast).toHaveBeenCalledWith("Note updated");

    fireEvent.click(await screen.findByText("Gamma note"));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/notes/detail", { note_id: "note-9" });
    });

    fireEvent.click(screen.getByRole("button", { name: /archive|common\.archive/i }));

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

    const modalInput = await screen.findByPlaceholderText("Tags (comma separated)");
    const modal = modalInput.closest("div")?.parentElement;
    if (!modal) throw new Error("expected create note modal");

    const inputs = within(modal).getAllByRole("textbox");
    fireEvent.change(inputs[0], { target: { value: "Sprint recap" } });
    fireEvent.change(inputs[1], { target: { value: "Summarize the weekly changes." } });
    fireEvent.change(inputs[2], { target: { value: "weekly,  summary , team" } });
    fireEvent.click(within(modal).getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/notes/create", {
        title: "Sprint recap",
        content: "Summarize the weekly changes.",
        tags: ["weekly", "summary", "team"],
      });
    });
    expect(showToast).toHaveBeenCalledWith("Note created");
  });
});
