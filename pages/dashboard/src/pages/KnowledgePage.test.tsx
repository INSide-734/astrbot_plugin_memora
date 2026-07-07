import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge", { limit: "100" });
    });

    fireEvent.change(screen.getByPlaceholderText("Search knowledge base..."), {
      target: { value: "python" },
    });

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge/search", { query: "python" });
    });
    expect(await screen.findByText("Search python")).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("Search knowledge base..."), {
      target: { value: "" },
    });

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge", { limit: "100" });
    });
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

    fireEvent.click(screen.getAllByRole("checkbox")[1]);

    await waitFor(() => {
      expect(screen.getByText("1 selected")).toBeTruthy();
    });

    fireEvent.click(screen.getAllByRole("checkbox")[2]);

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
    expect(showToast).toHaveBeenCalledWith("Deleted 2 entries");
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

    fireEvent.click(await screen.findByText("Gamma entry"));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/knowledge/detail", { entry_id: "kb-9" });
    });

    const titleInput = await screen.findByPlaceholderText("New title");
    const drawer = titleInput.closest("div")?.parentElement;
    if (!drawer) throw new Error("expected knowledge detail drawer");

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
    const reopenedTitleInput = await screen.findByPlaceholderText("New title");
    const reopenedDrawer = reopenedTitleInput.closest("div")?.parentElement;
    if (!reopenedDrawer) throw new Error("expected reopened knowledge detail drawer");

    fireEvent.click(within(reopenedDrawer).getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/knowledge/delete", {
        entry_id: "kb-9",
      });
    });
    expect(showToast).toHaveBeenCalledWith("Entry deleted");
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
