import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReconsolidationQueue } from "./ReconsolidationQueue";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale: ReturnType<typeof vi.fn>;
  getI18n: ReturnType<typeof vi.fn>;
  t: ReturnType<typeof vi.fn>;
}

type CandidateStatus = "pending" | "approved" | "rejected" | "failed" | "rolled_back";

/** 构造符合 Dashboard bridge 契约的成功响应。 */
function ok<T>(data: T) {
  return { status: "ok", data };
}

/** 构造可由测试显式完成或拒绝的 Promise。 */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

/** 构造列表使用的低敏再巩固候选 DTO。 */
function candidate(candidateId: string, status: CandidateStatus = "pending") {
  return {
    candidate_id: candidateId,
    status,
    change_summary: `summary-${candidateId}`,
    evidence_type: "llm_revision",
    reason_code: "proposed",
    created_at: "2026-08-01T10:00:00+00:00",
    updated_at: "2026-08-01T10:05:00+00:00",
    memory_id: "canonical-secret",
    source_revision: "revision-secret",
    metadata: { identity: "identity-secret" },
  };
}

/** 构造详情响应，额外包含受控的旧正文和拟议正文。 */
function detail(candidateId: string, status: CandidateStatus = "pending") {
  return ok({
    candidate: {
      ...candidate(candidateId, status),
      old_content: `old-${candidateId}`,
      proposed_content: `new-${candidateId}`,
    },
    actions: [
      {
        action: "stage",
        reason_code: "proposed",
        created_at: "2026-08-01T10:00:00+00:00",
        actor_id: "operator-secret",
      },
    ],
  });
}

/** 等待默认候选详情完成加载。 */
async function waitForDefaultDetail() {
  expect(await screen.findByText("old-recon-1")).toBeTruthy();
  expect(screen.getByText("new-recon-1")).toBeTruthy();
}

/** 在受控确认对话框中提交当前动作。 */
async function confirmCurrentAction() {
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", {
    name: /Confirm action|确认操作|Подтвердить действие/i,
  }));
}

describe("ReconsolidationQueue", () => {
  let bridge: BridgeMock;
  let currentStatus: CandidateStatus;

  beforeEach(() => {
    currentStatus = "pending";
    window.localStorage.removeItem("memora_lang");
    bridge = {
      apiGet: vi.fn(),
      apiPost: vi.fn(),
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
    };
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/review/reconsolidation") {
        return Promise.resolve(ok({
          items: [candidate("recon-1", currentStatus), candidate("recon-2", "approved")],
          total: 21,
          offset: Number(params.offset ?? 0),
          limit: Number(params.limit ?? 10),
        }));
      }
      if (path === "page/review/reconsolidation/detail") {
        return Promise.resolve(detail(params.candidate_id, currentStatus));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockImplementation((_path: string, body: Record<string, unknown>) => {
      currentStatus = body.action === "approve"
        ? "approved"
        : body.action === "reject"
          ? "rejected"
          : "rolled_back";
      return Promise.resolve(ok({
        candidate_id: body.candidate_id,
        action: body.action,
        status: currentStatus,
      }));
    });

    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: bridge,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    window.localStorage.removeItem("memora_lang");
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  it("loads the safe candidate list and fetches controlled detail fields", async () => {
    render(<ReconsolidationQueue showToast={() => undefined} />);

    expect(await screen.findByText("summary-recon-1")).toBeTruthy();
    expect(screen.getAllByText("Model revision evidence").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Revision proposed").length).toBeGreaterThan(0);
    await waitForDefaultDetail();

    expect(bridge.apiGet).toHaveBeenCalledWith("page/review/reconsolidation", {
      status: "pending",
      offset: "0",
      limit: "10",
    });
    expect(bridge.apiGet).toHaveBeenCalledWith("page/review/reconsolidation/detail", {
      candidate_id: "recon-1",
    });
    expect(screen.queryByText("canonical-secret")).toBe(null);
    expect(screen.queryByText("revision-secret")).toBe(null);
    expect(screen.queryByText("identity-secret")).toBe(null);
    expect(screen.queryByText("operator-secret")).toBe(null);
  });

  it("uses the server status filter and total-aware pagination", async () => {
    render(<ReconsolidationQueue showToast={() => undefined} />);
    await waitForDefaultDetail();

    fireEvent.click(screen.getByLabelText(/Candidate status|候选状态|Статус кандидата/i));
    const allStatuses = await screen.findByRole("option", {
      name: /All statuses|全部状态|Все статусы/i,
    });
    fireEvent.pointerDown(allStatuses, { pointerType: "mouse" });
    fireEvent.click(allStatuses);

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/review/reconsolidation", {
        status: "all",
        offset: "0",
        limit: "10",
      });
    });

    fireEvent.click(screen.getByRole("button", { name: /Next page|下一页|Следующая страница/i }));
    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/review/reconsolidation", {
        status: "all",
        offset: "10",
        limit: "10",
      });
    });
    expect(screen.getByText(/Page 2 of 3|第 2 页.*3|Страница 2.*3/i)).toBeTruthy();
  });

  it.each([
    ["approve", "pending", /^(Approve|批准|Одобрить)$/i],
    ["reject", "pending", /^(Reject|拒绝|Отклонить)$/i],
    ["rollback", "approved", /^(Rollback|回滚|Откатить)$/i],
  ] as const)("submits the %s action and refreshes list plus detail", async (action, status, label) => {
    currentStatus = status;
    const showToast = vi.fn();
    render(<ReconsolidationQueue showToast={showToast} />);
    await waitForDefaultDetail();

    fireEvent.click(screen.getByRole("button", { name: label }));
    await confirmCurrentAction();

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/review/reconsolidation/action", {
        candidate_id: "recon-1",
        action,
      });
    });
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledTimes(4));
    expect(showToast).toHaveBeenCalledWith(expect.stringMatching(/submitted|已提交|отправлено/i));
  });

  it("prevents same-tick duplicate actions while one write is pending", async () => {
    const action = deferred<ReturnType<typeof ok>>();
    bridge.apiPost.mockReturnValueOnce(action.promise);
    render(<ReconsolidationQueue showToast={() => undefined} />);
    await waitForDefaultDetail();

    const approve = screen.getByRole("button", { name: /^(Approve|批准|Одобрить)$/i });
    fireEvent.click(approve);
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", {
      name: /Confirm action|确认操作|Подтвердить действие/i,
    });
    act(() => {
      confirm.click();
      confirm.click();
    });

    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
    expect(confirm).toHaveProperty("disabled", true);
    await act(async () => {
      action.resolve(ok({ candidate_id: "recon-1", action: "approve", status: "approved" }));
    });
    await waitFor(() => expect(approve).toHaveProperty("disabled", false));
  });

  it("keeps the current detail and exposes inline feedback when an action fails", async () => {
    const showToast = vi.fn();
    bridge.apiPost.mockResolvedValueOnce({
      status: "error",
      message: "candidate changed",
      code: "reconsolidation_review_conflict",
    });
    render(<ReconsolidationQueue showToast={showToast} />);
    await waitForDefaultDetail();

    fireEvent.click(screen.getByRole("button", { name: /^(Approve|批准|Одобрить)$/i }));
    await confirmCurrentAction();

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("candidate changed"));
    expect(screen.getByText("old-recon-1")).toBeTruthy();
    expect(screen.getByText("new-recon-1")).toBeTruthy();
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining("candidate changed"), true);
  });

  it("renders stable loading, empty, and list-error states", async () => {
    const pendingList = deferred<ReturnType<typeof ok>>();
    bridge.apiGet.mockReturnValueOnce(pendingList.promise);
    const { unmount } = render(<ReconsolidationQueue showToast={() => undefined} />);

    expect(screen.getByText(/Loading candidates|正在加载候选|Загрузка кандидатов/i)).toBeTruthy();
    await act(async () => {
      pendingList.resolve(ok({ items: [], total: 0, offset: 0, limit: 10 }));
    });
    expect(await screen.findByText(/No reconsolidation candidates|暂无再巩固候选|Нет кандидатов/i)).toBeTruthy();

    unmount();
    bridge.apiGet.mockReset();
    bridge.apiGet.mockResolvedValue({ status: "error", message: "list unavailable" });
    render(<ReconsolidationQueue showToast={() => undefined} />);
    expect((await screen.findByRole("alert")).textContent).toContain("list unavailable");
  });

  it("renders a disabled feature state without requesting detail or showing an error", async () => {
    const showToast = vi.fn();
    bridge.apiGet.mockReset();
    bridge.apiGet.mockResolvedValueOnce(ok({
      enabled: false,
      items: [],
      total: 0,
      offset: 0,
      limit: 10,
    }));

    render(<ReconsolidationQueue showToast={showToast} />);

    expect((await screen.findAllByText(
      /Reconsolidation is not enabled|记忆再巩固功能未启用|Реконсолидация памяти не включена/i,
    )).length).toBe(2);
    expect(bridge.apiGet).toHaveBeenCalledTimes(1);
    expect(showToast).not.toHaveBeenCalled();
  });

  it.each([
    ["zh-CN", "再巩固候选", "旧正文", "拟议正文"],
    ["en-US", "Reconsolidation candidates", "Original content", "Proposed content"],
    ["ru-RU", "Кандидаты реконсолидирования", "Исходный текст", "Предлагаемый текст"],
  ])("renders %s copy without raw translation keys", async (locale, title, oldContent, proposedContent) => {
    bridge.getLocale.mockReturnValue(locale);
    render(<ReconsolidationQueue showToast={() => undefined} />);

    expect(await screen.findByText(title)).toBeTruthy();
    await waitForDefaultDetail();
    expect(screen.getByText(oldContent)).toBeTruthy();
    expect(screen.getByText(proposedContent)).toBeTruthy();
    const region = screen.getByRole("region", { name: title });
    expect(within(region).queryByText(/intelligence\.reconsolidation\./)).toBe(null);
  });
});
