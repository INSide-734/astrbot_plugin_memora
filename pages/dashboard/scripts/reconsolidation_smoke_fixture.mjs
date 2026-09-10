const CANDIDATES = [
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
  },
];

/** 生成与生产列表 API 一致的低敏候选摘要。 */
function summary(candidate) {
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

/** 为 browser smoke 提供安全、可分页的再巩固 Page API 响应。 */
export function reconsolidationSmokePayload(method, path, payload = {}) {
  if (method === "GET" && path === "review/reconsolidation") {
    const status = String(payload.status || "pending");
    const offset = Math.max(0, Number(payload.offset || 0));
    const limit = Math.max(1, Number(payload.limit || 10));
    const filtered = CANDIDATES.filter((candidate) => status === "all" || candidate.status === status);
    return {
      items: filtered.slice(offset, offset + limit).map(summary),
      total: filtered.length,
      offset,
      limit,
    };
  }
  if (method === "GET" && path === "review/reconsolidation/detail") {
    const candidate = CANDIDATES.find((entry) => entry.candidate_id === payload.candidate_id);
    if (!candidate) return {};
    return {
      candidate: { ...summary(candidate), old_content: candidate.old_content, proposed_content: candidate.proposed_content },
      actions: [{ action: "stage", reason_code: "proposed", created_at: candidate.created_at }],
    };
  }
  if (method === "POST" && path === "review/reconsolidation/action") {
    return {
      candidate_id: String(payload.candidate_id || ""),
      action: String(payload.action || ""),
      status: payload.action === "rollback" ? "rolled_back" : payload.action === "reject" ? "rejected" : "approved",
    };
  }
  return undefined;
}
