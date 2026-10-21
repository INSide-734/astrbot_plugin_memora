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

const categories = [
  "positive",
  "negative",
  "ambiguous",
  "cross_scope",
  "relation",
  "multi_hop",
  "long_payload",
];
const hash = "a".repeat(64);
const effectGate = {
  schema_version: "effect-gate-v1",
  baseline: "fixed_route_baseline",
  readiness: {
    required_categories: categories,
    present_counts: Object.fromEntries(categories.map((name) => [name, 10])),
    total_cases: 70,
    missing: [],
    can_evaluate: true,
  },
  factors: Object.fromEntries([
    ["atom", "atom_signal_on", "pass_enable", "enable", ["pareto_improvement"]],
    ["graph_route", "graph_route_off", "pass_disable", "degrade_to_document_only", ["graph_removal_non_inferior"]],
    ["dynamic_weights", "dynamic_route_weighting_on", "reject", "keep_disabled", ["guardrail_regression"]],
    ["rerank", "final_reranker_mmr", "insufficient_evidence", "collect_more_evidence", ["missing_primary_metric"]],
  ].map(([factor, variant, status, recommendation, reason_codes]) => [
    factor,
    { factor, variant, status, recommendation, reason_codes, metrics: {}, categories: {} },
  ])),
  recommended_config: { atom_route_weight: 0.25 },
  policy: {
    policy_version: "effect-gate-v1",
    required_categories: categories,
    min_cases_per_category: 10,
    min_total_cases: 60,
  },
  manifest: {
    data_source: "plugin_evaluation_datasets",
    code_revision: hash,
    config_hash: hash,
    schema_hash: hash,
    fixture_hmac: hash,
    db_snapshot_hash: hash,
    seed: 0,
    k: 5,
    case_count: 70,
    injection_config_hash: hash,
    gate_policy_version: "effect-gate-v1",
    gate_policy: { policy_version: "effect-gate-v1", required_categories: categories },
  },
  privacy_canary: { checked_values: 140, violations: [] },
};

/** 返回 browser smoke 使用的评测数据集与能力描述符。 */
export function evaluationDatasetsPayload() {
  return {
    datasets: [
      {
        name: "current_memories",
        case_count: 20,
        path: "",
        intents: ["self_retrieval"],
        chat_types: ["private", "group"],
        source: "current_memories",
      },
      {
        name: "private_basic",
        case_count: 10,
        path: "private_basic.jsonl",
        intents: ["preference"],
        chat_types: ["private"],
      },
      {
        name: "group_context",
        case_count: 12,
        path: "group_context.jsonl",
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
      effect_gate: effectGate,
      manifest: effectGate.manifest,
    },
  };
}
