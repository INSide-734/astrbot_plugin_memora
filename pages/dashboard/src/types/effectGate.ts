/** 效果门报告类型；字段与服务端 allowlist 一一对应。 */

export type EffectGateStatus =
  | "pass_enable"
  | "pass_disable"
  | "reject"
  | "insufficient_evidence"
  | "not_applicable";

export type EffectGateRecommendation =
  | "enable"
  | "keep_disabled"
  | "degrade_to_document_only"
  | "collect_more_evidence";

export type EffectGateFactorName =
  | "atom"
  | "graph_route"
  | "dynamic_weights"
  | "rerank";

export type EffectGateMetricName =
  | "final_injected_hit_rate"
  | "injection_precision"
  | "required_fact_coverage"
  | "negative_injection_rate"
  | "observed_latency_ms"
  | "observed_provider_calls";

export type EffectGateRecommendationConfig = Partial<{
  atom_route_weight: 0.25;
  graph_retrieval_enabled: false;
  dynamic_route_weighting: true;
  final_reranker: "mmr";
}>;

export interface EffectGateMetric {
  available: boolean;
  pairs: number;
  baseline?: number | null;
  variant?: number | null;
  delta?: number | null;
  ci_low?: number | null;
  ci_high?: number | null;
  margin?: number | null;
}

export interface EffectGateCategoryMetric {
  baseline?: number | null;
  variant?: number | null;
  delta?: number | null;
}

export type EffectGateCategoryMetrics = {
  case_count: number;
} & Partial<Record<EffectGateMetricName, EffectGateCategoryMetric>>;

export interface EffectGateFactor {
  factor: EffectGateFactorName | string;
  variant: string;
  status: EffectGateStatus | string;
  recommendation: EffectGateRecommendation | string;
  reason_codes: string[];
  metrics: Partial<Record<EffectGateMetricName, EffectGateMetric>>;
  categories: Partial<Record<string, EffectGateCategoryMetrics>>;
}

export interface EffectGateReadiness {
  required_categories: string[];
  present_counts: Record<string, number>;
  total_cases: number;
  missing: string[];
  can_evaluate: boolean;
}

export interface EffectGatePolicy {
  policy_version?: string;
  required_categories?: string[];
  min_cases_per_category?: number;
  min_total_cases?: number;
  confidence?: number;
  bootstrap_iterations?: number;
  rate_margin?: number;
  latency_margin_ms?: number;
  latency_margin_ratio?: number;
  provider_calls_margin?: number;
  negative_injection_margin?: number;
}

export interface EffectGateManifest {
  data_source: string;
  code_revision: string;
  config_hash: string;
  schema_hash: string;
  fixture_hmac: string;
  db_snapshot_hash: string | null;
  seed: number;
  k: number;
  case_count: number;
  injection_config_hash: string;
  gate_policy_version: string;
  gate_policy: EffectGatePolicy;
}

export interface EffectGateReport {
  schema_version: string;
  baseline: string;
  readiness: EffectGateReadiness;
  factors: Partial<Record<EffectGateFactorName, EffectGateFactor>>;
  recommended_config: EffectGateRecommendationConfig;
  policy: EffectGatePolicy;
  manifest: EffectGateManifest;
  privacy_canary: {
    checked_values: number;
    violations: string[];
  };
}

export interface EffectGateFactorSummary {
  status: EffectGateStatus | string;
  recommendation: EffectGateRecommendation | string;
  reason_codes: string[];
}

export interface EffectGateSummary {
  schema_version: string;
  baseline: string;
  readiness: EffectGateReadiness;
  factors: Partial<Record<EffectGateFactorName, EffectGateFactorSummary>>;
  recommended_config: EffectGateRecommendationConfig;
  manifest: Pick<
    EffectGateManifest,
    | "code_revision"
    | "config_hash"
    | "schema_hash"
    | "fixture_hmac"
    | "db_snapshot_hash"
    | "seed"
    | "k"
    | "gate_policy_version"
  >;
}

export interface InjectionSummary {
  final_injected_hit_rate?: number | null;
  injection_precision?: number | null;
  required_fact_coverage?: number | null;
  negative_injection_rate?: number | null;
}

export interface EvaluationCategoryBreakdown {
  case_count?: number;
  final_injected_hit_rate?: number | null;
  injection_precision?: number | null;
  required_fact_coverage?: number | null;
  negative_injection_rate?: number | null;
  observed_latency_ms?: number | null;
  observed_provider_calls?: number | null;
}
