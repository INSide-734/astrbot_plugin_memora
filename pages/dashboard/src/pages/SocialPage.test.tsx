import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SocialPage } from "./SocialPage";
import { ApiRequestError } from "@/types/editing";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale: ReturnType<typeof vi.fn>;
  getI18n: ReturnType<typeof vi.fn>;
  t: ReturnType<typeof vi.fn>;
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

function relation(overrides: Record<string, unknown> = {}) {
  return {
    from_user: "alice",
    to_user: "bob",
    group_id: "group-1",
    relation_type: "colleague",
    strength: 0.5,
    frequency: 3,
    last_interaction: 1,
    tags: ["project"],
    category: "career",
    revision: "rev-1",
    ...overrides,
  };
}

function identity(item: Record<string, unknown>) {
  return {
    from_user: item.from_user,
    to_user: item.to_user,
    group_id: item.group_id,
    relation_type: item.relation_type,
  };
}

describe("SocialPage", () => {
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
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  function mockSocialList(
    relations: Record<string, unknown>[] = [relation()],
    groups = [
      { group_id: "group-1", message_count: 12 },
      { group_id: "group-2", message_count: 4 },
    ],
  ) {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string> = {}) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups }));
      if (path === "page/social/relations") {
        return Promise.resolve(ok({
          relations: relations.filter((item) => {
            const matchingGroup = !params.group_id || item.group_id === params.group_id;
            const matchingCategory = !params.category || item.category === params.category;
            return matchingGroup && matchingCategory;
          }),
        }));
      }
      return Promise.resolve(ok({}));
    });
  }

  async function openCreateDialog() {
    fireEvent.click(await screen.findByRole("button", { name: /new relation/i }));
    return screen.findByRole("dialog", { name: "New Relation" });
  }

  async function fillRelationDraft(container: HTMLElement, values: {
    from_user?: string;
    to_user?: string;
    group_id?: string;
    relation_type?: string;
    strength?: string;
    tags?: string[];
  } = {}) {
    const form = within(container);
    if (values.from_user !== undefined) fireEvent.change(form.getByLabelText("From user"), { target: { value: values.from_user } });
    if (values.to_user !== undefined) fireEvent.change(form.getByLabelText("To user"), { target: { value: values.to_user } });
    if (values.group_id !== undefined) fireEvent.change(form.getByLabelText("Group ID"), { target: { value: values.group_id } });
    if (values.relation_type !== undefined) {
      fireEvent.click(form.getByLabelText("Relation type"));
      const relationLabel = values.relation_type.split("_").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
      const option = await screen.findByRole("option", { name: new RegExp(relationLabel, "i") });
      fireEvent.pointerDown(option, { pointerType: "mouse" });
      fireEvent.click(option);
      await waitFor(() => expect(form.getByLabelText("Relation type").textContent).toMatch(new RegExp(`(?:${relationLabel}|${values.relation_type})`, "i")));
    }
    if (values.strength !== undefined) fireEvent.change(form.getByLabelText("Strength"), { target: { value: values.strength } });
    for (const tag of values.tags ?? []) {
      const tags = form.getByRole("textbox", { name: "Tags" });
      fireEvent.change(tags, { target: { value: tag } });
      fireEvent.keyDown(tags, { key: "Enter" });
    }
  }

  async function openRelationEditor() {
    fireEvent.click(await screen.findByRole("button", { name: /row actions alice.*bob/i }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /^view$/i }));
    return screen.findByRole("dialog", { name: /relation: alice.*bob/i });
  }

  function selectRelation(fromUser: string, toUser: string) {
    fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(`select relation ${fromUser}.*${toUser}`, "i") }));
  }

  it("shows the same selected group label as the menu option", async () => {
    mockSocialList();
    render(<SocialPage showToast={showToast} />);

    const trigger = screen.getByRole("combobox");
    await waitFor(() => expect(trigger.textContent).toContain("group-1 (12)"));
    fireEvent.click(trigger);
    expect((await screen.findByRole("option", { name: "group-1 (12)" })).textContent).toContain("group-1 (12)");
  });

  it("loads the first group, keeps category tabs, and displays returned relation details", async () => {
    mockSocialList();

    render(<SocialPage showToast={showToast} />);

    const page = screen.getByRole("region", { name: /social/i });
    expect(page.getAttribute("data-layout")).toBe("standard");
    expect(page.querySelector('[data-slot="page-header"]')).toBeTruthy();
    expect(screen.getByRole("tablist", { name: /categor/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /all/i }).getAttribute("aria-selected")).toBe("true");

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/social/relations", {
        group_id: "group-1",
        sort_by: "last_interaction",
        sort_order: "desc",
      });
    });

    expect(await screen.findByText("alice")).toBeTruthy();
    expect(screen.getByText("bob")).toBeTruthy();
    expect(screen.getByText("50%")).toBeTruthy();
    expect(screen.getByText("project")).toBeTruthy();
  });

  it("sorts frequency on the server without persisting sort", async () => {
    mockSocialList();
    render(<SocialPage showToast={showToast} />);

    await screen.findByText("alice");
    const preferenceKey = "memora.table.social-relations.v1";
    const storedBeforeSort = localStorage.getItem(preferenceKey);
    selectRelation("alice", "bob");
    fireEvent.click(screen.getByRole("button", { name: /Sort Frequency ascending/i }));
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith(
      "page/social/relations",
      {
        group_id: "group-1",
        sort_by: "frequency",
        sort_order: "asc",
      },
    ));
    expect(screen.queryByText("1 selected")).toBeNull();

    fireEvent.click((await screen.findByRole("button", { name: /Sort Frequency descending/i })));
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith(
      "page/social/relations",
      {
        group_id: "group-1",
        sort_by: "frequency",
        sort_order: "desc",
      },
    ));
    expect(localStorage.getItem(preferenceKey)).toBe(storedBeforeSort);
  });

  it("isolates row actions from row activation and deletes without opening detail", async () => {
    mockSocialList();
    render(<SocialPage showToast={showToast} />);

    const rowActions = await screen.findByRole("button", { name: /row actions alice.*bob/i });
    fireEvent.click(rowActions);
    expect(screen.queryByRole("dialog", { name: /relation: alice.*bob/i })).toBeNull();
    fireEvent.click(await screen.findByRole("menuitem", { name: /^delete$/i }));
    const confirmation = await screen.findByRole("dialog", { name: /delete relation/i });
    expect(screen.queryByRole("dialog", { name: /relation: alice.*bob/i })).toBeNull();
    fireEvent.click(within(confirmation).getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /delete relation/i })).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: /row actions alice.*bob/i }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /^view$/i }));
    expect(await screen.findByRole("dialog", { name: /relation: alice.*bob/i })).toBeTruthy();
  });

  it("refetches the current group with the selected category tab", async () => {
    mockSocialList([
      relation({ category: "career" }),
      relation({ from_user: "eve", to_user: "frank", category: "emotional", relation_type: "best_friend" }),
    ]);

    render(<SocialPage showToast={showToast} />);

    await screen.findByText("alice");
    fireEvent.click(screen.getByRole("tab", { name: /career/i }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/social/relations", {
        group_id: "group-1",
        category: "career",
        sort_by: "last_interaction",
        sort_order: "desc",
      });
    });
    expect(screen.getByRole("tab", { name: /career/i }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("alice")).toBeTruthy();
  });

  it("preserves loading, empty, and fetch-error states", async () => {
    const request = deferred<ReturnType<typeof ok>>();
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [{ group_id: "group-1" }] }));
      if (path === "page/social/relations") return request.promise;
      return Promise.resolve(ok({}));
    });

    render(<SocialPage showToast={showToast} />);

    expect(await screen.findByText(/loading/i)).toBeTruthy();
    await act(async () => {
      request.resolve(ok({ relations: [] }));
      await request.promise;
    });
    expect(await screen.findByText(/no relations found/i)).toBeTruthy();

    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [{ group_id: "group-1" }] }));
      if (path === "page/social/relations") return Promise.reject(new Error("relations unavailable"));
      return Promise.resolve(ok({}));
    });
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith("Error: relations unavailable", true);
    });
    expect(screen.getByText(/no relations found/i)).toBeTruthy();
  });

  it("creates a relation with the full SocialRelationDraft and opens an in-view result in view mode", async () => {
    mockSocialList([]);
    const created = relation({ strength: 0.8, tags: ["trusted"], revision: "rev-created" });
    bridge.apiPost.mockResolvedValue(ok({ entity: created, revision: "rev-created" }));

    render(<SocialPage showToast={showToast} />);

    const dialog = await openCreateDialog();
    await fillRelationDraft(dialog, {
      from_user: "alice",
      to_user: "bob",
      group_id: "group-1",
      relation_type: "colleague",
      strength: "0.8",
      tags: ["trusted"],
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/social/create", {
        from_user: "alice",
        to_user: "bob",
        group_id: "group-1",
        relation_type: "colleague",
        strength: 0.8,
        tags: ["trusted"],
      });
    });
    expect((await screen.findAllByText("trusted")).length).toBeGreaterThan(0);
    expect(await screen.findByRole("dialog", { name: /relation: alice.*bob/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^save$/i })).toBeNull();
  });

  it("keeps a nonmatching create result out of the current view and reports the localized toast", async () => {
    mockSocialList([]);
    bridge.apiPost.mockResolvedValue(ok({
      entity: relation({ group_id: "group-2", revision: "rev-created" }),
      revision: "rev-created",
    }));

    render(<SocialPage showToast={showToast} />);

    const dialog = await openCreateDialog();
    await fillRelationDraft(dialog, { from_user: "eve", to_user: "frank", group_id: "group-2", relation_type: "colleague", strength: "0.4" });
    fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith("Created relation is outside the current view");
    });
    expect(screen.queryByRole("dialog", { name: /relation: alice.*bob/i })).toBeNull();
  });

  it("opens relation details from an explicit row action and saves immutable identity with editable changes", async () => {
    mockSocialList();
    const updated = relation({ relation_type: "best_friend", strength: 0.8, tags: ["project", "trusted"], category: "emotional", revision: "rev-2" });
    bridge.apiPost.mockResolvedValue(ok({ entity: updated, revision: "rev-2" }));

    render(<SocialPage showToast={showToast} />);

    const sheet = await openRelationEditor();
    const footer = within(sheet).getByTestId("entity-editor-footer");
    const body = within(sheet).getByTestId("entity-editor-body");
    expect(within(footer).getByRole("button", { name: /^delete$/i })).toBeTruthy();
    expect(within(body).queryByRole("button", { name: /^delete$/i })).toBeNull();
    expect(within(sheet).queryByText(/unsaved/i)).toBeNull();
    fireEvent.click(within(sheet).getByRole("button", { name: /^edit$/i }));
    expect(screen.getByRole("dialog", { name: /relation: alice.*bob/i })).toBe(sheet);
    expect(within(sheet).getByLabelText("From user")).toHaveProperty("disabled", true);
    expect(within(sheet).getByLabelText("To user")).toHaveProperty("disabled", true);
    expect(within(sheet).getByLabelText("Group ID")).toHaveProperty("disabled", true);
    await fillRelationDraft(sheet, { relation_type: "best_friend", strength: "0.8", tags: ["trusted"] });
    expect(within(sheet).getByText(/unsaved/i)).toBeTruthy();
    fireEvent.click(within(sheet).getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/social/update", {
        identity: {
          from_user: "alice",
          to_user: "bob",
          group_id: "group-1",
          relation_type: "colleague",
        },
        changes: {
          relation_type: "best_friend",
          strength: 0.8,
          tags: ["project", "trusted"],
        },
        expected_revision: "rev-1",
      });
    });
    expect(await screen.findByRole("button", { name: /^edit$/i })).toBeTruthy();
    expect(screen.getAllByText("trusted").length).toBeGreaterThan(0);
  });

  it("normalizes update field errors into one linked validation summary", async () => {
    mockSocialList();
    bridge.apiPost.mockRejectedValue(new ApiRequestError("Invalid relation", "validation_error", {
      "changes.strength": "strength rejected",
      "changes.tags.0": "first tag rejected",
      "changes.unknown": "unknown relation field",
    }));
    render(<SocialPage showToast={showToast} />);
    const sheet = await openRelationEditor();
    fireEvent.click(within(sheet).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(sheet).getByLabelText("Strength"), { target: { value: "0.8" } });
    fireEvent.click(within(sheet).getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(within(sheet).getAllByRole("alert")).toHaveLength(1));
    const href = within(sheet).getByRole("link", { name: "strength rejected" }).getAttribute("href")!;
    const errorId = href.slice(1);
    expect(within(sheet).getByLabelText("Strength").getAttribute("aria-describedby")?.split(/\s+/)).toContain(errorId);
    expect(document.querySelectorAll(`[id="${errorId}"]`)).toHaveLength(1);
    const tagHref = within(sheet).getByRole("link", { name: "first tag rejected" }).getAttribute("href")!;
    const tagErrorId = tagHref.slice(1);
    expect(within(sheet).getByRole("textbox", { name: "Tags" }).getAttribute("aria-describedby")?.split(/\s+/)).toContain(tagErrorId);
    expect(document.querySelectorAll(`[id="${tagErrorId}"]`)).toHaveLength(1);
    expect(within(sheet).getByText("unknown relation field")).toBeTruthy();
    expect(within(sheet).queryByRole("link", { name: "unknown relation field" })).toBeNull();
  });

  it("reapplies a local relation edit after a structured stale conflict and retries the latest revision", async () => {
    mockSocialList();
    bridge.apiPost
      .mockResolvedValueOnce({
        status: "error",
        code: "edit_conflict",
        message: "stale",
        data: {
          current_entity: relation({ strength: 0.6, tags: ["remote"], revision: undefined }),
          current_revision: "rev-2",
        },
      })
      .mockResolvedValueOnce(ok({ entity: relation({ relation_type: "best_friend", strength: 0.8, tags: ["project", "trusted"], revision: "rev-3" }), revision: "rev-3" }));

    render(<SocialPage showToast={showToast} />);

    const sheet = await openRelationEditor();
    fireEvent.click(within(sheet).getByRole("button", { name: /^edit$/i }));
    await fillRelationDraft(sheet, { relation_type: "best_friend", strength: "0.8", tags: ["trusted"] });
    fireEvent.click(within(sheet).getByRole("button", { name: /^save$/i }));

    expect(await screen.findByRole("dialog", { name: /relation changed|conflict/i })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /reapply local values/i }));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenLastCalledWith("page/social/update", {
        identity: {
          from_user: "alice",
          to_user: "bob",
          group_id: "group-1",
          relation_type: "colleague",
        },
        changes: {
          relation_type: "best_friend",
          strength: 0.8,
          tags: ["project", "trusted"],
        },
        expected_revision: "rev-2",
      });
    });
  });

  it("opens DeleteConfirmDialog and sends no single-delete request before confirmation", async () => {
    mockSocialList();
    bridge.apiPost.mockResolvedValue(ok({ deleted: true, identity: identity(relation()) }));

    render(<SocialPage showToast={showToast} />);

    const sheet = await openRelationEditor();
    fireEvent.click(within(sheet).getByRole("button", { name: /^delete$/i }));
    expect(bridge.apiPost).not.toHaveBeenCalled();

    const confirm = await screen.findByRole("dialog", { name: /delete relation/i });
    fireEvent.click(within(confirm).getByRole("button", { name: /^delete relation$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/social/delete", {
        identity: {
          from_user: "alice",
          to_user: "bob",
          group_id: "group-1",
          relation_type: "colleague",
        },
        expected_revision: "rev-1",
      });
    });
  });

  it("submits revisioned batch delete, reports a partial failure, and retains only failed selection", async () => {
    const first = relation({ from_user: "alice", to_user: "bob", revision: "rev-1" });
    const second = relation({ from_user: "carol", to_user: "dave", revision: "rev-2" });
    mockSocialList([first, second]);
    bridge.apiPost.mockResolvedValue(ok({
      total: 2,
      succeeded_ids: [identity(first)],
      failures: [{ identity: identity(second), code: "not_found", message: "missing" }],
    }));

    render(<SocialPage showToast={showToast} />);

    await screen.findByText("alice");
    selectRelation("alice", "bob");
    selectRelation("carol", "dave");
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/social/batch", {
        action: "delete",
        items: [
          { identity: identity(first), expected_revision: "rev-1" },
          { identity: identity(second), expected_revision: "rev-2" },
        ],
        params: {},
      });
    });
    expect(screen.getByText("1 selected")).toBeTruthy();
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining("1"), true);
  });

  it("prevents duplicate revisioned batch deletes while preserving selections added during the request", async () => {
    const first = relation({ from_user: "alice", to_user: "bob", revision: "rev-1" });
    const second = relation({ from_user: "carol", to_user: "dave", revision: "rev-2" });
    const request = deferred<ReturnType<typeof ok>>();
    mockSocialList([first, second]);
    bridge.apiPost.mockReturnValue(request.promise);

    render(<SocialPage showToast={showToast} />);

    await screen.findByText("alice");
    selectRelation("alice", "bob");
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));

    const toolbars = screen.getAllByRole("toolbar");
    const toolbar = toolbars[toolbars.length - 1];
    expect(within(toolbar).getByRole("button", { name: /^delete$/i })).toHaveProperty("disabled", true);
    fireEvent.click(within(toolbar).getByRole("button", { name: /^delete$/i }));
    selectRelation("carol", "dave");
    expect(bridge.apiPost).toHaveBeenCalledTimes(1);

    await act(async () => request.resolve(ok({ total: 1, succeeded_count: 1, failed_count: 0, succeeded_ids: [identity(first)], failures: [] })));

    await waitFor(() => expect(screen.getByText("1 selected")).toBeTruthy());
    expect(screen.getByRole("checkbox", { name: "Select relation carol dave" }).getAttribute("aria-checked")).toBe("true");
    const toolbarsAfter = screen.getAllByRole("toolbar");
    expect(within(toolbarsAfter[toolbarsAfter.length - 1]).getByRole("button", { name: /^delete$/i })).toHaveProperty("disabled", false);
  });

  it("submits only operation and tag controls through both supported batch-tag envelopes", async () => {
    mockSocialList();
    bridge.apiPost.mockResolvedValue(ok({ total: 1, succeeded_ids: [identity(relation())], failures: [] }));

    render(<SocialPage showToast={showToast} />);

    await screen.findByText("alice");
    selectRelation("alice", "bob");
    fireEvent.click(screen.getByRole("button", { name: /edit tags/i }));
    const addDialog = await screen.findByRole("dialog", { name: /edit relation tags/i });
    expect(within(addDialog).getByLabelText("Operation")).toBeTruthy();
    expect(within(addDialog).getByRole("textbox", { name: "Tags" })).toBeTruthy();
    expect(within(addDialog).queryByLabelText("From user")).toBeNull();
    expect(within(addDialog).queryByLabelText("Strength")).toBeNull();
    fireEvent.change(within(addDialog).getByLabelText("Operation"), { target: { value: "add_tags" } });
    const addTags = within(addDialog).getByRole("textbox", { name: "Tags" });
    fireEvent.change(addTags, { target: { value: "trusted" } });
    fireEvent.keyDown(addTags, { key: "Enter" });
    fireEvent.click(within(addDialog).getByRole("button", { name: /apply/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/social/batch", {
        action: "add_tags",
        items: [{ identity: identity(relation()), expected_revision: "rev-1" }],
        params: { tags: ["trusted"] },
      });
    });

    selectRelation("alice", "bob");
    fireEvent.click(screen.getByRole("button", { name: /edit tags/i }));
    const removeDialog = await screen.findByRole("dialog", { name: /edit relation tags/i });
    fireEvent.change(within(removeDialog).getByLabelText("Operation"), { target: { value: "remove_tags" } });
    const removeTags = within(removeDialog).getByRole("textbox", { name: "Tags" });
    fireEvent.change(removeTags, { target: { value: "trusted" } });
    fireEvent.keyDown(removeTags, { key: "Enter" });
    fireEvent.click(within(removeDialog).getByRole("button", { name: /apply/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenLastCalledWith("page/social/batch", {
        action: "remove_tags",
        items: [{ identity: identity(relation()), expected_revision: "rev-1" }],
        params: { tags: ["trusted"] },
      });
    });
  });

  it("retains the full create draft and visible error after a create network failure", async () => {
    mockSocialList([]);
    bridge.apiPost.mockRejectedValue(new Error("offline"));

    render(<SocialPage showToast={showToast} />);

    const dialog = await openCreateDialog();
    await fillRelationDraft(dialog, { from_user: "alice", to_user: "bob", group_id: "group-1", relation_type: "colleague", strength: "0.8", tags: ["trusted"] });
    fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));

    expect(await screen.findByText("offline")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "New Relation" })).toBeTruthy();
    expect(within(dialog).getByDisplayValue("alice")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("bob")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("group-1")).toBeTruthy();
    expect(within(dialog).getByLabelText("Relation type").textContent).toMatch(/colleague/i);
    expect(within(dialog).getByDisplayValue("0.8")).toBeTruthy();
    expect(within(dialog).getByText("trusted")).toBeTruthy();
  });

  it("retains the full edit sheet draft and visible error after an update network failure", async () => {
    mockSocialList();
    bridge.apiPost.mockRejectedValue(new Error("offline"));

    render(<SocialPage showToast={showToast} />);

    const sheet = await openRelationEditor();
    fireEvent.click(within(sheet).getByRole("button", { name: /^edit$/i }));
    await fillRelationDraft(sheet, { relation_type: "best_friend", strength: "0.8", tags: ["trusted"] });
    fireEvent.click(within(sheet).getByRole("button", { name: /^save$/i }));

    expect(await screen.findByText("offline")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: /relation: alice.*bob/i })).toBeTruthy();
    expect(within(sheet).getByDisplayValue("alice")).toBeTruthy();
    expect(within(sheet).getByDisplayValue("bob")).toBeTruthy();
    expect(within(sheet).getByDisplayValue("group-1")).toBeTruthy();
    expect(within(sheet).getByLabelText("Relation type").textContent).toMatch(/best.friend/i);
    expect(within(sheet).getByDisplayValue("0.8")).toBeTruthy();
    expect(within(sheet).getByText("trusted")).toBeTruthy();
  });

  it("retains the batch-tag dialog, tag draft, selection, and visible error after a network failure", async () => {
    mockSocialList();
    bridge.apiPost.mockRejectedValue(new Error("offline"));

    render(<SocialPage showToast={showToast} />);

    await screen.findByText("alice");
    selectRelation("alice", "bob");
    fireEvent.click(screen.getByRole("button", { name: /edit tags/i }));
    const dialog = await screen.findByRole("dialog", { name: /edit relation tags/i });
    fireEvent.change(within(dialog).getByLabelText("Operation"), { target: { value: "add_tags" } });
    const tags = within(dialog).getByRole("textbox", { name: "Tags" });
    fireEvent.change(tags, { target: { value: "trusted" } });
    fireEvent.keyDown(tags, { key: "Enter" });
    fireEvent.click(within(dialog).getByRole("button", { name: /apply/i }));

    expect(await screen.findByText("offline")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: /edit relation tags/i })).toBeTruthy();
    expect(within(dialog).getByDisplayValue("add_tags")).toBeTruthy();
    expect(within(dialog).getByText("trusted")).toBeTruthy();
    expect(screen.getByText("1 selected")).toBeTruthy();
  });

  it("clears selected relations when the group changes", async () => {
    mockSocialList([
      relation({ group_id: "group-1" }),
      relation({ from_user: "carol", to_user: "dave", group_id: "group-2", revision: "rev-2" }),
    ]);

    render(<SocialPage showToast={showToast} />);

    await screen.findByText("alice");
    selectRelation("alice", "bob");
    expect(screen.getByText("1 selected")).toBeTruthy();
    fireEvent.click(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByRole("option", { name: /group-2/i }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/social/relations", {
        group_id: "group-2",
        sort_by: "last_interaction",
        sort_order: "desc",
      });
    });
    expect(screen.queryByText("1 selected")).toBeNull();
  });

  it("clears selected relations when the category changes", async () => {
    mockSocialList();

    render(<SocialPage showToast={showToast} />);

    await screen.findByText("alice");
    selectRelation("alice", "bob");
    expect(screen.getByText("1 selected")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: /career/i }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/social/relations", {
        group_id: "group-1",
        category: "career",
        sort_by: "last_interaction",
        sort_order: "desc",
      });
    });
    expect(screen.queryByText("1 selected")).toBeNull();
  });

  it("reports create draft dirty ownership and clears it after discard", async () => {
    const onDirtyChange = vi.fn();
    mockSocialList([]);

    render(<SocialPage showToast={showToast} onDirtyChange={onDirtyChange} />);

    const dialog = await openCreateDialog();
    await fillRelationDraft(dialog, { from_user: "alice" });
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    fireEvent.click(await screen.findByRole("button", { name: "Discard changes and leave" }));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
  });

  it("reports edit draft dirty ownership and clears it after cancellation", async () => {
    const onDirtyChange = vi.fn();
    mockSocialList();

    render(<SocialPage showToast={showToast} onDirtyChange={onDirtyChange} />);

    const sheet = await openRelationEditor();
    fireEvent.click(within(sheet).getByRole("button", { name: /^edit$/i }));
    await fillRelationDraft(sheet, { strength: "0.8" });
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    fireEvent.click(within(sheet).getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
  });

  it("keeps a dirty batch-tag draft when Cancel is chosen, then clears it only after explicit discard", async () => {
    const onDirtyChange = vi.fn();
    mockSocialList();

    render(<SocialPage showToast={showToast} onDirtyChange={onDirtyChange} />);

    await screen.findByText("alice");
    selectRelation("alice", "bob");
    fireEvent.click(screen.getByRole("button", { name: /edit tags/i }));
    const dialog = await screen.findByRole("dialog", { name: /edit relation tags/i });
    const tags = within(dialog).getByRole("textbox", { name: "Tags" });
    fireEvent.change(tags, { target: { value: "trusted" } });
    fireEvent.keyDown(tags, { key: "Enter" });
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    fireEvent.click(within(dialog).getByRole("button", { name: /^cancel$/i }));
    expect(await screen.findByRole("button", { name: "Keep editing" })).toBeTruthy();
    expect(screen.getByText("trusted", { selector: "span" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.getByText("trusted", { selector: "span" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    fireEvent.click(await screen.findByRole("button", { name: "Discard changes and leave" }));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
    expect(screen.queryByText("trusted", { selector: "span" })).toBeNull();
  });

  it("prevents duplicate create requests and disables create, close, and cancel while create is pending", async () => {
    const request = deferred<ReturnType<typeof ok>>();
    mockSocialList([]);
    bridge.apiPost.mockReturnValue(request.promise);

    render(<SocialPage showToast={showToast} />);

    const dialog = await openCreateDialog();
    await fillRelationDraft(dialog, { from_user: "alice", to_user: "bob", group_id: "group-1", relation_type: "colleague", strength: "0.8" });
    const create = within(dialog).getByRole("button", { name: /^create$/i });
    fireEvent.click(create);
    fireEvent.click(create);

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    expect((create as HTMLButtonElement).disabled).toBe(true);
    expect((within(dialog).getByRole("button", { name: "Close" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(dialog).getByRole("button", { name: /^cancel$/i }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      request.resolve(ok({ entity: relation({ revision: "rev-2" }), revision: "rev-2" }));
      await request.promise;
    });
  });

  it("prevents duplicate update requests and disables save, close, and cancel while edit is pending", async () => {
    const request = deferred<ReturnType<typeof ok>>();
    mockSocialList();
    bridge.apiPost.mockReturnValue(request.promise);

    render(<SocialPage showToast={showToast} />);

    const sheet = await openRelationEditor();
    fireEvent.click(within(sheet).getByRole("button", { name: /^edit$/i }));
    await fillRelationDraft(sheet, { strength: "0.8" });
    const save = within(sheet).getByRole("button", { name: /^save$/i });
    fireEvent.click(save);
    fireEvent.click(save);

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    expect((save as HTMLButtonElement).disabled).toBe(true);
    expect((within(sheet).getByRole("button", { name: "Close" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(sheet).getByRole("button", { name: /^cancel$/i }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      request.resolve(ok({ entity: relation({ strength: 0.8, revision: "rev-2" }), revision: "rev-2" }));
      await request.promise;
    });
  });

  it("prevents duplicate batch-tag requests and disables apply, close, and cancel while pending", async () => {
    const request = deferred<ReturnType<typeof ok>>();
    mockSocialList();
    bridge.apiPost.mockReturnValue(request.promise);

    render(<SocialPage showToast={showToast} />);

    await screen.findByText("alice");
    selectRelation("alice", "bob");
    fireEvent.click(screen.getByRole("button", { name: /edit tags/i }));
    const dialog = await screen.findByRole("dialog", { name: /edit relation tags/i });
    const tags = within(dialog).getByRole("textbox", { name: "Tags" });
    fireEvent.change(tags, { target: { value: "trusted" } });
    fireEvent.keyDown(tags, { key: "Enter" });
    const apply = within(dialog).getByRole("button", { name: /apply/i });
    fireEvent.click(apply);
    fireEvent.click(apply);

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    expect((apply as HTMLButtonElement).disabled).toBe(true);
    expect((within(dialog).getByRole("button", { name: "Close" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(dialog).getByRole("button", { name: /^cancel$/i }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      request.resolve(ok({ total: 1, succeeded_ids: [identity(relation())], failures: [] }));
      await request.promise;
    });
  });

  it("retains the social create draft when the entity envelope is malformed", async () => {
    mockSocialList([]);
    bridge.apiPost.mockResolvedValue(ok({ entity: null, revision: "rev-new" }));

    render(<SocialPage showToast={showToast} />);
    const dialog = await openCreateDialog();
    await fillRelationDraft(dialog, { from_user: "alice", to_user: "bob", group_id: "group-1", relation_type: "colleague" });
    fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));

    expect(await screen.findByText("Invalid social relation entity response")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "New Relation" })).toBeTruthy();
    expect(within(dialog).getByDisplayValue("alice")).toBeTruthy();
  });

  it("retains the social edit draft when the update envelope is malformed", async () => {
    mockSocialList();
    bridge.apiPost.mockResolvedValue(ok({ entity: relation({ strength: 0.8 }), revision: "" }));

    render(<SocialPage showToast={showToast} />);
    const sheet = await openRelationEditor();
    fireEvent.click(within(sheet).getByRole("button", { name: /^edit$/i }));
    await fillRelationDraft(sheet, { strength: "0.8" });
    fireEvent.click(within(sheet).getByRole("button", { name: /^save$/i }));

    expect(await screen.findByText("Invalid social relation entity response")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: /relation: alice.*bob/i })).toBeTruthy();
    expect(within(sheet).getByDisplayValue("0.8")).toBeTruthy();
  });

  it("preserves the social selection and batch draft when a batch result is malformed", async () => {
    mockSocialList();
    bridge.apiPost.mockResolvedValue(ok({ total: 1, succeeded_ids: [], failures: [{ identity: null, code: "bad", message: "bad" }] }));

    render(<SocialPage showToast={showToast} />);
    await screen.findByText("alice");
    selectRelation("alice", "bob");
    fireEvent.click(screen.getByRole("button", { name: /edit tags/i }));
    const dialog = await screen.findByRole("dialog", { name: /edit relation tags/i });
    const tags = within(dialog).getByRole("textbox", { name: "Tags" });
    fireEvent.change(tags, { target: { value: "trusted" } });
    fireEvent.keyDown(tags, { key: "Enter" });
    fireEvent.click(within(dialog).getByRole("button", { name: /apply/i }));

    expect(await screen.findByText(/Invalid social batch response/)).toBeTruthy();
    expect(screen.getByRole("dialog", { name: /edit relation tags/i })).toBeTruthy();
    expect(screen.getByText("1 selected")).toBeTruthy();
    expect(within(dialog).getByText("trusted")).toBeTruthy();
  });
});
