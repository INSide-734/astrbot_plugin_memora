export type IntelligenceTabId =
  | "evaluation"
  | "recallTrace"
  | "diagnostics"
  | "reviewQueue"
  | "topicGovernance";

export type IntelligenceRunStatus = "idle" | "running" | "passed" | "warning" | "failed";

export interface IntelligenceEvaluationSummary {
  run_id: string;
  dataset_id: string;
  variant: string;
  status: IntelligenceRunStatus;
  recall_at_k: number;
  mrr: number;
  ndcg_at_k: number;
  p95_latency_ms: number;
  updated_at: string;
}

export interface IntelligenceRecallTraceStep {
  id: string;
  stage: "query" | "document" | "graph" | "rerank" | "personalize" | "inject";
  label: string;
  duration_ms: number;
  score?: number;
  status: IntelligenceRunStatus;
}

export interface RecallTraceStage {
  name: string;
  duration_ms: number;
  candidate_count: number;
  metadata: Record<string, unknown>;
  status?: string;
}

export interface RecallTraceScoreContribution {
  source: string;
  score: number;
  weight: number;
}

export interface RecallTraceResult {
  rank: number;
  initial_score: number;
  final_score: number;
  score_contributions: RecallTraceScoreContribution[];
  metadata: Record<string, unknown>;
}

export interface RecallTraceFilteredCandidate {
  reason: string;
  stage?: string;
  score?: number;
}

export interface RecallTraceFactAlignment {
  aligned: number;
  misaligned: number;
  undeterminable: number;
}

/** 生产快照的来源证据状态；未评估与无法判断保持可区分。 */
export type RecallTraceSourceStatus = "not_assessed" | "unknown";

/** 生产快照按 (stage, reason) 聚合的过滤计数，不含被滤候选 ID。 */
export interface RecallTraceFilterCount {
  stage: string;
  reason: string;
  count: number;
}

/** 生产快照附带的同请求注入摘要；全部来自已执行阶段。 */
export interface RecallTraceInjectionSummary {
  routing_mode?: string;
  resolved_preset?: string;
  resolved_delivery?: string;
  outcome?: string;
  candidate_count?: number;
  selected_count?: number;
  injected_count?: number;
  configured_budget_chars?: number;
  effective_budget_chars?: number;
}

export interface RecallTraceResponse {
  trace_id: string;
  total_ms: number;
  stages: RecallTraceStage[];
  results: RecallTraceResult[];
  filtered: RecallTraceFilteredCandidate[];
  created_at: number;
  metadata: Record<string, unknown>;
  filter_summary?: RecallTraceFilterCount[];
  injection?: RecallTraceInjectionSummary;
  fact_alignment?: RecallTraceFactAlignment;
  source_status?: RecallTraceSourceStatus;
}

export interface RecallTraceRequest {
  query: string;
  k: number;
  session_id?: string;
  user_id?: string;
  chat_type: string;
  chain_depth: number;
}

export interface DiagnosticHealthDomain {
  name: string;
  score?: number;
  status: string;
  message: string;
}

export interface DiagnosticHealthResponse {
  score: number;
  level: "healthy" | "watch" | "degraded" | "critical" | string;
  domains: DiagnosticHealthDomain[];
  recommended_actions: string[];
}

export interface DiagnosticEvent {
  event_id: string;
  created_at: string;
  domain: string;
  severity: string;
  title: string;
  message: string;
  source: string;
  payload: Record<string, unknown>;
  resolved_at: string | null;
}

export interface DiagnosticEventsResponse {
  events: DiagnosticEvent[];
  total: number;
}

export interface IntelligenceReviewQueueItem {
  id: string;
  queue: "evaluation" | "trace" | "diagnostics";
  title: string;
  severity: "low" | "medium" | "high";
  status: "open" | "triaged" | "deferred";
  created_at: string;
  owner?: string;
}

export type ReviewActionValue = "approve" | "edit" | "merge" | "archive" | "delete" | "mark_safe";

export interface ReviewItem {
  item_id: string;
  memory_id: string;
  reasons: string[];
  severity: "low" | "medium" | "high" | "critical" | string;
  status: "open" | "approved" | "edited" | "merged" | "archived" | "deleted" | "safe" | string;
  content_preview: string;
  metadata: Record<string, unknown>;
  created_at: number;
  updated_at: number;
}

export interface ReviewAction {
  action_id: string;
  item_id: string;
  action: string;
  actor_id: string | null;
  payload: Record<string, unknown>;
  created_at: number;
}

export interface ReviewItemsResponse {
  items: ReviewItem[];
  total: number;
}

export interface ReviewItemDetailResponse {
  item: ReviewItem;
  actions: ReviewAction[];
}

export type ReconsolidationReviewStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "failed"
  | "rolled_back"
  | string;

export type ReconsolidationReviewActionValue = "approve" | "reject" | "rollback";

export interface ReconsolidationReviewItem {
  candidate_id: string;
  status: ReconsolidationReviewStatus;
  change_summary: string;
  evidence_type: string;
  reason_code: string;
  created_at: string | number;
  updated_at: string | number;
}

export interface ReconsolidationReviewDetail extends ReconsolidationReviewItem {
  old_content: string;
  proposed_content: string;
}

export interface ReconsolidationReviewAction {
  action: string;
  reason_code: string;
  created_at: string | number;
}

export interface ReconsolidationReviewItemsResponse {
  enabled?: boolean;
  items: ReconsolidationReviewItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface ReconsolidationReviewDetailResponse {
  candidate: ReconsolidationReviewDetail;
  actions: ReconsolidationReviewAction[];
}

export interface ReconsolidationReviewActionResponse {
  candidate_id: string;
  action: ReconsolidationReviewActionValue;
  status: ReconsolidationReviewStatus;
}

export interface EvaluationDataset {
  name: string;
  case_count: number;
  path: string;
  intents: string[];
  chat_types: string[];
  source?: "current_memories" | "imported" | string;
}

export interface EvaluationVariantDescriptor {
  name: string;
  available: boolean;
  reason_code: string;
  default_selected: boolean;
}

export interface EvaluationSummaryMetrics {
  total_cases: number;
  k: number;
  recall_at_k: number;
  mrr: number;
  ndcg_at_k: number;
  observed_p95_latency_ms?: number | null;
  observed_p50_latency_ms?: number | null;
  annotated_p50_latency_ms?: number | null;
  annotated_p95_latency_ms?: number | null;
  reported_p50_latency_ms?: number | null;
  reported_p95_latency_ms?: number | null;
  annotated_answer_faithfulness?: number | null;
  annotated_answer_relevancy?: number | null;
  judged_answer_faithfulness?: number | null;
  judged_answer_relevancy?: number | null;
  reported_answer_faithfulness?: number | null;
  reported_answer_relevancy?: number | null;
  observed_provider_calls?: number | null;
  observed_token_cost?: number | null;
  annotated_provider_calls?: number | null;
  annotated_token_cost?: number | null;
  reported_provider_calls?: number | null;
  reported_token_cost?: number | null;
}

export interface EvaluationVariantPayload {
  name: string;
  status: "completed" | "skipped" | "error" | string;
  summary?: EvaluationSummaryMetrics;
  reason?: string;
  capability_status?: "available" | "unavailable" | string;
  reason_code?: string;
  effective_settings?: Record<string, string | number | boolean>;
}

export interface EvaluationVariantDelta {
  recall_at_k: number | null;
  mrr: number | null;
  ndcg_at_k: number | null;
  observed_p95_latency_ms?: number | null;
}

export interface QualityLoopManifestSummary {
  schema_version: string;
  evaluator_version: string;
  code_revision: string;
  config_hash: string;
  schema_hash: string;
  fixture_hash: string;
  manifest_hash: string;
  model_id: string | null;
  embedding_id: string | null;
  tokenizer_id: string | null;
  seed: number;
  k: number;
  db_snapshot_hash: string | null;
  pair_count: number;
}

export interface QualityLoopStage {
  stage: "write" | "source" | "recall" | "injection" | "lifecycle" | "expression" | string;
  state: "available" | "degraded" | "unavailable" | string;
  reason: string;
  owning_stage: string | null;
  metrics: Record<string, number | null>;
}

export interface QualityLoopPairsSummary {
  total_pairs: number;
  should_use_hit_rate: number | null;
  should_silence_correct_rate: number | null;
  reject_reason_counts?: Record<string, number>;
}

export interface QualityLoopPayload {
  manifest: QualityLoopManifestSummary | null;
  stages: QualityLoopStage[];
  pairs: QualityLoopPairsSummary;
  pair_outcomes?: Array<{
    context_key_hash: string;
    slot_order: string[];
    should_use_case_hash: string;
    should_silence_case_hash: string;
    should_use_hit: boolean | null;
    should_silence_correct: boolean | null;
  }>;
}

export interface EvaluationCaseResult {
  case_id: string;
  recall_at_k: number;
  precision_at_k?: number;
  reciprocal_rank: number;
  ndcg_at_k: number;
  observed_latency_ms?: number | null;
  annotated_latency_ms?: number | null;
  reported_latency_ms?: number | null;
}

export interface EvaluationReport {
  report_id: string;
  created_at: number;
  baseline: string;
  datasets: string[];
  summary: EvaluationSummaryMetrics;
  variants: Record<string, EvaluationVariantPayload>;
  deltas?: Record<string, EvaluationVariantDelta>;
  cases?: EvaluationCaseResult[];
  case_count?: number;
  quality_loop?: QualityLoopPayload | null;
}
