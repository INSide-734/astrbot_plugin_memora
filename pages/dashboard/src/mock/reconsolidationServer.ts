import type {
  ReconsolidationReviewAction,
  ReconsolidationReviewActionValue,
  ReconsolidationReviewDetail,
  ReconsolidationReviewItem,
} from "@/types/intelligence";

interface MockResponse {
  status: "ok" | "error";
  data?: unknown;
  message?: string;
  code?: string;
}

interface MockCandidate extends ReconsolidationReviewDetail {
  actions: ReconsolidationReviewAction[];
}

const CANDIDATE_SEEDS: MockCandidate[] = [
  {
    candidate_id: "recon-smoke-pending",
    status: "pending",
    change_summary: "修正用户近期工作地点偏好",
    evidence_type: "llm_revision",
    reason_code: "proposed",
    created_at: "2026-08-01T10:00:00+00:00",
    updated_at: "2026-08-01T10:05:00+00:00",
    old_content: "用户周末通常在家工作。",
    proposed_content: "用户近期更喜欢周末在安静的咖啡馆工作。",
    actions: [
      {
        action: "stage",
        reason_code: "proposed",
        created_at: "2026-08-01T10:00:00+00:00",
      },
    ],
  },
  {
    candidate_id: "recon-smoke-approved",
    status: "approved",
    change_summary: "更新前端技术偏好",
    evidence_type: "llm_revision",
    reason_code: "applied",
    created_at: "2026-07-31T09:00:00+00:00",
    updated_at: "2026-07-31T09:20:00+00:00",
    old_content: "用户偏好 class component。",
    proposed_content: "用户目前偏好 React function component。",
    actions: [
      {
        action: "stage",
        reason_code: "proposed",
        created_at: "2026-07-31T09:00:00+00:00",
      },
      {
        action: "apply",
        reason_code: "applied",
        created_at: "2026-07-31T09:20:00+00:00",
      },
    ],
  },
];

let candidates: MockCandidate[] = [];
const REVIEW_STATUSES = new Set(["all", "pending", "approved", "rejected", "failed", "rolled_back"]);
const ACTION_FIELDS = new Set(["candidate_id", "action"]);

/** 构造隔离副本，避免 Mock 响应被消费方原地修改。 */
function clone<T>(value: T): T {
  return structuredClone(value);
}

/** 构造统一成功 envelope。 */
function ok(data: unknown): MockResponse {
  return { status: "ok", data: clone(data) };
}

/** 构造统一错误 envelope。 */
function error(message: string, code: string): MockResponse {
  return { status: "error", message, code };
}

/** 严格解析 Mock 查询中的非负整数并应用上限。 */
function parsePageValue(value: string | undefined, fallback: number, maximum: number): number {
  const parsed = Number(value ?? fallback);
  if (!Number.isInteger(parsed) || parsed < 0) return fallback;
  return Math.min(parsed, maximum);
}

/** 从内部候选构造与生产 Page API 一致的列表安全 DTO。 */
function candidateSummary(candidate: MockCandidate): ReconsolidationReviewItem {
  return {
    candidate_id: candidate.candidate_id,
    status: candidate.status,
    change_summary: candidate.change_summary,
    evidence_type: candidate.evidence_type,
    reason_code: candidate.reason_code,
    created_at: candidate.created_at,
    updated_at: candidate.updated_at,
  };
}

/** 从内部候选构造允许正文对照的详情安全 DTO。 */
function candidateDetail(candidate: MockCandidate): ReconsolidationReviewDetail {
  return {
    ...candidateSummary(candidate),
    old_content: candidate.old_content,
    proposed_content: candidate.proposed_content,
  };
}

/** 处理再巩固列表和详情 GET 路由，未命中时返回 null。 */
export function handleReconsolidationGet(
  path: string,
  params: Record<string, string>,
): MockResponse | null {
  if (path === "review/reconsolidation") {
    const status = params.status || "pending";
    if (!REVIEW_STATUSES.has(status)) {
      return error("再巩固状态无效", "invalid_request");
    }
    const offset = parsePageValue(params.offset, 0, 1_000_000);
    const limit = Math.max(1, parsePageValue(params.limit, 50, 200));
    const filtered = candidates
      .filter((candidate) => status === "all" || candidate.status === status)
      .sort((left, right) => String(right.created_at).localeCompare(String(left.created_at)));
    return ok({
      items: filtered.slice(offset, offset + limit).map(candidateSummary),
      total: filtered.length,
      offset,
      limit,
    });
  }
  if (path === "review/reconsolidation/detail") {
    const candidate = candidates.find((entry) => entry.candidate_id === params.candidate_id);
    if (!candidate) return error("再巩固候选不存在", "reconsolidation_review_not_found");
    return ok({ candidate: candidateDetail(candidate), actions: candidate.actions });
  }
  return null;
}

/** 处理 approve、reject、rollback 动作并模拟生产状态 CAS。 */
export function handleReconsolidationPost(
  path: string,
  body: Record<string, unknown>,
): MockResponse | null {
  if (path !== "review/reconsolidation/action") return null;
  if (Object.keys(body).some((key) => !ACTION_FIELDS.has(key))) {
    return error("再巩固复核请求无效", "invalid_request");
  }
  const candidateId = String(body.candidate_id ?? "");
  const action = String(body.action ?? "") as ReconsolidationReviewActionValue;
  if (!candidateId || !["approve", "reject", "rollback"].includes(action)) {
    return error("再巩固复核请求无效", "invalid_request");
  }
  const candidate = candidates.find((entry) => entry.candidate_id === candidateId);
  if (!candidate) return error("再巩固候选不存在", "reconsolidation_review_not_found");
  const allowed = action === "rollback" ? candidate.status === "approved" : candidate.status === "pending";
  if (!allowed) return error("再巩固候选已变化，请刷新后重试", "reconsolidation_review_conflict");

  candidate.status = action === "approve"
    ? "approved"
    : action === "reject"
      ? "rejected"
      : "rolled_back";
  candidate.reason_code = action === "approve"
    ? "applied"
    : action === "reject"
      ? "manual_reject"
      : "rolled_back";
  candidate.updated_at = "2026-08-01T11:00:00+00:00";
  candidate.actions.push({
    action: action === "approve" ? "apply" : action,
    reason_code: candidate.reason_code,
    created_at: candidate.updated_at,
  });
  return ok({ candidate_id: candidateId, action, status: candidate.status });
}

/** 恢复确定性的候选与动作历史，供测试和 browser smoke 重放。 */
export function resetReconsolidationMockState(): void {
  candidates = clone(CANDIDATE_SEEDS);
}

resetReconsolidationMockState();
