import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

async function waitForDetailReady() {
  expect(await screen.findByLabelText(/Edit content|编辑内容/i)).toBeTruthy();
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

    expect((await screen.findAllByText(/duplicate|重复/)).length).toBeGreaterThan(0);
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
    render(<ReviewQueue showToast={() => undefined} />);

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
  });

  it("shows backend error envelopes through toast", async () => {
    const showToast = vi.fn();
    bridge.apiPost.mockResolvedValueOnce({ status: "error", message: "merge target missing" });

    render(<ReviewQueue showToast={showToast} />);

    expect(await screen.findByText("mem-duplicate-1")).toBeTruthy();
    await waitForDetailReady();
    fireEvent.change(screen.getByPlaceholderText("target_memory_id"), {
      target: { value: "mem-target-9" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Merge|合并/i }));
    const confirmBar = screen.getByText(/Confirm merge|确认合并/i).closest("div");
    if (!confirmBar) throw new Error("expected merge confirmation bar");
    fireEvent.click(within(confirmBar).getByRole("button", { name: /Confirm|确认/i }));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith("Error: merge target missing", true);
    });
    expect(screen.getByText(/Confirm merge|确认合并/i)).toBeTruthy();
  });
});
