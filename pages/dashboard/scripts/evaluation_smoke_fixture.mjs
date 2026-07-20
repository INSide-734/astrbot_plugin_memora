const variants = [
  { name: "baseline", available: true, reason_code: "available", default_selected: true },
  { name: "A", available: false, reason_code: "equivalent_to_baseline", default_selected: false },
  { name: "B", available: true, reason_code: "available", default_selected: false },
  {
    name: "C",
    available: false,
    reason_code: "readonly_snapshot_cannot_activate_worker",
    default_selected: false,
  },
  { name: "graph_expansion_off", available: true, reason_code: "available", default_selected: true },
  { name: "topic_expansion_off", available: true, reason_code: "available", default_selected: true },
  { name: "final_reranker_off", available: true, reason_code: "available", default_selected: false },
  {
    name: "final_reranker_mmr",
    available: false,
    reason_code: "equivalent_to_baseline",
    default_selected: false,
  },
  {
    name: "final_reranker_embedding_similarity",
    available: false,
    reason_code: "missing_document_vector_access",
    default_selected: false,
  },
  { name: "graph_neighbors_off", available: true, reason_code: "available", default_selected: false },
  {
    name: "graph_neighbors_1_hop",
    available: false,
    reason_code: "equivalent_to_baseline",
    default_selected: false,
  },
  { name: "graph_neighbors_2_hops", available: true, reason_code: "available", default_selected: false },
];

const reportSummary = {
  total_cases: 20,
  k: 5,
  recall_at_k: 0.9,
  mrr: 0.74,
  ndcg_at_k: 0.78,
  p95_latency_ms: 42.6,
};

/** 返回 browser smoke 使用的评测数据集与能力描述符。 */
export function evaluationDatasetsPayload() {
  return {
    datasets: [
      {
        name: "private_basic",
        case_count: 10,
        path: "tests/fixtures/retrieval/private_basic.jsonl",
        intents: ["preference"],
        chat_types: ["private"],
      },
      {
        name: "group_context",
        case_count: 12,
        path: "tests/fixtures/retrieval/group_context.jsonl",
        intents: ["relation", "fact"],
        chat_types: ["group"],
      },
    ],
    variants,
  };
}

/** 返回 browser smoke 使用的安全评测报告列表。 */
export function evaluationReportsPayload() {
  return {
    reports: [
      {
        report_id: "eval-smoke-latest",
        created_at: 1_782_000_000,
        baseline: "baseline",
        datasets: ["private_basic"],
        summary: { ...reportSummary },
        variants: {
          baseline: {
            name: "baseline",
            status: "completed",
            summary: { ...reportSummary },
          },
        },
        deltas: {},
        cases: [],
      },
    ],
  };
}

/** 返回 browser smoke 使用的安全评测报告详情。 */
export function evaluationReportDetailPayload() {
  return {
    report: {
      report_id: "eval-smoke-latest",
      created_at: 1_782_000_000,
      baseline: "baseline",
      datasets: ["private_basic"],
      summary: { ...reportSummary },
      variants: {
        baseline: {
          name: "baseline",
          status: "completed",
          capability_status: "available",
          reason_code: "available",
          effective_settings: { variant: "baseline" },
        },
        graph_expansion_off: {
          name: "graph_expansion_off",
          status: "completed",
          capability_status: "available",
          reason_code: "available",
          effective_settings: { chain_graph_expansion_enabled: false },
        },
      },
      deltas: {},
      cases: [],
    },
  };
}
