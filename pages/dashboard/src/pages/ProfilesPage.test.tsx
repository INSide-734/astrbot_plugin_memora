import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EN_MAP } from "../mock";
import { ProfilesPage } from "./ProfilesPage";
import { ApiRequestError } from "@/types/editing";

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
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("ProfilesPage", () => {
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

  it("loads profile statistics and renders aggregated tag totals", async () => {
    const localeSpy = vi.spyOn(Date.prototype, "toLocaleDateString");
    bridge.apiGet.mockResolvedValue(ok({
      total: 2,
      profiles: [
        {
          user_id: "user-1",
          display_name: "Alice",
          tag_count: 3,
          top_interests: ["testing", "python"],
          last_seen: "2026-06-28T12:00:00Z",
        },
        {
          user_id: "user-2",
          display_name: "Bob",
          tags: [
            { name: "ops", confidence: 0.8 },
            { name: "graphs", confidence: 0.5 },
          ],
          top_interests: ["ops"],
          last_seen: "2026-06-27T12:00:00Z",
        },
      ],
    }));

    render(<ProfilesPage showToast={showToast} />);

    expect(screen.getByRole("region").getAttribute("data-layout")).toBe("dense");

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/profiles", {
        limit: "100",
        offset: "0",
        sort_by: "last_seen_at",
        sort_order: "desc",
      });
    });

    expect(await screen.findByText("Alice")).toBeTruthy();
    expect(screen.getByText("Bob")).toBeTruthy();
    expect(screen.getByText("Profiles")).toBeTruthy();
    expect(screen.getAllByText("Tags").length).toBeGreaterThan(1);
    expect(screen.getByText("User ID")).toBeTruthy();
    expect(screen.getAllByText("2").length).toBeGreaterThan(0);
    expect(screen.getByText((content) => content.trim() === "5")).toBeTruthy();
    expect(screen.getByText("testing")).toBeTruthy();
    expect(screen.getByText(new Date("2026-06-28T12:00:00Z").toLocaleDateString("en-US"))).toBeTruthy();
    expect(localeSpy).toHaveBeenCalledWith("en-US");
    expect(screen.getByRole("navigation", { name: "Pagination" })).toBeTruthy();
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("counts canonical tags without tag_count", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      total: 1,
      profiles: [{
        user_id: "canonical",
        display_name: "Canonical",
        tags: [
          { category: "interest", value: "testing", confidence: 0.9 },
          { category: "custom", value: "graphs", confidence: 0.8 },
        ],
        top_interests: [],
        last_seen: "2026-06-28T12:00:00Z",
      }],
    }));
    render(<ProfilesPage showToast={showToast} />);
    const row = (await screen.findByText("Canonical")).closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("2")).toBeTruthy();
  });

  it("pages through profile results using backend offsets", async () => {
    bridge.apiGet.mockImplementation((_path: string, params: Record<string, string>) => Promise.resolve(ok({
      total: 201,
      profiles: [{
        user_id: `user-${params.offset}`,
        display_name: `Offset ${params.offset}`,
      }],
    })));

    render(<ProfilesPage showToast={showToast} />);

    expect(await screen.findByText("Offset 0")).toBeTruthy();
    expect(screen.getByText("Page 1 of 3")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select profile Offset 0" }));
    expect(screen.getByText("1 selected")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));

    expect(screen.queryByText("1 selected")).toBeNull();

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/profiles", {
        limit: "100",
        offset: "100",
        sort_by: "last_seen_at",
        sort_order: "desc",
      });
    });
    expect(await screen.findByText("Offset 100")).toBeTruthy();
    expect(screen.getByText("Page 2 of 3")).toBeTruthy();
  });

  it("sorts profiles on the server and resets the selected page", async () => {
    bridge.apiGet.mockImplementation((_path: string, params: Record<string, string>) => Promise.resolve(ok({
      total: 201,
      profiles: [{
        user_id: `user-${params.offset}`,
        display_name: `Offset ${params.offset}`,
      }],
    })));

    render(<ProfilesPage showToast={showToast} />);

    expect(await screen.findByText("Offset 0")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Offset 100")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select profile Offset 100" }));
    expect(screen.getByText("1 selected")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Sort Name ascending" }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/profiles", {
        limit: "100",
        offset: "0",
        sort_by: "display_name",
        sort_order: "asc",
      });
    });
    expect(screen.queryByText("1 selected")).toBeNull();
  });

  it("returns to the previous valid page when deletion empties the current page", async () => {
    let offsetPageReads = 0;
    let deleted = false;
    bridge.apiGet.mockImplementation((_path: string, params: Record<string, string>) => {
      if (params.offset === "100") {
        offsetPageReads += 1;
        return Promise.resolve(ok(offsetPageReads === 1
          ? { total: 101, profiles: [{ user_id: "user-last", display_name: "Last profile" }] }
          : { total: 100, profiles: [] }));
      }
      return Promise.resolve(ok({ total: deleted ? 100 : 101, profiles: [{ user_id: "user-first", display_name: "First profile" }] }));
    });
    bridge.apiPost.mockImplementation(() => { deleted = true; return Promise.resolve(ok({})); });

    render(<ProfilesPage showToast={showToast} />);
    expect(await screen.findByText("First profile")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Last profile")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select profile Last profile" }));
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(screen.getByText("Page 1 of 1")).toBeTruthy();
      expect(screen.getByText("First profile")).toBeTruthy();
    });
    expect(screen.queryByText("1 selected")).toBeNull();
  });

  it("shows the batch bar and deletes selected profiles", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      total: 2,
      profiles: [
        {
          user_id: "user-1",
          display_name: "Alice",
          tag_count: 2,
          top_interests: ["testing"],
          last_seen: "2026-06-28T12:00:00Z",
        },
        {
          user_id: "user-2",
          display_name: "Bob",
          tag_count: 1,
          top_interests: ["ops"],
          last_seen: "2026-06-27T12:00:00Z",
        },
      ],
    }));
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<ProfilesPage showToast={showToast} />);

    expect(await screen.findByText("Alice")).toBeTruthy();
    expect(screen.getByText("Bob")).toBeTruthy();

    const aliceCheckbox = screen.getByRole("checkbox", { name: "Select profile Alice" });
    fireEvent.click(aliceCheckbox);

    await waitFor(() => {
      expect(screen.getByText("1 selected")).toBeTruthy();
    });
    expect(aliceCheckbox.closest("tr")?.getAttribute("data-state")).toBe("selected");

    fireEvent.click(screen.getByRole("checkbox", { name: "Select profile Bob" }));

    await waitFor(() => {
      expect(screen.getByText("2 selected")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/profiles/batch", {
        user_ids: ["user-1", "user-2"],
        action: "delete",
      });
    });
    expect(showToast).toHaveBeenCalledWith(EN_MAP["toast.batchDeleted"].replace("{0}", "2"));
  });

  it("accepts the legacy batch delete response and retains only failed profiles", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      total: 2,
      profiles: [
        { user_id: "user-1", display_name: "Alice" },
        { user_id: "user-2", display_name: "Bob" },
      ],
    }));
    bridge.apiPost.mockResolvedValue(ok({
      deleted_count: 1,
      failed_count: 1,
      total: 2,
      failed_ids: ["user-2"],
    }));

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile Alice" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select profile Bob" }));
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/profiles/batch", {
        user_ids: ["user-1", "user-2"],
        action: "delete",
      });
    });
    expect(showToast).toHaveBeenCalledWith("1 profile operation failed", true);
    expect(screen.getByText("Bob").closest("tr")?.getAttribute("data-state")).toBe("selected");
  });

  it("opens profile detail and deletes the profile from the side panel", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/profiles") {
        return Promise.resolve(ok({
          total: 1,
          profiles: [
            {
              user_id: "user-9",
              display_name: "$& $$ Gamma",
              tag_count: 2,
              top_interests: ["graphs"],
              last_seen: "2026-06-28T12:00:00Z",
            },
          ],
        }));
      }
      if (path === "page/profiles/detail") {
        return Promise.resolve(ok({
          profile: {
            user_id: params.user_id,
            display_name: "$& $$ Gamma",
            message_count: 12,
            tags: [
              { name: "graphs", confidence: 0.92 },
              { name: "testing", confidence: 0.61 },
            ],
          },
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("button", { name: "Open profile $& $$ Gamma" }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/profiles/detail", { user_id: "user-9" });
    });

    const drawer = await screen.findByRole("dialog", { name: "Profile: $& $$ Gamma" });

    expect(within(drawer).getByText("user-9")).toBeTruthy();
    expect(within(drawer).getByText("12")).toBeTruthy();
    expect(within(drawer).getByText("graphs")).toBeTruthy();
    expect(within(drawer).getByText("92%")).toBeTruthy();

    fireEvent.click(within(drawer).getByRole("button", { name: /delete profile/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/profiles/delete", {
        user_id: "user-9",
      });
    });
    expect(showToast).toHaveBeenCalledWith("Profile deleted");
  });

  it("creates a profile with the full structured draft, updates the current list total, and opens the returned entity in view mode", async () => {
    bridge.apiGet.mockResolvedValue(ok({ total: 0, profiles: [] }));
    bridge.apiPost.mockResolvedValue(ok({
      entity: {
        user_id: "alice",
        display_name: "Alice",
        preferences: {
          reply_style: "detailed",
          preferred_topics: ["ops"],
          avoided_topics: ["spoilers"],
          active_hours: [9, 17],
        },
        tags: [{ category: "interest", value: "testing", confidence: 0.8 }],
      },
      revision: "rev-new",
    }));

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("button", { name: /new profile/i }));
    const dialog = await screen.findByRole("dialog", { name: "New Profile" });
    fireEvent.change(within(dialog).getByLabelText("User ID"), { target: { value: "alice" } });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Alice" } });
    fireEvent.change(within(dialog).getByLabelText("Reply style"), { target: { value: "detailed" } });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Preferred topics" }), { target: { value: "ops" } });
    fireEvent.keyDown(within(dialog).getByRole("textbox", { name: "Preferred topics" }), { key: "Enter" });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Avoided topics" }), { target: { value: "spoilers" } });
    fireEvent.keyDown(within(dialog).getByRole("textbox", { name: "Avoided topics" }), { key: "Enter" });
    fireEvent.change(within(dialog).getByLabelText("Active hours start"), { target: { value: "9" } });
    fireEvent.change(within(dialog).getByLabelText("Active hours end"), { target: { value: "17" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add tag" }));
    fireEvent.change(within(dialog).getByLabelText("Tag category"), { target: { value: "interest" } });
    fireEvent.change(within(dialog).getByLabelText("Tag value"), { target: { value: "testing" } });
    fireEvent.change(within(dialog).getByLabelText("Tag confidence"), { target: { value: "0.8" } });

    fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/profiles/create", {
        user_id: "alice",
        display_name: "Alice",
        preferences: {
          reply_style: "detailed",
          preferred_topics: ["ops"],
          avoided_topics: ["spoilers"],
          active_hours: [9, 17],
        },
        tags: [{ category: "interest", value: "testing", confidence: 0.8 }],
      });
    });
    expect(await screen.findByText("Alice")).toBeTruthy();
    expect(screen.getAllByText("1", { selector: ".text-lg.font-bold.tabular-nums" })).toHaveLength(2);
    expect(await screen.findByRole("dialog", { name: "Profile: Alice" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^save$/i })).toBeNull();
  });

  it("retains every create draft field and reports a visible error after a create network failure", async () => {
    bridge.apiGet.mockResolvedValue(ok({ total: 0, profiles: [] }));
    bridge.apiPost.mockRejectedValue(new Error("offline"));

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("button", { name: /new profile/i }));
    const dialog = await screen.findByRole("dialog", { name: "New Profile" });
    fireEvent.change(within(dialog).getByLabelText("User ID"), { target: { value: "alice" } });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Alice" } });
    fireEvent.change(within(dialog).getByLabelText("Reply style"), { target: { value: "detailed" } });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Preferred topics" }), { target: { value: "ops" } });
    fireEvent.keyDown(within(dialog).getByRole("textbox", { name: "Preferred topics" }), { key: "Enter" });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Avoided topics" }), { target: { value: "spoilers" } });
    fireEvent.keyDown(within(dialog).getByRole("textbox", { name: "Avoided topics" }), { key: "Enter" });
    fireEvent.change(within(dialog).getByLabelText("Active hours start"), { target: { value: "9" } });
    fireEvent.change(within(dialog).getByLabelText("Active hours end"), { target: { value: "17" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add tag" }));
    fireEvent.change(within(dialog).getByLabelText("Tag category"), { target: { value: "interest" } });
    fireEvent.change(within(dialog).getByLabelText("Tag value"), { target: { value: "testing" } });
    fireEvent.change(within(dialog).getByLabelText("Tag confidence"), { target: { value: "0.8" } });

    fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));

    expect(await screen.findByText("offline")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "New Profile" })).toBeTruthy();
    expect(within(dialog).getByDisplayValue("alice")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("Alice")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("detailed")).toBeTruthy();
    expect(within(dialog).getByText("ops")).toBeTruthy();
    expect(within(dialog).getByText("spoilers")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("9")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("17")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("interest")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("testing")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("0.8")).toBeTruthy();

    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(await screen.findByRole("button", { name: "Discard changes and leave" })).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "New Profile" })).toBeTruthy();
  });

  it("saves a complete editable profile through the revisioned update envelope", async () => {
    bridge.apiGet.mockImplementation((path: string) => Promise.resolve(ok(path.endsWith("detail")
      ? {
        user_id: "alice",
        display_name: "Alice",
        revision: "rev-1",
        preferences: { reply_style: "casual", preferred_topics: [], avoided_topics: [], active_hours: [] },
        tags: [],
      }
      : { total: 1, profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }] })));
    bridge.apiPost.mockResolvedValue(ok({
      entity: {
        user_id: "alice",
        display_name: "Alicia",
        preferences: {
          reply_style: "detailed",
          preferred_topics: ["ops"],
          avoided_topics: ["spoilers"],
          active_hours: [9, 17],
        },
        tags: [{ category: "interest", value: "testing", confidence: 0.8 }],
      },
      revision: "rev-2",
    }));

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("button", { name: /open profile alice/i }));
    const drawer = await screen.findByRole("dialog", { name: "Profile: Alice" });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    expect(within(drawer).getByLabelText("User ID")).toHaveProperty("disabled", true);
    fireEvent.change(within(drawer).getByLabelText("Name"), { target: { value: "Alicia" } });
    fireEvent.change(within(drawer).getByLabelText("Reply style"), { target: { value: "detailed" } });
    fireEvent.change(within(drawer).getByRole("textbox", { name: "Preferred topics" }), { target: { value: "ops" } });
    fireEvent.keyDown(within(drawer).getByRole("textbox", { name: "Preferred topics" }), { key: "Enter" });
    fireEvent.change(within(drawer).getByRole("textbox", { name: "Avoided topics" }), { target: { value: "spoilers" } });
    fireEvent.keyDown(within(drawer).getByRole("textbox", { name: "Avoided topics" }), { key: "Enter" });
    fireEvent.change(within(drawer).getByLabelText("Active hours start"), { target: { value: "9" } });
    fireEvent.change(within(drawer).getByLabelText("Active hours end"), { target: { value: "17" } });
    fireEvent.click(within(drawer).getByRole("button", { name: "Add tag" }));
    fireEvent.change(within(drawer).getByLabelText("Tag category"), { target: { value: "interest" } });
    fireEvent.change(within(drawer).getByLabelText("Tag value"), { target: { value: "testing" } });
    fireEvent.change(within(drawer).getByLabelText("Tag confidence"), { target: { value: "0.8" } });

    fireEvent.click(within(drawer).getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/profiles/update", {
        identity: { user_id: "alice" },
        changes: {
          display_name: "Alicia",
          preferences: {
            reply_style: "detailed",
            preferred_topics: ["ops"],
            avoided_topics: ["spoilers"],
            active_hours: [9, 17],
          },
          tags: [{ category: "interest", value: "testing", confidence: 0.8 }],
        },
        expected_revision: "rev-1",
      });
    });
    expect(await screen.findByRole("button", { name: /^edit$/i })).toBeTruthy();
  });

  it("normalizes prefixed profile update errors into one linked summary", async () => {
    bridge.apiGet.mockImplementation((path: string) => Promise.resolve(ok(path.endsWith("detail")
      ? { user_id: "alice", display_name: "Alice", revision: "rev-1", preferences: { reply_style: "casual", preferred_topics: [], avoided_topics: [], active_hours: [] }, tags: [{ category: "interest", value: "music", confidence: 0.8 }] }
      : { total: 1, profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }] })));
    bridge.apiPost.mockRejectedValue(new ApiRequestError("Invalid profile", "validation_error", {
      "changes.display_name": "name rejected",
      "changes.tags.0.value": "tag value rejected",
      "changes.preferences.unknown": "unknown preference field",
      "changes.tags.999.value": "missing tag field",
    }));
    render(<ProfilesPage showToast={showToast} />);
    fireEvent.click(await screen.findByRole("button", { name: /open profile alice/i }));
    const drawer = await screen.findByRole("dialog", { name: "Profile: Alice" });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Name"), { target: { value: "Alicia" } });
    fireEvent.click(within(drawer).getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(within(drawer).getAllByRole("alert")).toHaveLength(1));
    const href = within(drawer).getByRole("link", { name: "name rejected" }).getAttribute("href")!;
    const errorId = href.slice(1);
    expect(within(drawer).getByLabelText("Name").getAttribute("aria-describedby")?.split(/\s+/)).toContain(errorId);
    expect(document.querySelectorAll(`[id="${errorId}"]`)).toHaveLength(1);
    const tagHref = within(drawer).getByRole("link", { name: "tag value rejected" }).getAttribute("href")!;
    const tagErrorId = tagHref.slice(1);
    expect(within(drawer).getByLabelText("Tag value").getAttribute("aria-describedby")?.split(/\s+/)).toContain(tagErrorId);
    expect(document.querySelectorAll(`[id="${tagErrorId}"]`)).toHaveLength(1);
    expect(within(drawer).getByText("unknown preference field; missing tag field")).toBeTruthy();
    expect(within(drawer).queryByRole("link", { name: "unknown preference field" })).toBeNull();
    expect(within(drawer).queryByRole("link", { name: "missing tag field" })).toBeNull();
  });

  it("retains the edit sheet, draft, and visible error after an update network failure", async () => {
    bridge.apiGet.mockImplementation((path: string) => Promise.resolve(ok(path.endsWith("detail")
      ? {
        user_id: "alice",
        display_name: "Alice",
        revision: "rev-1",
        preferences: { reply_style: "casual", preferred_topics: [], avoided_topics: [], active_hours: [] },
        tags: [],
      }
      : { total: 1, profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }] })));
    bridge.apiPost.mockRejectedValue(new Error("offline"));

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("button", { name: /open profile alice/i }));
    const drawer = await screen.findByRole("dialog", { name: "Profile: Alice" });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Name"), { target: { value: "Alicia" } });
    fireEvent.change(within(drawer).getByLabelText("Reply style"), { target: { value: "detailed" } });

    fireEvent.click(within(drawer).getByRole("button", { name: /^save$/i }));

    expect(await screen.findByText("offline")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Profile: Alice" })).toBeTruthy();
    expect(within(drawer).getByDisplayValue("Alicia")).toBeTruthy();
    expect(within(drawer).getByDisplayValue("detailed")).toBeTruthy();
  });

  it("reapplies a local edit after a structured stale conflict and retries against the latest revision", async () => {
    bridge.apiGet.mockImplementation((path: string) => Promise.resolve(ok(path.endsWith("detail")
      ? { user_id: "alice", display_name: "Alice", revision: "rev-1", preferences: { reply_style: "casual", preferred_topics: [], avoided_topics: [], active_hours: [] }, tags: [] }
      : { total: 1, profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }] })));
    bridge.apiPost
      .mockResolvedValueOnce({ status: "error", code: "edit_conflict", message: "stale", data: { current_entity: { user_id: "alice", display_name: "Remote", preferences: { reply_style: "formal", preferred_topics: [], avoided_topics: [], active_hours: [] }, tags: [] }, current_revision: "rev-2" } })
      .mockResolvedValueOnce(ok({ entity: { user_id: "alice", display_name: "Alicia", preferences: { reply_style: "formal", preferred_topics: [], avoided_topics: [], active_hours: [] }, tags: [] }, revision: "rev-3" }));
    render(<ProfilesPage showToast={showToast} />);
    fireEvent.click(await screen.findByRole("button", { name: /open profile alice/i }));
    fireEvent.click(await screen.findByRole("button", { name: /edit/i }));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Alicia" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    expect(await screen.findByRole("dialog", { name: /profile changed|conflict/i })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /reapply local values/i }));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenLastCalledWith("page/profiles/update", {
      identity: { user_id: "alice" }, changes: { display_name: "Alicia", preferences: { reply_style: "formal", preferred_topics: [], avoided_topics: [], active_hours: [] }, tags: [] }, expected_revision: "rev-2",
    }));
  });

  it("opens DeleteConfirmDialog and submits a revisioned single delete only after confirmation", async () => {
    bridge.apiGet.mockImplementation((path: string) => Promise.resolve(ok(path.endsWith("detail") ? { user_id: "alice", display_name: "Alice", revision: "rev-1", preferences: { reply_style: "casual", preferred_topics: [], avoided_topics: [], active_hours: [] }, tags: [] } : { total: 1, profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }] })));
    render(<ProfilesPage showToast={showToast} />); fireEvent.click(await screen.findByRole("button", { name: /open profile alice/i })); fireEvent.click(await screen.findByRole("button", { name: /^delete$/i }));
    expect(bridge.apiPost).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole("button", { name: /^delete profile$/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/profiles/delete", { identity: { user_id: "alice" }, expected_revision: "rev-1" }));
  });

  it("submits a safe batch delete, reports partial failure, and retains only failed selection", async () => {
    bridge.apiGet.mockResolvedValue(ok({ total: 2, profiles: [{ user_id: "a", display_name: "A", revision: "r1" }, { user_id: "b", display_name: "B", revision: "r2" }] }));
    bridge.apiPost.mockResolvedValue(ok({ total: 2, succeeded_count: 1, failed_count: 1, succeeded_ids: [{ user_id: "a" }], failures: [{ identity: { user_id: "b" }, code: "not_found", message: "missing" }] }));
    render(<ProfilesPage showToast={showToast} />); fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile A" })); fireEvent.click(screen.getByRole("checkbox", { name: "Select profile B" })); fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/profiles/batch", { action: "delete", items: [{ identity: { user_id: "a" }, expected_revision: "r1" }, { identity: { user_id: "b" }, expected_revision: "r2" }], params: {} }));
    expect(screen.getByText("1 selected")).toBeTruthy(); expect(showToast).toHaveBeenCalledWith(expect.stringContaining("1"), true);
  });

  it("prevents duplicate revisioned batch deletes while preserving selections added during the request", async () => {
    const first = { user_id: "alice", display_name: "Alice", revision: "rev-1" };
    const second = { user_id: "bob", display_name: "Bob", revision: "rev-2" };
    const request = deferred<ReturnType<typeof ok>>();
    bridge.apiGet.mockResolvedValue(ok({ total: 2, profiles: [first, second] }));
    bridge.apiPost.mockReturnValue(request.promise);

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile Alice" }));
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));

    const toolbar = screen.getByRole("toolbar");
    expect(within(toolbar).getByRole("button", { name: /^delete$/i })).toHaveProperty("disabled", true);
    fireEvent.click(within(toolbar).getByRole("button", { name: /^delete$/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select profile Bob" }));
    expect(bridge.apiPost).toHaveBeenCalledTimes(1);

    await act(async () => request.resolve(ok({ total: 1, succeeded_count: 1, failed_count: 0, succeeded_ids: [{ user_id: "alice" }], failures: [] })));

    await waitFor(() => expect(screen.getByText("1 selected")).toBeTruthy());
    expect(screen.getByRole("checkbox", { name: "Select profile Bob" }).getAttribute("aria-checked")).toBe("true");
    expect(within(screen.getByRole("toolbar")).getByRole("button", { name: /^delete$/i })).toHaveProperty("disabled", false);
  });

  it("clears selection when profile pagination changes", async () => {
    bridge.apiGet.mockImplementation((_path: string, params: Record<string, string>) => Promise.resolve(ok({ total: 101, profiles: [{ user_id: `user-${params.offset}`, display_name: "A" }] }))); render(<ProfilesPage showToast={showToast} />); fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile A" })); fireEvent.click(screen.getByRole("button", { name: "Next page" })); await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith("page/profiles", { limit: "100", offset: "100", sort_by: "last_seen_at", sort_order: "desc" })); expect(screen.queryByText("1 selected")).toBeNull();
  });

  it("submits both structured batch tag actions with only the supported tag fields", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      total: 1,
      profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }],
    }));
    bridge.apiPost.mockResolvedValue(ok({
      total: 1,
      succeeded_count: 1,
      failed_count: 0,
      succeeded_ids: [{ user_id: "alice" }],
      failures: [],
    }));

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile Alice" }));
    fireEvent.click(screen.getByRole("button", { name: /edit tags/i }));
    const addDialog = await screen.findByRole("dialog");
    expect(within(addDialog).getByLabelText("Tag category")).toBeTruthy();
    expect(within(addDialog).getByLabelText("Tag value")).toBeTruthy();
    expect(within(addDialog).getByLabelText("Tag confidence")).toBeTruthy();
    expect(within(addDialog).queryByLabelText("User ID")).toBeNull();
    expect(within(addDialog).queryByLabelText("Name")).toBeNull();
    expect(within(addDialog).queryByLabelText("Reply style")).toBeNull();
    fireEvent.change(within(addDialog).getByLabelText("Tag category"), { target: { value: "interest" } });
    fireEvent.change(within(addDialog).getByLabelText("Tag value"), { target: { value: "ops" } });
    fireEvent.change(within(addDialog).getByLabelText("Tag confidence"), { target: { value: "0.8" } });
    fireEvent.click(within(addDialog).getByRole("button", { name: /add tag/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/profiles/batch", {
        action: "tags_add",
        items: [{ identity: { user_id: "alice" }, expected_revision: "rev-1" }],
        params: { tag: { category: "interest", value: "ops", confidence: 0.8 } },
      });
    });

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile Alice" }));
    fireEvent.click(screen.getByRole("button", { name: /edit tags/i }));
    const removeDialog = await screen.findByRole("dialog");
    fireEvent.change(within(removeDialog).getByLabelText("Tag category"), { target: { value: "interest" } });
    fireEvent.change(within(removeDialog).getByLabelText("Tag value"), { target: { value: "ops" } });
    fireEvent.change(within(removeDialog).getByLabelText("Tag confidence"), { target: { value: "0.8" } });
    fireEvent.click(within(removeDialog).getByRole("button", { name: /remove tag/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenLastCalledWith("page/profiles/batch", {
        action: "tags_remove",
        items: [{ identity: { user_id: "alice" }, expected_revision: "rev-1" }],
        params: { tag: { category: "interest", value: "ops", confidence: 0.8 } },
      });
    });
  });

  it("does not make Edit Tags available for a selected profile without a revision", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      total: 1,
      profiles: [{ user_id: "alice", display_name: "Alice" }],
    }));

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile Alice" }));

    expect((screen.getByRole("button", { name: /edit tags/i }) as HTMLButtonElement).disabled).toBe(true);
    expect(bridge.apiPost).not.toHaveBeenCalled();
  });

  it("rejects a batch tag submission when the selected dialog snapshot is incomplete", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      total: 2,
      profiles: [
        { user_id: "alice", display_name: "Alice", revision: "rev-1" },
        { user_id: "bob", display_name: "Bob", revision: "rev-2" },
      ],
    }));

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile Alice" }));
    fireEvent.click(screen.getByRole("button", { name: /edit tags/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Tag value"), { target: { value: "ops" } });
    const checkboxes = document.querySelectorAll('[data-slot="checkbox"]');
    fireEvent.click(checkboxes[2]);
    fireEvent.click(within(dialog).getByRole("button", { name: /add tag/i }));

    expect(bridge.apiPost).not.toHaveBeenCalled();
    expect(await screen.findByText("Selected profiles changed; please review the selection"))
      .toBeTruthy();
    expect(screen.getByText("2 selected")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("ops")).toBeTruthy();
  });

  it("rejects a revisioned batch tag submission when the selected identity changes", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      total: 2,
      profiles: [
        { user_id: "alice", display_name: "Alice", revision: "rev-1" },
        { user_id: "bob", display_name: "Bob", revision: "rev-2" },
      ],
    }));

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile Alice" }));
    fireEvent.click(screen.getByRole("button", { name: /edit tags/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Tag value"), { target: { value: "ops" } });
    const checkboxes = document.querySelectorAll('[data-slot="checkbox"]');
    fireEvent.click(checkboxes[1]);
    fireEvent.click(checkboxes[2]);
    fireEvent.click(within(dialog).getByRole("button", { name: /add tag/i }));

    expect(bridge.apiPost).not.toHaveBeenCalled();
    expect(await screen.findByText("Selected profiles changed; please review the selection"))
      .toBeTruthy();
    expect(screen.getByText("1 selected")).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "Select profile Bob", hidden: true })
      .getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("ops")).toBeTruthy();
  });

  it("retains the batch tag dialog, draft, selection, and visible error after a network failure", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      total: 1,
      profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }],
    }));
    bridge.apiPost.mockRejectedValue(new Error("offline"));

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile Alice" }));
    fireEvent.click(screen.getByRole("button", { name: /edit tags/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Tag category"), { target: { value: "interest" } });
    fireEvent.change(within(dialog).getByLabelText("Tag value"), { target: { value: "ops" } });
    fireEvent.change(within(dialog).getByLabelText("Tag confidence"), { target: { value: "0.8" } });

    fireEvent.click(within(dialog).getByRole("button", { name: /add tag/i }));

    expect(await screen.findByText("offline")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("interest")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("ops")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("0.8")).toBeTruthy();
    expect(screen.getByText("1 selected")).toBeTruthy();
  });

  it("requires the exact confirmation phrase before deleting exactly the bulk threshold", async () => {
    const profiles = Array.from({ length: 20 }, (_, index) => ({
      user_id: `u${index}`,
      display_name: `U${index}`,
      revision: `r${index}`,
    }));
    bridge.apiGet.mockResolvedValue(ok({ total: 20, profiles }));
    bridge.apiPost.mockResolvedValue(ok({
      total: 20,
      succeeded_count: 20,
      failed_count: 0,
      succeeded_ids: profiles.map(({ user_id }) => ({ user_id })),
      failures: [],
    }));

    render(<ProfilesPage showToast={showToast} />);

    await screen.findByText("U0");
    for (const profile of profiles) {
      fireEvent.click(screen.getByRole("checkbox", { name: `Select profile ${profile.display_name}` }));
    }
    expect(screen.getByText("20 selected")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    const dialog = await screen.findByRole("dialog");
    const confirmation = within(dialog).getByRole("textbox");
    expect(confirmation).toBeTruthy();
    expect(within(dialog).getByText(/delete selected/i)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: /^delete$/i }));
    expect(bridge.apiPost).not.toHaveBeenCalled();
    fireEvent.change(confirmation, { target: { value: "19" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^delete$/i }));
    expect(bridge.apiPost).not.toHaveBeenCalled();
    fireEvent.change(confirmation, { target: { value: "20" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/profiles/batch", {
        action: "delete",
        items: profiles.map(({ user_id, revision }) => ({
          identity: { user_id },
          expected_revision: revision,
        })),
        params: {},
      });
    });
  });

  it("requires the exact confirmation phrase before deleting profiles from multiple defined groups below the bulk threshold", async () => {
    const profiles = [
      { user_id: "alice", display_name: "Alice", group_id: "group-a", revision: "rev-a" },
      { user_id: "bob", display_name: "Bob", group_id: "group-b", revision: "rev-b" },
    ];
    bridge.apiGet.mockResolvedValue(ok({ total: 2, profiles }));
    bridge.apiPost.mockResolvedValue(ok({ total: 2, succeeded_count: 2, failed_count: 0, succeeded_ids: profiles.map(({ user_id }) => ({ user_id })), failures: [] }));

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile Alice" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select profile Bob" }));
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    const dialog = await screen.findByRole("dialog");
    const confirmation = within(dialog).getByRole("textbox");
    expect(bridge.apiPost).not.toHaveBeenCalled();
    fireEvent.change(confirmation, { target: { value: "wrong" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^delete$/i }));
    expect(bridge.apiPost).not.toHaveBeenCalled();
    fireEvent.change(confirmation, { target: { value: "2" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^delete$/i }));

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/profiles/batch", {
      action: "delete",
      items: profiles.map(({ user_id, revision }) => ({ identity: { user_id }, expected_revision: revision })),
      params: {},
    }));
  });

  it("keeps a created profile outside the backend offset page closed and announces the localized outside-view toast", async () => {
    bridge.apiGet.mockImplementation((_path: string, params: Record<string, string>) => Promise.resolve(ok(
      params.offset === "100"
        ? { total: 101, profiles: [{ user_id: "tail", display_name: "Tail", revision: "rev-tail" }] }
        : { total: 101, profiles: [{ user_id: "first", display_name: "First", revision: "rev-first" }] },
    )));
    bridge.apiPost.mockResolvedValue(ok({
      entity: {
        user_id: "created-on-first-page",
        display_name: "Created elsewhere",
        preferences: { reply_style: "casual", preferred_topics: [], avoided_topics: [], active_hours: [] },
        tags: [],
      },
      revision: "rev-created",
    }));

    render(<ProfilesPage showToast={showToast} />);

    expect(await screen.findByText("First")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Tail")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /new profile/i }));
    const dialog = await screen.findByRole("dialog", { name: "New Profile" });
    fireEvent.change(within(dialog).getByLabelText("User ID"), { target: { value: "created-on-first-page" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/profiles/create", {
        user_id: "created-on-first-page",
        display_name: "",
        preferences: { reply_style: "casual", preferred_topics: [], avoided_topics: [], active_hours: [] },
        tags: [],
      });
    });
    expect(showToast).toHaveBeenCalledWith("Created profile is outside the current view");
    expect(screen.getByText("102", { selector: ".text-lg.font-bold.tabular-nums" })).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Profile: Created elsewhere" })).toBeNull();
  });

  it("prevents duplicate create requests and disables create, close, and cancel while creation is pending", async () => {
    const createRequest = deferred<ReturnType<typeof ok>>();
    bridge.apiGet.mockResolvedValue(ok({ total: 0, profiles: [] }));
    bridge.apiPost.mockReturnValue(createRequest.promise);

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("button", { name: /new profile/i }));
    const dialog = await screen.findByRole("dialog", { name: "New Profile" });
    fireEvent.change(within(dialog).getByLabelText("User ID"), { target: { value: "alice" } });
    const create = within(dialog).getByRole("button", { name: /^create$/i });
    fireEvent.click(create);
    fireEvent.click(create);

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    expect((create as HTMLButtonElement).disabled).toBe(true);
    expect((within(dialog).getByRole("button", { name: "Close" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(dialog).getByRole("button", { name: /^cancel$/i }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      createRequest.resolve(ok({
        entity: { user_id: "alice", display_name: "", preferences: { reply_style: "casual", preferred_topics: [], avoided_topics: [], active_hours: [] }, tags: [] },
        revision: "rev-created",
      }));
      await createRequest.promise;
    });
  });

  it("prevents duplicate edit requests and disables save, close, and cancel while an edit is pending", async () => {
    const updateRequest = deferred<ReturnType<typeof ok>>();
    bridge.apiGet.mockImplementation((path: string) => Promise.resolve(ok(path.endsWith("detail")
      ? { user_id: "alice", display_name: "Alice", revision: "rev-1", preferences: { reply_style: "casual", preferred_topics: [], avoided_topics: [], active_hours: [] }, tags: [] }
      : { total: 1, profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }] })));
    bridge.apiPost.mockReturnValue(updateRequest.promise);

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("button", { name: /open profile alice/i }));
    const drawer = await screen.findByRole("dialog", { name: "Profile: Alice" });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Name"), { target: { value: "Alicia" } });
    const save = within(drawer).getByRole("button", { name: /^save$/i });
    fireEvent.click(save);
    fireEvent.click(save);

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    expect((save as HTMLButtonElement).disabled).toBe(true);
    expect((within(drawer).getByRole("button", { name: "Close" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(drawer).getByRole("button", { name: /^cancel$/i }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      updateRequest.resolve(ok({
        entity: { user_id: "alice", display_name: "Alicia", preferences: { reply_style: "casual", preferred_topics: [], avoided_topics: [], active_hours: [] }, tags: [] },
        revision: "rev-2",
      }));
      await updateRequest.promise;
    });
  });

  it("prevents duplicate batch tag requests and disables the action, close, and cancel while they are pending", async () => {
    const batchRequest = deferred<ReturnType<typeof ok>>();
    bridge.apiGet.mockResolvedValue(ok({
      total: 1,
      profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }],
    }));
    bridge.apiPost.mockReturnValue(batchRequest.promise);

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile Alice" }));
    fireEvent.click(screen.getByRole("button", { name: /edit tags/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Tag category"), { target: { value: "interest" } });
    fireEvent.change(within(dialog).getByLabelText("Tag value"), { target: { value: "ops" } });
    const addTag = within(dialog).getByRole("button", { name: /add tag/i });
    fireEvent.click(addTag);
    fireEvent.click(addTag);

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    expect((addTag as HTMLButtonElement).disabled).toBe(true);
    expect((within(dialog).getByRole("button", { name: "Close" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(dialog).getByRole("button", { name: /^cancel$/i }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      batchRequest.resolve(ok({
        total: 1,
        succeeded_count: 1,
        failed_count: 0,
        succeeded_ids: [{ user_id: "alice" }],
        failures: [],
      }));
      await batchRequest.promise;
    });
  });

  it("notifies create draft ownership as dirty and then clean when the draft is discarded", async () => {
    const onDirtyChange = vi.fn();
    bridge.apiGet.mockResolvedValue(ok({ total: 0, profiles: [] }));

    render(<ProfilesPage showToast={showToast} onDirtyChange={onDirtyChange} />);

    fireEvent.click(await screen.findByRole("button", { name: /new profile/i }));
    const dialog = await screen.findByRole("dialog", { name: "New Profile" });
    fireEvent.change(within(dialog).getByLabelText("User ID"), { target: { value: "alice" } });
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    fireEvent.click(await screen.findByRole("button", { name: "Discard changes and leave" }));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
  });

  it("notifies edit draft ownership as dirty and then clean when editing is cancelled", async () => {
    const onDirtyChange = vi.fn();
    bridge.apiGet.mockImplementation((path: string) => Promise.resolve(ok(path.endsWith("detail")
      ? { user_id: "alice", display_name: "Alice", revision: "rev-1", preferences: { reply_style: "casual", preferred_topics: [], avoided_topics: [], active_hours: [] }, tags: [] }
      : { total: 1, profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }] })));

    render(<ProfilesPage showToast={showToast} onDirtyChange={onDirtyChange} />);

    fireEvent.click(await screen.findByRole("button", { name: /open profile alice/i }));
    const drawer = await screen.findByRole("dialog", { name: "Profile: Alice" });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Name"), { target: { value: "Alicia" } });
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    fireEvent.click(within(drawer).getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
  });

  it("keeps a dirty batch-tag draft when Cancel is chosen, then clears it only after explicit discard", async () => {
    const onDirtyChange = vi.fn();
    bridge.apiGet.mockResolvedValue(ok({
      total: 1,
      profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }],
    }));

    render(<ProfilesPage showToast={showToast} onDirtyChange={onDirtyChange} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile Alice" }));
    fireEvent.click(screen.getByRole("button", { name: /edit tags/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Tag value"), { target: { value: "ops" } });
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    fireEvent.click(within(dialog).getByRole("button", { name: /^cancel$/i }));
    expect(await screen.findByRole("button", { name: "Keep editing" })).toBeTruthy();
    expect(document.querySelector('input[value="ops"]')).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.getByDisplayValue("ops")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Discard changes and leave" }));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
    expect(screen.queryByDisplayValue("ops")).toBeNull();
  });

  it("keeps a dirty profile batch-tag dialog open until its draft is explicitly discarded", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      total: 1,
      profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }],
    }));

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile Alice" }));
    fireEvent.click(screen.getByRole("button", { name: /edit tags/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Tag value"), { target: { value: "ops" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));

    expect(await screen.findByRole("button", { name: "Discard changes and leave" })).toBeTruthy();
    expect(document.querySelector('input[value="ops"]')).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Discard changes and leave" }));
    await waitFor(() => expect(document.querySelector('input[value="ops"]')).toBeNull());
  });

  it("does not open the editor for a malformed current profile envelope", async () => {
    bridge.apiGet.mockImplementation((path: string) => Promise.resolve(ok(path.endsWith("detail") ? { entity: null, revision: "rev-1" } : {
      total: 1,
      profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }],
    })));

    render(<ProfilesPage showToast={showToast} />);
    fireEvent.click(await screen.findByRole("button", { name: /open profile alice/i }));

    await waitFor(() => expect(showToast).toHaveBeenCalledWith("Invalid profile detail response", true));
    expect(screen.queryByRole("dialog", { name: /profile: alice/i })).toBeNull();
  });

  it("retains the profile create draft when the entity envelope is malformed", async () => {
    bridge.apiGet.mockResolvedValue(ok({ total: 0, profiles: [] }));
    bridge.apiPost.mockResolvedValue(ok({ entity: null, revision: "rev-new" }));

    render(<ProfilesPage showToast={showToast} />);
    fireEvent.click(await screen.findByRole("button", { name: /new profile/i }));
    const dialog = await screen.findByRole("dialog", { name: "New Profile" });
    fireEvent.change(within(dialog).getByLabelText("User ID"), { target: { value: "alice" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));

    expect(await screen.findByText("Invalid profile entity response")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "New Profile" })).toBeTruthy();
    expect(within(dialog).getByDisplayValue("alice")).toBeTruthy();
  });

  it("retains the profile edit draft when the update envelope has an invalid revision", async () => {
    bridge.apiGet.mockImplementation((path: string) => Promise.resolve(ok(path.endsWith("detail") ? {
      user_id: "alice", display_name: "Alice", revision: "rev-1", preferences: {}, tags: [],
    } : { total: 1, profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }] })));
    bridge.apiPost.mockResolvedValue(ok({ entity: { user_id: "alice", display_name: "Alicia" }, revision: 7 }));

    render(<ProfilesPage showToast={showToast} />);
    fireEvent.click(await screen.findByRole("button", { name: /open profile alice/i }));
    const drawer = await screen.findByRole("dialog", { name: /profile: alice/i });
    fireEvent.click(within(drawer).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(drawer).getByLabelText("Name"), { target: { value: "Alicia" } });
    fireEvent.click(within(drawer).getByRole("button", { name: /^save$/i }));

    expect(await screen.findByText("Invalid profile entity response")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: /profile: alice/i })).toBeTruthy();
    expect(within(drawer).getByDisplayValue("Alicia")).toBeTruthy();
  });

  it("preserves the full profile selection when a batch result is malformed", async () => {
    bridge.apiGet.mockResolvedValue(ok({ total: 1, profiles: [{ user_id: "alice", display_name: "Alice", revision: "rev-1" }] }));
    bridge.apiPost.mockResolvedValue(ok({ total: 1, succeeded_ids: "alice", failures: [] }));

    render(<ProfilesPage showToast={showToast} />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select profile Alice" }));
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => expect(showToast).toHaveBeenCalledWith("Invalid profile batch response", true));
    expect(screen.getByText("1 selected")).toBeTruthy();
  });
});
