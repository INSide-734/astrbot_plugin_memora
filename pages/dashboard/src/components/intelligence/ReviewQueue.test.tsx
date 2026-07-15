import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReviewQueue } from "./ReviewQueue";

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

function reviewDetail(itemId: string, memoryId: string, action = "flagged") {
  return ok({
    item: {
      item_id: itemId,
      memory_id: memoryId,
      reasons: ["duplicate"],
      severity: "medium",
      status: "open",
      content_preview: `${memoryId} content`,
      metadata: {},
      created_at: 1783150200,
      updated_at: 1783150200,
    },
    actions: [{
      action_id: `action-${itemId}`,
      item_id: itemId,
      action,
      actor_id: null,
      payload: {},
      created_at: 1783150200,
    }],
  });
}

async function waitForDetailReady() {
  expect(await screen.findByLabelText(/Edit content|编辑内容|Редактировать/i)).toBeTruthy();
}

describe("ReviewQueue", () => {
  let bridge: BridgeMock;

  beforeEach(() => {
    bridge = {
      apiGet: vi.fn(),
      apiPost: vi.fn(),
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
    };

    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/review/items") {
        return Promise.resolve(ok({
          items: [
            {
              item_id: "review-duplicate-1",
              memory_id: "mem-duplicate-1",
              reasons: ["duplicate"],
              severity: "medium",
              status: "open",
              content_preview: "重复记忆：用户周末喜欢在安静咖啡馆工作。",
              metadata: { provenance: "atom_store", session_id: "sess-1", source: "mock" },
              created_at: 1783150200,
              updated_at: 1783150200,
            },
            {
              item_id: "review-stale-1",
              memory_id: "mem-stale-1",
              reasons: ["stale"],
              severity: "low",
              status: "approved",
              content_preview: "旧偏好：用户曾经使用 class component。",
              metadata: { provenance: "quality_scorer", session_id: "sess-2" },
              created_at: 1783146600,
              updated_at: 1783147000,
            },
          ],
          total: 2,
        }));
      }
      if (path === "page/review/items/detail") {
        return Promise.resolve(ok({
          item: {
            item_id: "review-duplicate-1",
            memory_id: "mem-duplicate-1",
            reasons: ["duplicate"],
            severity: "medium",
            status: "open",
            content_preview: "重复记忆：用户周末喜欢在安静咖啡馆工作。",
            metadata: { provenance: "atom_store", session_id: "sess-1", source: "mock" },
            created_at: 1783150200,
            updated_at: 1783150200,
          },
          actions: [
            {
              action_id: "act-1",
              item_id: "review-duplicate-1",
              action: "flagged",
              actor_id: null,
              payload: { reason: "duplicate" },
              created_at: 1783150200,
            },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    bridge.apiPost.mockResolvedValue(ok({ accepted: true }));

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

  it("renders review items and exposes status reason severity filters", async () => {
    const { container } = render(<ReviewQueue showToast={() => undefined} />);

    expect((await screen.findAllByText(/duplicate|重复/i)).length).toBeGreaterThan(0);
    expect(screen.getByLabelText(/Status|状态/i)).toBeTruthy();
    expect(screen.getByLabelText(/Reason|原因/i)).toBeTruthy();
    expect(screen.getByLabelText(/Severity|严重/i)).toBeTruthy();
    expect(screen.getByPlaceholderText(/Search|搜索/i)).toBeTruthy();
    expect(container.querySelector("select")).toBe(null);
    await waitForDetailReady();
    expect(screen.getByText("--")).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/Reason|原因/i), { target: { value: "stale" } });
    await waitFor(() => {
      expect(screen.getByText("mem-stale-1")).toBeTruthy();
    });
  });

  it("moves the shared current-item treatment with the detail selection", async () => {
    render(<ReviewQueue showToast={() => undefined} />);

    const firstItem = await screen.findByRole("button", {
      name: /mem-duplicate-1/,
    });
    const secondItem = screen.getByRole("button", { name: /mem-stale-1/ });

    expect(firstItem.getAttribute("aria-current")).toBe("true");
    expect(firstItem.className).toContain(
      "shadow-[inset_2px_0_0_var(--selection-indicator)]",
    );
    expect(secondItem.hasAttribute("aria-current")).toBe(false);

    fireEvent.click(secondItem);

    expect(secondItem.getAttribute("aria-current")).toBe("true");
    expect(firstItem.hasAttribute("aria-current")).toBe(false);
  });

  it("renders fixed review chrome from dashboard i18n", async () => {
    bridge.getLocale.mockReturnValue("zh-CN");

    render(<ReviewQueue showToast={() => undefined} />);

    expect((await screen.findAllByText("复核队列")).length).toBeGreaterThan(0);
    expect(screen.getByText(/可见/)).toBeTruthy();
    expect(screen.getByText("全部状态")).toBeTruthy();
    expect(screen.getByText("全部原因")).toBeTruthy();
    expect(screen.getByText("全部严重度")).toBeTruthy();
    expect(screen.getByPlaceholderText("搜索复核项")).toBeTruthy();
    expect(await screen.findByText("记忆复核")).toBeTruthy();
    expect(screen.getByText("记忆内容")).toBeTruthy();
    expect(screen.getByText("复核原因")).toBeTruthy();
    expect(screen.getByText("来源信息")).toBeTruthy();
    expect(screen.getByText("候选操作")).toBeTruthy();
    expect(screen.getByText("操作历史")).toBeTruthy();
    expect(screen.queryByText("Review queue")).toBe(null);
    expect(screen.queryByText("Memory review")).toBe(null);
  });

  it("localizes review enums in list and filters while preserving unknown values", async () => {
    bridge.getLocale.mockReturnValue("zh-CN");
    bridge.t.mockImplementation((key: string) => ({
      "dashboard.intelligence.review.status.open": "待处理",
      "dashboard.intelligence.review.status.approved": "已批准",
      "dashboard.severity.medium": "中等",
      "dashboard.severity.low": "低",
      "dashboard.intelligence.review.reason.duplicate": "重复项",
      "dashboard.intelligence.review.reason.stale": "陈旧",
    })[key] ?? key);

    render(<ReviewQueue showToast={() => undefined} />);

    expect((await screen.findAllByText("重复项")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("中等").length).toBeGreaterThan(0);
    expect(screen.getAllByText("待处理").length).toBeGreaterThan(0);
    expect(await screen.findByText("flagged")).toBeTruthy();

    const reasonFilter = screen.getByLabelText("原因");
    fireEvent.click(reasonFilter);
    expect(await screen.findByRole("option", { name: "陈旧" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "stale" })).toBe(null);
  });

  it("formats queue and detail timestamps with the dashboard locale", async () => {
    bridge.getLocale.mockReturnValue("ru-RU");
    render(<ReviewQueue showToast={() => undefined} />);

    await waitForDetailReady();
    const expected = new Date(1783150200 * 1000).toLocaleString("ru-RU");
    expect(screen.getAllByText(expected).length).toBeGreaterThan(0);
  });

  it("requires inline confirmation before deleting a review item", async () => {
    render(<ReviewQueue showToast={() => undefined} />);

    expect(await screen.findByText("mem-duplicate-1")).toBeTruthy();
    await waitForDetailReady();
    fireEvent.click(screen.getByRole("button", { name: /Delete|删除/i }));

    const confirmMessage = screen.getByText(/Confirm delete|确认删除/i);
    expect(confirmMessage).toBeTruthy();
    expect(bridge.apiPost).not.toHaveBeenCalled();

    const confirmBar = confirmMessage.closest("div");
    if (!confirmBar) throw new Error("expected delete confirmation bar");
    fireEvent.click(within(confirmBar).getByRole("button", { name: /Confirm|确认/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/review/action", {
        review_id: "review-duplicate-1",
        action: "delete",
        payload: {},
        confirmed: true,
      });
    });
    await waitFor(() => {
      expect(screen.queryByText(/Confirm delete|确认删除/i)).toBe(null);
    });
  });

  it("requires confirmation before archive and clears confirmation after success", async () => {
    render(<ReviewQueue showToast={() => undefined} />);

    expect(await screen.findByText("mem-duplicate-1")).toBeTruthy();
    await waitForDetailReady();
    fireEvent.click(screen.getByRole("button", { name: /Archive|归档/i }));

    const confirmMessage = screen.getByText(/Confirm archive|确认归档/i);
    expect(confirmMessage).toBeTruthy();
    expect(bridge.apiPost).not.toHaveBeenCalled();

    const confirmBar = confirmMessage.closest("div");
    if (!confirmBar) throw new Error("expected archive confirmation bar");
    fireEvent.click(within(confirmBar).getByRole("button", { name: /Confirm|确认/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/review/action", {
        review_id: "review-duplicate-1",
        action: "archive",
        payload: {},
        confirmed: true,
      });
    });
    await waitFor(() => {
      expect(screen.queryByText(/Confirm archive|确认归档/i)).toBe(null);
    });
  });

  it("requires merge confirmation and posts target memory id", async () => {
    render(<ReviewQueue showToast={() => undefined} />);

    expect(await screen.findByText("mem-duplicate-1")).toBeTruthy();
    await waitForDetailReady();
    fireEvent.change(screen.getByPlaceholderText("target_memory_id"), {
      target: { value: "mem-target-9" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Merge|合并/i }));

    const confirmMessage = screen.getByText(/Confirm merge|确认合并/i);
    expect(confirmMessage).toBeTruthy();
    expect(bridge.apiPost).not.toHaveBeenCalled();

    const confirmBar = confirmMessage.closest("div");
    if (!confirmBar) throw new Error("expected merge confirmation bar");
    fireEvent.click(within(confirmBar).getByRole("button", { name: /Confirm|确认/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/review/action", {
        review_id: "review-duplicate-1",
        action: "merge",
        payload: { target_memory_id: "mem-target-9" },
        confirmed: true,
      });
    });
    await waitFor(() => {
      expect(screen.queryByText(/Confirm merge|确认合并/i)).toBe(null);
    });
  });

  it("posts edited content without confirmation", async () => {
    const showToast = vi.fn();
    bridge.t.mockImplementation((key: string) => (
      key === "dashboard.intelligence.review.action.edit" ? "Edit action" : key
    ));
    render(<ReviewQueue showToast={showToast} />);

    expect(await screen.findByText("mem-duplicate-1")).toBeTruthy();
    await waitForDetailReady();
    fireEvent.change(screen.getByLabelText(/Edit content|编辑内容/i), {
      target: { value: "修订后的记忆内容" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Edit|编辑/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/review/action", {
        review_id: "review-duplicate-1",
        action: "edit",
        payload: { content: "修订后的记忆内容" },
      });
    });
    expect(showToast).toHaveBeenCalledWith("Review action submitted: Edit action");
  });

  it("guards same-tick review actions and preserves draft confirmation after failure", async () => {
    const showToast = vi.fn();
    let resolveAction!: (value: { status: "error"; message: string }) => void;
    bridge.apiPost.mockReturnValueOnce(new Promise((resolve) => { resolveAction = resolve; }));

    render(<ReviewQueue showToast={showToast} />);

    expect(await screen.findByText("mem-duplicate-1")).toBeTruthy();
    await waitForDetailReady();
    const draft = screen.getByPlaceholderText("target_memory_id") as HTMLInputElement;
    fireEvent.change(draft, { target: { value: "mem-target-9" } });
    fireEvent.click(screen.getByRole("button", { name: /Merge|合并/i }));
    const confirmBar = screen.getByText(/Confirm merge|确认合并/i).closest("div");
    if (!confirmBar) throw new Error("expected merge confirmation bar");
    const confirm = within(confirmBar).getByRole("button", { name: /Confirm|确认/i });
    act(() => {
      confirm.click();
      confirm.click();
    });

    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
    expect(confirm).toHaveProperty("disabled", true);
    expect(within(screen.getByRole("region", { name: /Memory review|记忆复核|Ревью памяти/i })).getByText(/Loading|加载|Загрузка/i)).toBeTruthy();

    await act(async () => { resolveAction({ status: "error", message: "merge target missing" }); });
    await waitFor(() => expect(showToast).toHaveBeenCalledWith("ApiRequestError: merge target missing", true));
    expect(await screen.findByText(/Confirm merge|确认合并/i)).toBeTruthy();
    expect(draft.value).toBe("mem-target-9");
    expect(screen.getByRole("alert").textContent).toContain("ApiRequestError: merge target missing");
  });

  it("keeps pending and error feedback inside the stable memory review region", async () => {
    let resolveAction!: (value: { status: "error"; message: string }) => void;
    bridge.apiPost.mockReturnValueOnce(new Promise((resolve) => { resolveAction = resolve; }));

    const { container } = render(<ReviewQueue showToast={() => undefined} />);
    expect(await screen.findByText("mem-duplicate-1")).toBeTruthy();
    await waitForDetailReady();
    fireEvent.click(screen.getByRole("button", { name: /Archive|归档/i }));
    const confirmBar = screen.getByText(/Confirm archive|确认归档/i).closest("div");
    if (!confirmBar) throw new Error("expected archive confirmation bar");
    fireEvent.click(within(confirmBar).getByRole("button", { name: /Confirm|确认/i }));

    const region = screen.getByRole("region", { name: /Memory review|记忆复核|Ревью памяти/i });
    expect(within(region).getByText(/Loading|加载|Загрузка/i)).toBeTruthy();
    expect(within(region).getAllByRole("button", { name: /Archive…|归档…|Архивировать…/i })).toHaveLength(2);
    expect(within(region).getByRole("heading", { name: "mem-duplicate-1" })).toBeTruthy();
    const grid = region.parentElement;
    expect(grid).toBeTruthy();
    expect(grid?.children).toHaveLength(2);
    expect(grid).toBe(container.querySelector(".xl\\:grid-cols-\\[420px_1fr\\]"));

    await act(async () => { resolveAction({ status: "error", message: "archive failed" }); });
    await waitFor(() => expect(within(region).getByRole("alert").textContent).toContain("archive failed"));
    expect(within(region).getByRole("heading", { name: "mem-duplicate-1" })).toBeTruthy();
    expect(grid?.children).toHaveLength(2);
  });

  it("keeps only the latest selected detail when responses resolve out of order", async () => {
    const detailA = deferred<ReturnType<typeof reviewDetail>>();
    const detailB = deferred<ReturnType<typeof reviewDetail>>();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/review/items") return Promise.resolve(ok({
        items: [
          { item_id: "review-a", memory_id: "memory-a", reasons: ["duplicate"], severity: "medium", status: "open", content_preview: "A", metadata: {}, created_at: 1, updated_at: 1 },
          { item_id: "review-b", memory_id: "memory-b", reasons: ["stale"], severity: "low", status: "open", content_preview: "B", metadata: {}, created_at: 2, updated_at: 2 },
        ], total: 2,
      }));
      if (path === "page/review/items/detail") return params.review_id === "review-a" ? detailA.promise : detailB.promise;
      return Promise.resolve(ok({}));
    });
    render(<ReviewQueue showToast={() => undefined} />);
    fireEvent.click(await screen.findByRole("button", { name: /memory-b/ }));
    await act(async () => { detailB.resolve(reviewDetail("review-b", "memory-b", "edited")); });
    expect(await screen.findByRole("heading", { name: "memory-b" })).toBeTruthy();
    expect(screen.getByText("edited")).toBeTruthy();
    await act(async () => { detailA.resolve(reviewDetail("review-a", "memory-a")); });
    expect(screen.getByRole("heading", { name: "memory-b" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "memory-a" })).toBe(null);
    expect(screen.queryByText("flagged")).toBe(null);
  });

  it("clears the previous detail immediately and keeps it cleared when the new detail fails", async () => {
    const detailB = deferred<ReturnType<typeof reviewDetail>>();
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/review/items") return Promise.resolve(ok({ items: [
        { item_id: "review-a", memory_id: "memory-a", reasons: ["duplicate"], severity: "medium", status: "open", content_preview: "A", metadata: {}, created_at: 1, updated_at: 1 },
        { item_id: "review-b", memory_id: "memory-b", reasons: ["stale"], severity: "low", status: "open", content_preview: "B", metadata: {}, created_at: 2, updated_at: 2 },
      ], total: 2 }));
      if (path === "page/review/items/detail") return params.review_id === "review-a" ? Promise.resolve(reviewDetail("review-a", "memory-a")) : detailB.promise;
      return Promise.resolve(ok({}));
    });
    render(<ReviewQueue showToast={() => undefined} />);
    expect(await screen.findByRole("heading", { name: "memory-a" })).toBeTruthy();
    expect(screen.getByText("flagged")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /memory-b/ }));
    expect(screen.queryByRole("heading", { name: "memory-a" })).toBe(null);
    expect(screen.queryByText("flagged")).toBe(null);
    await act(async () => { detailB.reject(new Error("detail b failed")); });
    await waitFor(() => expect(screen.queryByRole("heading", { name: "memory-a" })).toBe(null));
    expect(screen.queryByText("flagged")).toBe(null);
  });

  it("never runs actions for a detail whose item id differs from the selected review", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/review/items") return Promise.resolve(ok({ items: [
        { item_id: "review-b", memory_id: "memory-b", reasons: ["stale"], severity: "low", status: "open", content_preview: "B", metadata: {}, created_at: 2, updated_at: 2 },
      ], total: 1 }));
      if (path === "page/review/items/detail") return Promise.resolve(reviewDetail("review-a", "memory-a"));
      return Promise.resolve(ok({}));
    });
    render(<ReviewQueue showToast={() => undefined} />);
    expect(await screen.findByRole("heading", { name: "memory-a" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Edit|编辑/i }));
    expect(bridge.apiPost).not.toHaveBeenCalled();
  });

  it("attributes pending and failed action feedback to the initiating review", async () => {
    const action = deferred<{ status: "error"; message: string }>();
    bridge.apiPost.mockReturnValueOnce(action.promise);
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/review/items") return Promise.resolve(ok({ items: [
        { item_id: "review-a", memory_id: "memory-a", reasons: ["duplicate"], severity: "medium", status: "open", content_preview: "A", metadata: {}, created_at: 1, updated_at: 1 },
        { item_id: "review-b", memory_id: "memory-b", reasons: ["stale"], severity: "low", status: "open", content_preview: "B", metadata: {}, created_at: 2, updated_at: 2 },
      ], total: 2 }));
      if (path === "page/review/items/detail") return Promise.resolve(params.review_id === "review-a" ? reviewDetail("review-a", "memory-a") : reviewDetail("review-b", "memory-b"));
      return Promise.resolve(ok({}));
    });
    render(<ReviewQueue showToast={() => undefined} />);
    expect(await screen.findByRole("heading", { name: "memory-a" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Edit|编辑/i }));
    const region = screen.getByRole("region", { name: /Memory review|记忆复核|Ревью памяти/i });
    expect(within(region).getByText(/Loading|加载|Загрузка/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /memory-b/ }));
    expect(await screen.findByRole("heading", { name: "memory-b" })).toBeTruthy();
    const pendingItemButton = within(region).getByRole("button", { name: /Edit|编辑/i });
    expect(pendingItemButton).toHaveProperty("disabled", true);
    fireEvent.click(pendingItemButton);
    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
    expect(within(region).queryByText(/Loading|加载|Загрузка/i)).toBe(null);
    await act(async () => { action.resolve({ status: "error", message: "action a failed" }); });
    await waitFor(() => expect(within(region).queryByRole("alert")).toBe(null));
  });
});
