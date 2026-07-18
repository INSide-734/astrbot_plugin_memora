import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ComponentType } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AffectionPage } from "./AffectionPage";
import { ApiRequestError } from "@/types/editing";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale: ReturnType<typeof vi.fn>;
  getI18n: ReturnType<typeof vi.fn>;
  t: ReturnType<typeof vi.fn>;
}

type FutureAffectionPageProps = { showToast: (message: string, isError?: boolean) => void; onDirtyChange?: (dirty: boolean) => void };
const FutureAffectionPage = AffectionPage as unknown as ComponentType<FutureAffectionPageProps>;

function ok<T>(data: T) { return { status: "ok", data }; }
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => { resolve = resolvePromise; reject = rejectPromise; });
  return { promise, resolve, reject };
}

const user = (overrides: Record<string, unknown> = {}) => ({
  user_id: "alice", group_id: "group-1", affection_score: 42, affection_level: "FRIENDLY",
  level_name: "Friendly", interaction_count: 8, last_interaction: 1, revision: "rev-1", ...overrides,
});
const status = (overrides: Record<string, unknown> = {}) => ({
  group_id: "group-1", total_affection: 48, max_total_affection: 100, user_count: 2,
  current_mood: { mood_type: "happy", intensity: 0.5, duration_hours: 12.5, start_time: 100, description: "Upbeat", is_active: true },
  top_users: [user()], ...overrides,
});

const AFFECTION_SENTINELS: Record<string, string> = {
  "affection.newUser": "新建好感用户哨兵",
  "affection.createUserDescription": "创建好感用户说明哨兵",
  "detail.create": "创建好感动作哨兵",
  "detail.edit": "编辑好感动作哨兵",
  "common.save": "保存好感动作哨兵",
  "common.delete": "删除好感动作哨兵",
  "common.close": "关闭好感动作哨兵",
  "common.cancel": "取消好感动作哨兵",
  "affection.deleteUser": "删除好感用户标题哨兵",
  "affection.userId": "用户字段哨兵",
  "affection.groupId": "群组字段哨兵",
  "affection.score": "好感分数字段哨兵",
  "affection.scoreRange": "好感分数范围哨兵",
  "affection.scoreInteger": "好感分数整数哨兵",
  "affection.conflictTitle": "好感冲突哨兵",
  "affection.conflictDescription": "远端好感已变更哨兵",
  "config.conflict.loadRemote": "加载远端好感哨兵",
  "affection.reapplyLocal": "重用本地好感哨兵",
  "config.unsaved.title": "未保存好感哨兵",
  "config.unsaved.description": "丢弃好感草稿哨兵",
  "config.unsaved.keepEditing": "继续编辑好感哨兵",
  "config.unsaved.discard": "放弃好感草稿哨兵",
  "affection.moodTitle": "情绪编辑哨兵",
  "affection.setMoodDescription": "设置情绪说明哨兵",
  "affection.setMood": "设置情绪动作哨兵",
  "affection.moodType": "情绪类型字段哨兵",
  "affection.moodIntensity": "情绪强度字段哨兵",
  "affection.moodDuration": "情绪时长字段哨兵",
  "affection.moodDescription": "情绪描述字段哨兵",
  "affection.intensityRange": "情绪强度范围哨兵",
  "affection.durationRange": "情绪时长范围哨兵",
  "affection.restoreDefaultMood": "恢复默认情绪哨兵",
  "affection.restoreDefaultMoodDescription": "恢复默认情绪说明哨兵",
  "affection.restoreDefaultMoodAction": "确认恢复默认情绪哨兵",
};

describe("AffectionPage", () => {
  let bridge: BridgeMock;
  let showToast: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    bridge = {
      apiGet: vi.fn(), apiPost: vi.fn(), getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}), t: vi.fn((key: string) => key),
    };
    showToast = vi.fn();
    Object.defineProperty(window, "AstrBotPluginPage", { configurable: true, value: bridge });
  });
  afterEach(() => {
    cleanup(); vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", { configurable: true, value: undefined });
  });

  function mockInitialData(groups = [{ group_id: "group-1", message_count: 12 }], statusOverrides: Record<string, unknown> = {}) {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups }));
      if (path === "page/affection/status") return Promise.resolve(ok(status(statusOverrides)));
      if (path === "page/affection/users") return Promise.resolve(ok({ group_id: params.group_id, users: [user()], total: 1, limit: 50, offset: Number(params.offset) }));
      if (path === "page/affection/moods/history") return Promise.resolve(ok({ history: [] }));
      return Promise.resolve(ok({}));
    });
  }
  async function renderLoaded(props: Partial<FutureAffectionPageProps> = {}, statusOverrides: Record<string, unknown> = {}) {
    mockInitialData(undefined, statusOverrides);
    render(<FutureAffectionPage showToast={showToast} {...props} />);
    await screen.findByText("alice");
  }
  function editor(name: RegExp | string) { return screen.getByRole("dialog", { name }); }
  function openRowAction(userId: string, action: "View" | "Edit" | "Delete") {
    fireEvent.click(screen.getByRole("button", { name: new RegExp(`row actions ${userId}`, "i") }));
    fireEvent.click(screen.getByRole("menuitem", { name: new RegExp(`^${action}$`, "i") }));
  }

  it("loads status and the full users page with exact group pagination parameters", async () => {
    mockInitialData();
    render(<AffectionPage showToast={showToast} />);
    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/affection/status", { group_id: "group-1" });
      expect(bridge.apiGet).toHaveBeenCalledWith("page/affection/users", {
        group_id: "group-1",
        limit: "50",
        offset: "0",
        sort_by: "affection_score",
        sort_order: "desc",
      });
    });
    expect(await screen.findByText("Upbeat")).toBeTruthy();
    expect(screen.getByText("alice")).toBeTruthy();
  });

  it("shows the same selected group label as the menu option", async () => {
    mockInitialData();
    render(<AffectionPage showToast={showToast} />);

    const trigger = screen.getByRole("combobox");
    await waitFor(() => expect(trigger.textContent).toContain("group-1 (12)"));
    fireEvent.click(trigger);
    expect((await screen.findByRole("option", { name: "group-1 (12)" })).textContent).toContain("group-1 (12)");
  });

  it("keeps the business-ranked leaderboard as an unsortable base Table", async () => {
    mockInitialData();
    render(<AffectionPage showToast={showToast} />);
    const page = screen.getByRole("region", { name: /Affection|好感/ });
    expect(page.getAttribute("data-layout")).toBe("standard");
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith("page/affection/status", { group_id: "group-1" }));
    expect(await screen.findByText("Upbeat")).toBeTruthy();
    expect(screen.getByText("50%")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: /intensity/i })).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: /alice.*score/i })).toBeTruthy();
    expect(screen.getByText("Friendly")).toBeTruthy();
    const leaderboard = screen.getByRole("region", { name: /leaderboard/i });
    expect(within(leaderboard).queryByRole("button", { name: /Sort/ })).toBeNull();
    expect(showToast).not.toHaveBeenCalled();
  });

  it("sorts all users and mood history independently on the server", async () => {
    mockInitialData();
    render(<AffectionPage showToast={showToast} />);

    const allUsers = await screen.findByRole("region", { name: /all.*users/i });
    fireEvent.click(within(allUsers).getByRole("button", { name: /Sort User ID ascending/ }));
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith(
      "page/affection/users",
      {
        group_id: "group-1",
        limit: "50",
        offset: "0",
        sort_by: "user_id",
        sort_order: "asc",
      },
    ));

    const updatedHistory = await screen.findByRole("region", { name: /mood history/i });
    fireEvent.click(within(updatedHistory).getByRole("button", { name: /Sort Intensity ascending/ }));
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith(
      "page/affection/moods/history",
      {
        group_id: "group-1",
        limit: "50",
        sort_by: "intensity",
        sort_order: "asc",
      },
    ));
  });

  it("keeps the existing empty-state and status failure behavior", async () => {
    bridge.apiGet.mockImplementation((path: string) => path === "page/groups"
      ? Promise.resolve(ok({ groups: [{ group_id: "group-1" }] }))
      : Promise.reject(new Error("affection unavailable")));
    render(<AffectionPage showToast={showToast} />);
    await waitFor(() => expect(showToast).toHaveBeenCalledWith("Error: affection unavailable", true));
    expect(screen.getByText("No affection data")).toBeTruthy();
  });

  it("refreshes affection status from the selected group", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [{ group_id: "group-1", message_count: 12 }, { group_id: "group-2", message_count: 4 }] }));
      if (path === "page/affection/status" && params.group_id === "group-2") return Promise.resolve(ok(status({ group_id: "group-2", current_mood: { mood_type: "CURIOUS", intensity: 0.4, description: "Curious about a new topic.", is_active: true }, top_users: [] })));
      if (path === "page/affection/status") return Promise.resolve(ok(status({ current_mood: { mood_type: "CALM", intensity: 0.1, description: "Calm baseline.", is_active: true } })));
      return Promise.resolve(ok({ users: [], total: 0, limit: 50, offset: 0 }));
    });
    render(<AffectionPage showToast={showToast} />);
    expect(await screen.findByText("Calm baseline.")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("combobox")[0]);
    fireEvent.click(await screen.findByRole("option", { name: /group-2/i }));
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith("page/affection/status", { group_id: "group-2" }));
    expect(await screen.findByText("Curious about a new topic.")).toBeTruthy();
  });

  it("pages users with returned total, limit, and offset and clears selection", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [{ group_id: "group-1" }] }));
      if (path === "page/affection/status") return Promise.resolve(ok(status()));
      if (path === "page/affection/users") return Promise.resolve(ok({ total: 101, limit: 50, offset: Number(params.offset), users: [user({ user_id: `user-${params.offset}` })] }));
      return Promise.resolve(ok({ history: [] }));
    });
    render(<AffectionPage showToast={showToast} />);
    expect(await screen.findByText("user-0")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /select user-0/i }));
    fireEvent.click(screen.getByRole("button", { name: /next page/i }));
    expect(screen.queryByText(/selected/)).toBeNull();
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith("page/affection/users", { group_id: "group-1", limit: "50", offset: "50", sort_by: "affection_score", sort_order: "desc" }));
    expect(await screen.findByText("user-50")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /previous page/i }));
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith("page/affection/users", { group_id: "group-1", limit: "50", offset: "0", sort_by: "affection_score", sort_order: "desc" }));
  });

  it("keeps top users summary-only and puts edit actions only in the full users table", async () => {
    await renderLoaded();
    expect(within(screen.getByRole("heading", { name: /leaderboard/i }).closest("section") ?? document.body).queryByRole("button", { name: /edit/i })).toBeNull();
    expect(screen.getByRole("button", { name: /row actions alice/i })).toBeTruthy();
  });

  it("creates an affection user with the exact body and opens the authoritative result in view mode", async () => {
    const onDirtyChange = vi.fn();
    await renderLoaded({ onDirtyChange });
    bridge.apiPost.mockResolvedValue(ok({ entity: user({ affection_score: 42, affection_level: "VIP", level_name: "Very close", revision: "rev-2" }), revision: "rev-2" }));
    fireEvent.click(screen.getByRole("button", { name: /new affection user|add user/i }));
    const dialog = editor(/new affection/i);
    fireEvent.change(within(dialog).getByLabelText("User ID"), { target: { value: "alice" } });
    fireEvent.change(within(dialog).getByLabelText("Group ID"), { target: { value: "group-1" } });
    fireEvent.change(within(dialog).getByLabelText(/affection score/i), { target: { value: "42" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/affection/users/create", { group_id: "group-1", user_id: "alice", affection_score: 42 }));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
    expect(await screen.findByText("Very close")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^save$/i })).toBeNull();
    expect(screen.getByText("VIP")).toBeTruthy();
  });

  it("updates only affection score, keeping identity immutable and applying the authoritative level", async () => {
    await renderLoaded();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => path === "page/affection/users"
      ? Promise.resolve(ok({ users: [user()], total: 1, limit: 50, offset: Number(params.offset) }))
      : path === "page/affection/status" ? Promise.resolve(ok(status())) : Promise.resolve(ok({ groups: [{ group_id: "group-1" }] })));
    bridge.apiPost.mockResolvedValue(ok({ entity: user({ affection_score: 77, affection_level: "VIP", level_name: "VIP", revision: "rev-2" }), revision: "rev-2" }));
    openRowAction("alice", "View");
    const sheet = editor(/affection.*alice/i);
    const footer = within(sheet).getByTestId("entity-editor-footer");
    const body = within(sheet).getByTestId("entity-editor-body");
    expect(within(footer).getByRole("button", { name: /delete alice/i })).toBeTruthy();
    expect(within(body).queryByRole("button", { name: /delete alice/i })).toBeNull();
    expect(within(sheet).queryByText(/unsaved/i)).toBeNull();
    fireEvent.click(within(sheet).getByRole("button", { name: /^edit$/i }));
    expect(screen.getByRole("dialog", { name: /affection.*alice/i })).toBe(sheet);
    expect(within(sheet).getByLabelText("User ID")).toHaveProperty("disabled", true);
    expect(within(sheet).getByLabelText("Group ID")).toHaveProperty("disabled", true);
    fireEvent.change(within(sheet).getByLabelText(/affection score/i), { target: { value: "77" } });
    expect(within(sheet).getByText(/unsaved/i)).toBeTruthy();
    fireEvent.click(within(sheet).getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/affection/users/update", { identity: { user_id: "alice", group_id: "group-1" }, changes: { affection_score: 77 }, expected_revision: "rev-1" }));
    expect(await screen.findByText("VIP")).toBeTruthy();
  });

  it.each([["create", "New affection"], ["update", "Affection: alice"]])("retains the %s draft and readable error after network failure", async (operation, expectedName) => {
    await renderLoaded();
    bridge.apiPost.mockRejectedValue(new Error(`${operation} offline`));
    if (operation === "create") {
      fireEvent.click(screen.getByRole("button", { name: /new affection user|add user/i }));
      const dialog = editor(/new affection/i);
      fireEvent.change(within(dialog).getByLabelText("User ID"), { target: { value: "alice" } });
      fireEvent.change(within(dialog).getByLabelText(/affection score/i), { target: { value: "42" } });
      fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));
      expect(await screen.findByText(`${operation} offline`)).toBeTruthy();
      expect(within(dialog).getByDisplayValue("alice")).toBeTruthy();
    } else {
      openRowAction("alice", "View");
      const sheet = editor(/affection.*alice/i);
      fireEvent.click(within(sheet).getByRole("button", { name: /^edit$/i }));
      fireEvent.change(within(sheet).getByLabelText(/affection score/i), { target: { value: "77" } });
      fireEvent.click(within(sheet).getByRole("button", { name: /^save$/i }));
      expect(await screen.findByText(`${operation} offline`)).toBeTruthy();
      expect(within(sheet).getByDisplayValue("77")).toBeTruthy();
    }
    expect(expectedName).toBeTruthy();
  });

  it("propagates prefixed affection update errors to one linked summary", async () => {
    await renderLoaded();
    bridge.apiPost.mockRejectedValue(new ApiRequestError("Invalid affection", "validation_error", { "changes.affection_score": "score rejected" }));
    openRowAction("alice", "View");
    const sheet = editor(/affection.*alice/i);
    fireEvent.click(within(sheet).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(sheet).getByLabelText(/affection score/i), { target: { value: "77" } });
    fireEvent.click(within(sheet).getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(within(sheet).getAllByRole("alert")).toHaveLength(1));
    const href = within(sheet).getByRole("link", { name: "score rejected" }).getAttribute("href")!;
    const errorId = href.slice(1);
    expect(within(sheet).getByLabelText(/affection score/i).getAttribute("aria-describedby")?.split(/\s+/)).toContain(errorId);
    expect(document.querySelectorAll(`[id="${errorId}"]`)).toHaveLength(1);
  });

  it("offers load-latest and reapply-local choices for edit conflicts", async () => {
    await renderLoaded();
    bridge.apiPost.mockResolvedValue({ status: "error", code: "edit_conflict", message: "Concurrent update", data: { current_entity: user({ affection_score: 55 }), current_revision: "rev-remote" } });
    openRowAction("alice", "View");
    const sheet = editor(/affection.*alice/i);
    fireEvent.click(within(sheet).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(sheet).getByLabelText(/affection score/i), { target: { value: "77" } });
    fireEvent.click(within(sheet).getByRole("button", { name: /^save$/i }));
    const conflict = await screen.findByRole("dialog", { name: /conflict/i });
    expect(within(conflict).getByRole("button", { name: /load latest/i })).toBeTruthy();
    expect(within(conflict).getByRole("button", { name: /reapply local/i })).toBeTruthy();
    fireEvent.click(within(conflict).getByRole("button", { name: /reapply local/i }));
    expect(within(sheet).getByDisplayValue("77")).toBeTruthy();
    bridge.apiPost.mockResolvedValue(ok({ entity: user({ affection_score: 77 }), revision: "rev-3" }));
    fireEvent.click(within(sheet).getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenLastCalledWith("page/affection/users/update", expect.objectContaining({ expected_revision: "rev-remote" })));
  });

  it("requires explicit delete confirmation and sends exact identity and revision", async () => {
    await renderLoaded(); bridge.apiPost.mockResolvedValue(ok({}));
    openRowAction("alice", "Delete");
    const confirm = await screen.findByRole("dialog", { name: /delete/i });
    expect(within(confirm).getByRole("button", { name: /confirm|delete/i })).toHaveProperty("disabled", true);
    fireEvent.click(within(confirm).getByRole("button", { name: /confirm|delete/i }));
    expect(bridge.apiPost).not.toHaveBeenCalled();
    fireEvent.change(within(confirm).getByRole("textbox"), { target: { value: "alice" } });
    fireEvent.click(within(confirm).getByRole("button", { name: /confirm|delete/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/affection/users/delete", { identity: { user_id: "alice", group_id: "group-1" }, expected_revision: "rev-1" }));
  });

  it("accepts backend identity field order for affection single delete", async () => {
    await renderLoaded();
    bridge.apiPost.mockResolvedValue(ok({ deleted: true, identity: { group_id: "group-1", user_id: "alice" } }));
    openRowAction("alice", "Delete");
    const confirm = await screen.findByRole("dialog", { name: /delete affection user/i });
    fireEvent.change(within(confirm).getByRole("textbox"), { target: { value: "alice" } });
    fireEvent.click(within(confirm).getByRole("button", { name: /delete/i }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /delete affection user/i })).toBeNull());
    expect(screen.queryByText("alice")).toBeNull();
  });

  it("keeps affection single-delete context after malformed success", async () => {
    await renderLoaded();
    bridge.apiPost.mockResolvedValue(ok({}));
    openRowAction("alice", "Delete");
    const confirm = await screen.findByRole("dialog", { name: /delete affection user/i });
    fireEvent.change(within(confirm).getByRole("textbox"), { target: { value: "alice" } });
    fireEvent.click(within(confirm).getByRole("button", { name: /delete/i }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(expect.stringMatching(/invalid|delete/i), true));
    expect(screen.getByRole("dialog", { name: /delete affection user/i })).toBeTruthy();
    expect(screen.getAllByText("alice").length).toBeGreaterThan(0);
  });

  it("batch-deletes a snapshot once, retaining only failed selections", async () => {
    const bob = user({ user_id: "bob", revision: "rev-b" });
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [{ group_id: "group-1", message_count: 12 }] }));
      if (path === "page/affection/status") return Promise.resolve(ok(status()));
      if (path === "page/affection/users") return Promise.resolve(ok({ group_id: params.group_id, users: [user(), bob], total: 2, limit: 50, offset: Number(params.offset) }));
      if (path === "page/affection/moods/history") return Promise.resolve(ok({ history: [] }));
      return Promise.resolve(ok({}));
    });
    render(<AffectionPage showToast={showToast} />);
    await screen.findByText("alice");
    bridge.apiPost.mockResolvedValue(ok({ total: 2, succeeded_count: 1, failed_count: 1, succeeded_ids: [{ user_id: "alice", group_id: "group-1" }], failures: [{ identity: { user_id: "bob", group_id: "group-1" }, code: "edit_conflict", message: "changed", current_revision: "rev-b" }] }));
    fireEvent.click(screen.getByRole("checkbox", { name: /select alice/i }));
    fireEvent.click(document.querySelector('[aria-label="Select bob"]') as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));
    const confirm = await screen.findByRole("dialog", { name: /delete/i });
    fireEvent.click(within(confirm).getByRole("button", { name: /confirm|delete/i }));
    fireEvent.click(within(confirm).getByRole("button", { name: /confirm|delete/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    expect(bridge.apiPost).toHaveBeenCalledWith("page/affection/users/batch", { action: "delete", items: [{ identity: { user_id: "alice", group_id: "group-1" }, expected_revision: "rev-1" }, { identity: { user_id: "bob", group_id: "group-1" }, expected_revision: "rev-b" }] });
    expect(screen.getByText("1 selected")).toBeTruthy();
  });

  it("freezes the affection delete snapshot before confirmation", async () => {
    const alice = user({ revision: "rev-a" });
    const bob = user({ user_id: "bob", revision: "rev-b" });
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [{ group_id: "group-1" }] }));
      if (path === "page/affection/status") return Promise.resolve(ok(status()));
      if (path === "page/affection/users") return Promise.resolve(ok({ users: [alice, bob], total: 2, limit: 50, offset: Number(params.offset) }));
      return Promise.resolve(ok({ history: [] }));
    });
    render(<AffectionPage showToast={showToast} />);
    await screen.findByText("bob");
    bridge.apiPost.mockResolvedValue(ok({ failures: [] }));
    fireEvent.click(screen.getByRole("checkbox", { name: /select alice/i }));
    fireEvent.click(document.querySelector('[aria-label="Select bob"]') as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));
    const confirm = await screen.findByRole("dialog", { name: /delete selected users/i });

    alice.revision = "rev-mutated";
    fireEvent.click(document.querySelector('[aria-label="Select bob"]') as HTMLElement);
    fireEvent.click(within(confirm).getByRole("button", { name: /delete selected/i }));

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/affection/users/batch", {
      action: "delete",
      items: [
        { identity: { user_id: "alice", group_id: "group-1" }, expected_revision: "rev-a" },
        { identity: { user_id: "bob", group_id: "group-1" }, expected_revision: "rev-b" },
      ],
    }));
  });

  it("shows a toast and keeps the single-delete confirmation open after failure", async () => {
    await renderLoaded();
    bridge.apiPost.mockRejectedValue(new Error("single delete offline"));
    openRowAction("alice", "Delete");
    const confirm = await screen.findByRole("dialog", { name: /delete affection user/i });
    fireEvent.change(within(confirm).getByRole("textbox"), { target: { value: "alice" } });
    fireEvent.click(within(confirm).getByRole("button", { name: /delete/i }));

    await waitFor(() => expect(showToast).toHaveBeenCalledWith("single delete offline", true));
    expect(screen.getByRole("dialog", { name: /delete affection user/i })).toBeTruthy();
    expect(within(confirm).getByText("alice")).toBeTruthy();
  });

  it("shows a toast and keeps the batch-delete confirmation and selection after failure", async () => {
    const bob = user({ user_id: "bob", revision: "rev-b" });
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [{ group_id: "group-1" }] }));
      if (path === "page/affection/status") return Promise.resolve(ok(status()));
      if (path === "page/affection/users") return Promise.resolve(ok({ users: [user(), bob], total: 2, limit: 50, offset: Number(params.offset) }));
      return Promise.resolve(ok({ history: [] }));
    });
    render(<AffectionPage showToast={showToast} />);
    await screen.findByText("bob");
    bridge.apiPost.mockRejectedValue(new Error("batch delete offline"));
    fireEvent.click(screen.getByRole("checkbox", { name: /select alice/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /select bob/i }));
    fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));
    const confirm = await screen.findByRole("dialog", { name: /delete selected users/i });
    fireEvent.click(within(confirm).getByRole("button", { name: /delete selected/i }));

    await waitFor(() => expect(showToast).toHaveBeenCalledWith("batch delete offline", true));
    expect(screen.getByRole("dialog", { name: /delete selected users/i })).toBeTruthy();
    expect(within(confirm).getByText("2 selected")).toBeTruthy();
  });

  it("keeps affection batch-delete context and selection after malformed success", async () => {
    const bob = user({ user_id: "bob", revision: "rev-b" });
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [{ group_id: "group-1" }] }));
      if (path === "page/affection/status") return Promise.resolve(ok(status()));
      if (path === "page/affection/users") return Promise.resolve(ok({ users: [user(), bob], total: 2, limit: 50, offset: Number(params.offset) }));
      return Promise.resolve(ok({ history: [] }));
    });
    render(<AffectionPage showToast={showToast} />);
    await screen.findByText("bob");
    bridge.apiPost.mockResolvedValue(ok({ total: 2, failures: [] }));
    fireEvent.click(screen.getByRole("checkbox", { name: /select alice/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /select bob/i }));
    fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));
    const confirm = await screen.findByRole("dialog", { name: /delete selected users/i });
    fireEvent.click(within(confirm).getByRole("button", { name: /delete selected/i }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(expect.stringMatching(/invalid|batch/i), true));
    expect(screen.getByRole("dialog", { name: /delete selected users/i })).toBeTruthy();
    expect(within(confirm).getByText("2 selected")).toBeTruthy();
    expect(screen.getAllByText("alice").length).toBeGreaterThan(0);
    expect(screen.getAllByText("bob").length).toBeGreaterThan(0);
  });

  it("rejects whitespace-only batch failure messages", async () => {
    const bob = user({ user_id: "bob", revision: "rev-b" });
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") return Promise.resolve(ok({ groups: [{ group_id: "group-1" }] }));
      if (path === "page/affection/status") return Promise.resolve(ok(status()));
      if (path === "page/affection/users") return Promise.resolve(ok({ users: [user(), bob], total: 2, limit: 50, offset: Number(params.offset) }));
      return Promise.resolve(ok({ history: [] }));
    });
    render(<AffectionPage showToast={showToast} />);
    await screen.findByText("bob");
    bridge.apiPost.mockResolvedValue(ok({ total: 2, succeeded_count: 1, failed_count: 1, succeeded_ids: [{ user_id: "alice", group_id: "group-1" }], failures: [{ identity: { user_id: "bob", group_id: "group-1" }, code: "edit_conflict", message: "   " }] }));
    fireEvent.click(screen.getByRole("checkbox", { name: /select alice/i }));
    fireEvent.click(screen.getByRole("checkbox", { name: /select bob/i }));
    fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));
    const confirm = await screen.findByRole("dialog", { name: /delete selected users/i });
    fireEvent.click(within(confirm).getByRole("button", { name: /delete selected/i }));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(expect.stringMatching(/invalid|batch/i), true));
    expect(screen.getByRole("dialog", { name: /delete selected users/i })).toBeTruthy();
    expect(within(confirm).getByText("2 selected")).toBeTruthy();
  });

  it("clears selection on group change and pagination change", async () => {
    mockInitialData([{ group_id: "group-1", message_count: 12 }, { group_id: "group-2", message_count: 4 }]);
    render(<AffectionPage showToast={showToast} />);
    await screen.findByText("alice");
    fireEvent.click(screen.getByRole("checkbox", { name: /select alice/i }));
    expect(screen.getByText("1 selected")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("combobox")[0]);
    const groupOption = await screen.findByRole("option", { name: /group-2/i });
    fireEvent.click(groupOption);
    await waitFor(() => expect(screen.queryByText("1 selected")).toBeNull());
  });

  it("uses the mood select popup for keyboard selection", async () => {
    await renderLoaded({}, { current_mood: { ...status().current_mood, mood_type: "calm" } });
    bridge.apiPost.mockResolvedValue(ok({ mood_type: "happy", intensity: 0.5, duration_hours: 12.5, description: "Upbeat" }));
    fireEvent.click(screen.getByRole("button", { name: /edit mood|set mood/i }));
    const dialog = editor(/mood/i);

    expect(within(dialog).queryByRole("option")).toBeNull();
    const moodType = within(dialog).getByRole("combobox", { name: /mood type/i });
    moodType.focus();
    fireEvent.keyDown(moodType, { key: "ArrowDown", code: "ArrowDown" });

    const listbox = await screen.findByRole("listbox");
    const happyOption = within(listbox).getByRole("option", { name: /happy/i });
    await waitFor(() => expect(document.activeElement?.getAttribute("role")).toBe("option"));
    fireEvent.keyDown(document.activeElement as Element, { key: "Home", code: "Home" });
    await waitFor(() => expect(document.activeElement).toBe(happyOption));
    fireEvent.keyDown(happyOption, { key: "Enter", code: "Enter" });

    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    expect(moodType.textContent).toMatch(/happy/i);
    fireEvent.click(within(dialog).getByRole("button", { name: /set|save/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/affection/mood/set", {
      group_id: "group-1", mood_type: "happy", intensity: 0.5, duration_hours: 12.5, description: "Upbeat",
    }));
  });

  it("sets lowercase mood with exact body and enforces both range boundaries", async () => {
    await renderLoaded(); bridge.apiPost.mockResolvedValue(ok({ mood_type: "happy", intensity: 0.5, duration_hours: 4, description: "..." }));
    fireEvent.click(screen.getByRole("button", { name: /edit mood|set mood/i }));
    const dialog = editor(/mood/i);
    expect((within(dialog).getByLabelText(/duration/i) as HTMLInputElement).value).toBe("12.5");
    fireEvent.click(within(dialog).getByRole("combobox", { name: /mood type/i }));
    fireEvent.click(within(await screen.findByRole("listbox")).getByRole("option", { name: /happy/i }));
    const intensity = within(dialog).getByLabelText("Intensity");
    fireEvent.change(intensity, { target: { value: "0" } });
    expect(document.getElementById(intensity.getAttribute("aria-describedby") ?? "")?.textContent).toMatch(/between 0.1 and 1/i);
    fireEvent.change(within(dialog).getByLabelText("Intensity"), { target: { value: "0.5" } });
    const duration = within(dialog).getByLabelText(/duration/i);
    fireEvent.change(duration, { target: { value: "169" } });
    expect(document.getElementById(duration.getAttribute("aria-describedby") ?? "")?.textContent).toMatch(/between 0.25 and 168/i);
    fireEvent.change(within(dialog).getByLabelText(/duration/i), { target: { value: "4" } });
    fireEvent.change(within(dialog).getByLabelText("Description"), { target: { value: "..." } });
    fireEvent.click(within(dialog).getByRole("button", { name: /set|save/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/affection/mood/set", { group_id: "group-1", mood_type: "happy", intensity: 0.5, duration_hours: 4, description: "..." }));
  });

  it("retains the mood dialog after a malformed set success envelope", async () => {
    await renderLoaded();
    bridge.apiPost.mockResolvedValue(ok({ mood_type: "happy", intensity: 2, duration_hours: 4, description: "bad", start_time: 1, is_active: true }));
    fireEvent.click(screen.getByRole("button", { name: /edit mood|set mood/i }));
    const dialog = editor(/mood/i);
    fireEvent.click(within(dialog).getByRole("combobox", { name: /mood type/i }));
    fireEvent.click(within(await screen.findByRole("listbox")).getByRole("option", { name: /happy/i }));
    fireEvent.change(within(dialog).getByLabelText("Intensity"), { target: { value: "0.5" } });
    fireEvent.change(within(dialog).getByLabelText(/duration/i), { target: { value: "4" } });
    fireEvent.change(within(dialog).getByLabelText("Description"), { target: { value: "valid" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /set|save/i }));

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/affection/mood/set", expect.anything()));
    expect((await within(dialog).findByRole("alert")).textContent).toMatch(/invalid mood|invalid.*response/i);
    expect(screen.getByRole("dialog", { name: /mood/i })).toBeTruthy();
  });

  it("retains the reset confirmation after a malformed mood success envelope", async () => {
    await renderLoaded();
    bridge.apiPost.mockResolvedValue(ok({ mood_type: "happy", intensity: 0.5, duration_hours: 0, description: "bad", start_time: 1, is_active: "yes" }));
    fireEvent.click(screen.getByRole("button", { name: /restore default mood/i }));
    const dialog = editor(/restore default mood/i);
    fireEvent.click(within(dialog).getByRole("button", { name: /confirm|restore/i }));

    expect((await within(dialog).findByRole("alert")).textContent).toMatch(/invalid mood|invalid.*response/i);
    expect(screen.getByRole("dialog", { name: /restore default mood/i })).toBeTruthy();
  });

  it("retains every mood draft field and readable error after a set failure", async () => {
    await renderLoaded(); bridge.apiPost.mockRejectedValue(new Error("mood offline"));
    fireEvent.click(screen.getByRole("button", { name: /edit mood|set mood/i }));
    const dialog = editor(/mood/i);
    fireEvent.click(within(dialog).getByRole("combobox", { name: /mood type/i }));
    fireEvent.click(within(await screen.findByRole("listbox")).getByRole("option", { name: /happy/i }));
    fireEvent.change(within(dialog).getByLabelText("Intensity"), { target: { value: "0.5" } });
    fireEvent.change(within(dialog).getByLabelText(/duration/i), { target: { value: "4" } });
    fireEvent.change(within(dialog).getByLabelText("Description"), { target: { value: "keep me" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /set|save/i }));
    expect(await screen.findByText("mood offline")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("happy")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("0.5")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("4")).toBeTruthy();
    expect(within(dialog).getByDisplayValue("keep me")).toBeTruthy();
  });

  it("propagates mood field errors to one linked summary", async () => {
    await renderLoaded();
    bridge.apiPost.mockRejectedValue(new ApiRequestError("Invalid mood", "validation_error", { intensity: "intensity rejected" }));
    fireEvent.click(screen.getByRole("button", { name: /edit mood|set mood/i }));
    const dialog = editor(/mood/i);
    fireEvent.click(within(dialog).getByRole("combobox", { name: /mood type/i }));
    fireEvent.click(within(await screen.findByRole("listbox")).getByRole("option", { name: /happy/i }));
    fireEvent.change(within(dialog).getByLabelText("Intensity"), { target: { value: "0.6" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /set|save/i }));
    await waitFor(() => expect(within(dialog).getAllByRole("alert")).toHaveLength(1));
    const href = within(dialog).getByRole("link", { name: "intensity rejected" }).getAttribute("href")!;
    const errorId = href.slice(1);
    expect(within(dialog).getByLabelText("Intensity").getAttribute("aria-describedby")?.split(/\s+/)).toContain(errorId);
    expect(document.querySelectorAll(`[id="${errorId}"]`)).toHaveLength(1);
  });

  it("resets only with explicit confirmation, sends only group_id, and guards duplicate pending reset", async () => {
    await renderLoaded(); const request = deferred<ReturnType<typeof ok>>(); bridge.apiPost.mockReturnValue(request.promise);
    fireEvent.click(screen.getByRole("button", { name: /restore default mood|恢复默认情绪/i }));
    const confirm = await screen.findByRole("dialog", { name: /restore default mood|恢复默认情绪/i });
    expect(within(confirm).getByRole("heading", { name: /restore default mood|恢复默认情绪/i })).toBeTruthy();
    expect(within(confirm).queryByText(/delete|clear history/i)).toBeNull();
    const restore = within(confirm).getByRole("button", { name: /confirm|restore/i });
    fireEvent.click(restore);
    fireEvent.click(restore);
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    expect(bridge.apiPost).toHaveBeenCalledWith("page/affection/mood/reset", { group_id: "group-1" });
    await act(async () => { request.resolve(ok({ mood_type: "calm", intensity: 0.1, duration_hours: 4, description: "default" })); await request.promise; });
  });

  it("loads read-only mood history rows and exposes no history edit or delete action", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => path === "page/groups" ? Promise.resolve(ok({ groups: [{ group_id: "group-1" }] }))
      : path === "page/affection/status" ? Promise.resolve(ok(status()))
      : Promise.resolve(ok({ history: [{ start_time: 100, duration_hours: 4, mood_type: "happy", intensity: 0.5, description: "..." }] })));
    render(<AffectionPage showToast={showToast} />);
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith("page/affection/moods/history", { group_id: "group-1", limit: "50", sort_by: "start_time", sort_order: "desc" }));
    expect(await screen.findByText("...")).toBeTruthy();
    expect(screen.getByText("100")).toBeTruthy(); expect(screen.getByText("4")).toBeTruthy(); expect(screen.getByText("happy")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /edit.*history|delete.*history/i })).toBeNull();
  });

  it("reports the OR of independent user and mood dirty owners", async () => {
    const onDirtyChange = vi.fn(); await renderLoaded({ onDirtyChange });
    fireEvent.click(screen.getByRole("button", { name: /new affection user|add user/i }));
    const userDialog = editor(/new affection/i);
    fireEvent.change(within(userDialog).getByLabelText("User ID"), { target: { value: "alice" } });
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    fireEvent.click(within(userDialog).getByRole("button", { name: /^cancel$/i }));
    fireEvent.click(screen.getByRole("button", { name: /edit mood|set mood/i }));
    const moodDialog = editor(/mood/i);
    fireEvent.change(within(moodDialog).getByLabelText("Description"), { target: { value: "dirty mood" } });
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));
    fireEvent.click(within(moodDialog).getByRole("button", { name: /^cancel$/i }));
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });

  it("clears mood dirty ownership after authoritative set success", async () => {
    const onDirtyChange = vi.fn();
    await renderLoaded({ onDirtyChange });
    fireEvent.click(screen.getByRole("button", { name: /edit mood|set mood/i }));
    const dialog = editor(/mood/i);
    fireEvent.change(within(dialog).getByLabelText("Description"), { target: { value: "saved mood" } });
    bridge.apiPost.mockResolvedValue(ok({ mood_type: "happy", intensity: 0.5, duration_hours: 4, description: "saved mood", is_active: true }));
    fireEvent.click(within(dialog).getByRole("button", { name: /set|save/i }));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
    expect(screen.queryByRole("dialog", { name: /mood/i })).toBeNull();
  });

  it("clears mood dirty ownership after authoritative reset success", async () => {
    const onDirtyChange = vi.fn();
    await renderLoaded({ onDirtyChange });
    fireEvent.click(screen.getByRole("button", { name: /edit mood|set mood/i }));
    const dialog = editor(/mood/i);
    fireEvent.change(within(dialog).getByLabelText("Description"), { target: { value: "dirty mood" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^cancel$/i }));
    fireEvent.click(screen.getByRole("button", { name: /restore default mood/i }));
    bridge.apiPost.mockResolvedValue(ok({ mood_type: "calm", intensity: 0.1, duration_hours: 4, description: "default", is_active: true }));
    fireEvent.click(within(editor(/restore default mood/i)).getByRole("button", { name: /restore/i }));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
    expect(screen.queryByRole("dialog", { name: /restore default mood/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /edit mood|set mood/i }));
    expect(within(editor(/mood/i)).getByDisplayValue("default")).toBeTruthy();
  });

  it("clears mood dirty ownership on cancel and rebuilds baseline on reopen", async () => {
    const onDirtyChange = vi.fn();
    await renderLoaded({ onDirtyChange });
    fireEvent.click(screen.getByRole("button", { name: /edit mood|set mood/i }));
    const dialog = editor(/mood/i);
    fireEvent.change(within(dialog).getByLabelText("Description"), { target: { value: "discarded mood" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^cancel$/i }));
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
    fireEvent.click(screen.getByRole("button", { name: /edit mood|set mood/i }));
    expect(within(editor(/mood/i)).queryByDisplayValue("discarded mood")).toBeNull();
    expect(within(editor(/mood/i)).getByDisplayValue("Upbeat")).toBeTruthy();
  });

  it("disables close and action buttons and prevents duplicate create and update requests while pending", async () => {
    await renderLoaded(); const request = deferred<ReturnType<typeof ok>>(); bridge.apiPost.mockReturnValue(request.promise);
    fireEvent.click(screen.getByRole("button", { name: /new affection user|add user/i }));
    const dialog = editor(/new affection/i); fireEvent.change(within(dialog).getByLabelText("User ID"), { target: { value: "alice" } });
    const create = within(dialog).getByRole("button", { name: /^create$/i }); fireEvent.click(create); fireEvent.click(create);
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    expect(create).toHaveProperty("disabled", true); expect(within(dialog).getByRole("button", { name: "Close" })).toHaveProperty("disabled", true);
    await act(async () => { request.resolve(ok({ entity: user(), revision: "rev-2" })); await request.promise; });
  });

  it("prevents duplicate update and delete requests while their writes are pending", async () => {
    await renderLoaded(); const request = deferred<ReturnType<typeof ok>>(); bridge.apiPost.mockReturnValue(request.promise);
    openRowAction("alice", "View");
    const sheet = editor(/affection.*alice/i); fireEvent.click(within(sheet).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(sheet).getByLabelText(/affection score/i), { target: { value: "77" } });
    const save = within(sheet).getByRole("button", { name: /^save$/i }); fireEvent.click(save); fireEvent.click(save);
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    expect(save).toHaveProperty("disabled", true); expect(within(sheet).getByRole("button", { name: "Close" })).toHaveProperty("disabled", true);
    await act(async () => { request.resolve(ok({ entity: user({ affection_score: 77 }), revision: "rev-2" })); await request.promise; });

    const deleteRequest = deferred<ReturnType<typeof ok>>(); bridge.apiPost.mockReturnValue(deleteRequest.promise);
    fireEvent.click(within(sheet).getByRole("button", { name: /delete alice/i }));
    const confirm = await screen.findByRole("dialog", { name: /delete/i }); fireEvent.change(within(confirm).getByRole("textbox"), { target: { value: "alice" } });
    const remove = within(confirm).getByRole("button", { name: /confirm|delete/i }); fireEvent.click(remove); fireEvent.click(remove);
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(2));
    expect(remove).toHaveProperty("disabled", true);
    await act(async () => { deleteRequest.resolve(ok({})); await deleteRequest.promise; });
  });

  it("retains create and update drafts when the entity envelope is malformed", async () => {
    await renderLoaded(); bridge.apiPost.mockResolvedValue(ok({ entity: null, revision: "" }));
    fireEvent.click(screen.getByRole("button", { name: /new affection user|add user/i }));
    const createDialog = editor(/new affection/i); fireEvent.change(within(createDialog).getByLabelText("User ID"), { target: { value: "alice" } });
    fireEvent.click(within(createDialog).getByRole("button", { name: /^create$/i }));
    expect(await screen.findByText(/invalid affection.*entity|invalid.*response/i)).toBeTruthy();
    expect(within(createDialog).getByDisplayValue("alice")).toBeTruthy();

    fireEvent.click(within(createDialog).getByRole("button", { name: /^cancel$/i }));
    openRowAction("alice", "View");
    const sheet = editor(/affection.*alice/i); fireEvent.click(within(sheet).getByRole("button", { name: /^edit$/i }));
    fireEvent.change(within(sheet).getByLabelText(/affection score/i), { target: { value: "77" } });
    fireEvent.click(within(sheet).getByRole("button", { name: /^save$/i }));
    expect(await screen.findByText(/invalid affection.*entity|invalid.*response/i)).toBeTruthy();
    expect(within(sheet).getByDisplayValue("77")).toBeTruthy();
  });

  it("rejects an affection success entity with invalid authoritative field types and bounds", async () => {
    await renderLoaded();
    bridge.apiPost.mockResolvedValue(ok({ entity: user({ group_id: " ", affection_score: 101, revision: " ", level_name: null, interaction_count: "many", last_interaction: Infinity }), revision: "" }));
    fireEvent.click(screen.getByRole("button", { name: /new affection user|add user/i }));
    const dialog = editor(/new affection/i);
    fireEvent.change(within(dialog).getByLabelText("User ID"), { target: { value: "alice" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /^create$/i }));

    expect((await within(dialog).findByRole("alert")).textContent).toMatch(/invalid affection.*entity|invalid.*response/i);
    expect(within(dialog).getByDisplayValue("alice")).toBeTruthy();
  });

  it("retains both independent drafts when user and mood writes fail", async () => {
    await renderLoaded(); bridge.apiPost.mockRejectedValue(new Error("offline"));
    fireEvent.click(screen.getByRole("button", { name: /new affection user|add user/i }));
    const userDialog = editor(/new affection/i); fireEvent.change(within(userDialog).getByLabelText("User ID"), { target: { value: "alice" } });
    fireEvent.click(within(userDialog).getByRole("button", { name: /^create$/i }));
    expect(await screen.findByText("offline")).toBeTruthy(); expect(within(userDialog).getByDisplayValue("alice")).toBeTruthy();
    fireEvent.click(within(userDialog).getByRole("button", { name: /^cancel$/i }));
    fireEvent.click(screen.getByRole("button", { name: /edit mood|set mood/i }));
    const moodDialog = editor(/mood/i); fireEvent.change(within(moodDialog).getByLabelText("Description"), { target: { value: "keep mood" } });
    fireEvent.click(within(moodDialog).getByRole("button", { name: /set|save/i }));
    expect(await screen.findByText("offline")).toBeTruthy(); expect(within(moodDialog).getByDisplayValue("keep mood")).toBeTruthy();
  });

  it("consumes non-English translations in affection create and range validation UI", async () => {
    bridge.t.mockImplementation((key: string) => AFFECTION_SENTINELS[key] ?? key);
    await renderLoaded();

    fireEvent.click(screen.getByRole("button", { name: AFFECTION_SENTINELS["affection.newUser"] }));
    const createDialog = editor(AFFECTION_SENTINELS["affection.newUser"]);
    expect(within(createDialog).getByText(AFFECTION_SENTINELS["affection.createUserDescription"])).toBeTruthy();
    expect(within(createDialog).getByRole("button", { name: AFFECTION_SENTINELS["detail.create"] })).toBeTruthy();
    expect(within(createDialog).getByLabelText(AFFECTION_SENTINELS["affection.userId"])).toBeTruthy();
    expect(within(createDialog).getByLabelText(AFFECTION_SENTINELS["affection.groupId"])).toBeTruthy();
    const score = within(createDialog).getByLabelText(AFFECTION_SENTINELS["affection.score"]);
    fireEvent.change(score, { target: { value: "101" } });
    expect(within(createDialog).getAllByText(AFFECTION_SENTINELS["affection.scoreRange"]).length).toBeGreaterThan(0);
  });

  it("consumes non-English translations for affection edit, conflict, and delete actions", async () => {
    bridge.t.mockImplementation((key: string) => AFFECTION_SENTINELS[key] ?? key);
    await renderLoaded();
    bridge.apiPost.mockResolvedValueOnce({
      status: "error",
      code: "edit_conflict",
      message: "Concurrent update",
      data: { current_entity: user({ affection_score: 55 }), current_revision: "rev-remote" },
    });

    openRowAction("alice", "View");
    const sheet = editor(/alice/);
    fireEvent.click(within(sheet).getByRole("button", { name: AFFECTION_SENTINELS["detail.edit"] }));
    fireEvent.change(within(sheet).getByLabelText(AFFECTION_SENTINELS["affection.score"]), { target: { value: "77" } });
    fireEvent.click(within(sheet).getByRole("button", { name: AFFECTION_SENTINELS["common.save"] }));

    const conflict = await screen.findByRole("dialog", { name: AFFECTION_SENTINELS["affection.conflictTitle"] });
    expect(within(conflict).getByText(AFFECTION_SENTINELS["affection.conflictDescription"])).toBeTruthy();
    expect(within(conflict).getByRole("button", { name: AFFECTION_SENTINELS["config.conflict.loadRemote"] })).toBeTruthy();
    expect(within(conflict).getByRole("button", { name: AFFECTION_SENTINELS["affection.reapplyLocal"] })).toBeTruthy();
    fireEvent.click(within(conflict).getByRole("button", { name: AFFECTION_SENTINELS["config.conflict.loadRemote"] }));
    fireEvent.click(screen.getByRole("button", { name: `${AFFECTION_SENTINELS["common.delete"]} alice` }));
    expect(await screen.findByRole("dialog", { name: AFFECTION_SENTINELS["affection.deleteUser"] })).toBeTruthy();
  });

  it("consumes non-English mood field, range, and reset translations", async () => {
    bridge.t.mockImplementation((key: string) => AFFECTION_SENTINELS[key] ?? key);
    await renderLoaded();

    fireEvent.click(screen.getByRole("button", { name: AFFECTION_SENTINELS["affection.moodTitle"] }));
    const moodDialog = editor(AFFECTION_SENTINELS["affection.moodTitle"]);
    expect(within(moodDialog).getByText(AFFECTION_SENTINELS["affection.setMoodDescription"])).toBeTruthy();
    expect(within(moodDialog).getByLabelText(AFFECTION_SENTINELS["affection.moodType"])).toBeTruthy();
    expect(within(moodDialog).getByLabelText(AFFECTION_SENTINELS["affection.moodDescription"])).toBeTruthy();
    const intensity = within(moodDialog).getByLabelText(AFFECTION_SENTINELS["affection.moodIntensity"]);
    fireEvent.change(intensity, { target: { value: "0" } });
    expect(within(moodDialog).getAllByText(AFFECTION_SENTINELS["affection.intensityRange"]).length).toBeGreaterThan(0);
    const duration = within(moodDialog).getByLabelText(AFFECTION_SENTINELS["affection.moodDuration"]);
    fireEvent.change(duration, { target: { value: "169" } });
    expect(within(moodDialog).getAllByText(AFFECTION_SENTINELS["affection.durationRange"]).length).toBeGreaterThan(0);
    expect(within(moodDialog).getByRole("button", { name: AFFECTION_SENTINELS["affection.setMood"] })).toBeTruthy();
    fireEvent.click(within(moodDialog).getByRole("button", { name: AFFECTION_SENTINELS["common.cancel"] }));

    fireEvent.click(screen.getByRole("button", { name: AFFECTION_SENTINELS["affection.restoreDefaultMood"] }));
    const reset = editor(AFFECTION_SENTINELS["affection.restoreDefaultMood"]);
    expect(within(reset).getByText(AFFECTION_SENTINELS["affection.restoreDefaultMoodDescription"])).toBeTruthy();
    expect(within(reset).getByRole("button", { name: AFFECTION_SENTINELS["affection.restoreDefaultMoodAction"] })).toBeTruthy();
  });
});
