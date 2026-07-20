export type IntelligenceTabId =
  | "evaluation"
  | "recallTrace"
  | "diagnostics"
  | "reviewQueue";

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

export interface RecallTraceResponse {
  trace_id: string;
  total_ms: number;
  stages: RecallTraceStage[];
  results: RecallTraceResult[];
  filtered: RecallTraceFilteredCandidate[];
  created_at: number;
  metadata: Record<string, unknown>;
}

export interface RecallTraceRequest {
  query: string;
  k: number;
  session_id: string;
  user_id: string;
  chat_type: string;
  chain_depth: number;
}

export interface DiagnosticHealthDomain {
  name: string;
  score: number;
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

export interface EvaluationDataset {
  name: string;
  case_count: number;
  path: string;
  intents: string[];
  chat_types: string[];
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
  p95_latency_ms: number;
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
  p95_latency_ms: number | null;
}

export interface EvaluationCaseResult {
  case_id: string;
  recall_at_k: number;
  precision_at_k?: number;
  reciprocal_rank: number;
  ndcg_at_k: number;
  latency_ms: number;
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
}
