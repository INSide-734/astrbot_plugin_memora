/**
 * 构造不含 query、身份、正文、canonical ID 或任意 metadata 的 smoke trace。
 *
 * @param {string} traceId 仅用于 UI 关联的随机或固定 trace 码。
 * @returns {Record<string, unknown>} 与生产 API 一致的安全 DTO。
 */
export function recallTracePayload(traceId = "trace-smoke-coffee") {
  return {
    trace_id: traceId,
    total_ms: 84.2,
    stages: [
      {
        name: "search_memories",
        duration_ms: 82.7,
        candidate_count: 7,
        metadata: {},
      },
      {
        name: "injection_decision",
        duration_ms: 1.5,
        candidate_count: 7,
        metadata: {
          routing_mode: "hybrid",
          resolved_preset: "balanced",
          reason_code: "AUTO_FALLBACK",
        },
      },
    ],
    results: [
      {
        rank: 1,
        initial_score: 0.71,
        final_score: 0.93,
        score_contributions: [
          { source: "bm25", score: 0.62, weight: 0.35 },
        ],
        metadata: { memory_type: "preference", status: "active" },
      },
    ],
    filtered: [],
    created_at: 1_782_000_000,
    metadata: { debug_trace_available: true },
  };
}
