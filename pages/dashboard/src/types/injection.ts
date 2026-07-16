export type InjectionRoutingMode = "manual" | "auto" | "hybrid";
export type InjectionPresetName =
  | "tool_first"
  | "low_cost"
  | "balanced"
  | "quality";
export type InjectionContentLevel = "NONE" | "FACTS" | "COMPACT" | "DETAILED";
export type InjectionDeliveryMode =
  | "auto"
  | "extra_user_content"
  | "user_message_before"
  | "user_message_after"
  | "fake_tool_call"
  | "fake_tool_call_deepseek_v4";
export type InjectionOutcome =
  | "injected"
  | "skipped"
  | "empty"
  | "fallback"
  | "error";
export type InjectionSummaryWindow = "1h" | "24h" | "7d" | "30d";
export type InjectionWorkbenchTab = "overview" | "config" | "decisions";
export type InjectionLoadStatus = "loading" | "success" | "error";
export type InjectionDetailStatus = "idle" | InjectionLoadStatus;

export interface InjectionPresetContract {
  name: InjectionPresetName;
  rank: number;
  auto_inject: boolean;
  memory_budget_chars: number;
  max_memories: number;
  content_level: InjectionContentLevel;
  cost_penalty_weight: number;
  minimum_utility: number;
  allow_tool_fallback: boolean;
  preferred_delivery: InjectionDeliveryMode;
}

export interface InjectionStrategyCatalog {
  routing_modes: InjectionRoutingMode[];
  presets: InjectionPresetContract[];
  deliveries: InjectionDeliveryMode[];
  retention_options: number[];
  provider_tools_supported: boolean;
  memory_tool_available: boolean;
  recall_trace_available: boolean;
  effective_default_delivery: Exclude<InjectionDeliveryMode, "auto">;
}

export interface InjectionRecentEvent {
  decision_id: string;
  created_at_ms: number;
  trace_id: string | null;
  routing_mode: InjectionRoutingMode;
  resolved_preset: InjectionPresetName;
  outcome: InjectionOutcome;
  primary_reason: string;
  fallback_applied: boolean;
  actual_payload_chars: number;
}

export interface InjectionCostPoint {
  bucket_ms: number;
  decision_count: number;
  payload_chars_p95: number;
  provider_fallback_rate: number;
}

export interface InjectionStrategySummary {
  window: InjectionSummaryWindow;
  decision_count: number;
  payload_chars_p95: number;
  provider_fallback_rate: number;
  preset_distribution: Partial<Record<InjectionPresetName, number>>;
  cost_trend: InjectionCostPoint[];
  recent_events: InjectionRecentEvent[];
}

export interface InjectionDecisionListItem extends InjectionRecentEvent {
  configured_preset: InjectionPresetName;
  recommended_preset: InjectionPresetName;
  preferred_delivery: InjectionDeliveryMode;
  resolved_delivery: Exclude<InjectionDeliveryMode, "auto">;
  provider_type: string;
  provider_model: string;
  error_code: string | null;
  candidate_count: number;
  selected_count: number;
  dropped_count: number;
  truncated_count: number;
  configured_budget_chars: number;
  effective_budget_chars: number;
  context_headroom_chars: number;
  decision_ms: number;
  format_ms: number;
  inject_ms: number;
}

export interface InjectionDecisionDetail extends InjectionDecisionListItem {
  reason_codes: string[];
}

export interface InjectionDecisionPage {
  items: InjectionDecisionListItem[];
  total: number;
  offset: number;
  limit: number;
}

export interface InjectionDecisionFilters {
  fromMs: number | null;
  toMs: number | null;
  routingMode: InjectionRoutingMode | "";
  resolvedPreset: InjectionPresetName | "";
  providerType: string;
  primaryReason: string;
  fallbackApplied: "" | "true" | "false";
  outcome: InjectionOutcome | "";
}

export interface InjectionStrategyDraft {
  routingMode: InjectionRoutingMode;
  manualPreset: InjectionPresetName;
  autoFallbackPreset: InjectionPresetName;
  hybridBasePreset: InjectionPresetName;
  hybridMinPreset: InjectionPresetName;
  hybridMaxPreset: InjectionPresetName;
  deliveryOverride: InjectionDeliveryMode;
  overridesEnabled: boolean;
  budgetChars: number;
  memoryMaxChars: number;
  metadataMaxChars: number;
  includeKeyFacts: boolean;
  includeTopics: boolean;
  includeParticipants: boolean;
  compactHeader: boolean;
  retentionDays: 0 | 7 | 30 | 90 | 180;
  maxRows: number;
}

export const INJECTION_STRATEGY_PATHS = {
  routingMode: "recall_engine.injection_routing_mode",
  manualPreset: "recall_engine.injection_manual_preset",
  autoFallbackPreset: "recall_engine.injection_auto_fallback_preset",
  hybridBasePreset: "recall_engine.injection_hybrid_base_preset",
  hybridMinPreset: "recall_engine.injection_hybrid_min_preset",
  hybridMaxPreset: "recall_engine.injection_hybrid_max_preset",
  deliveryOverride: "recall_engine.injection_delivery_override",
  overridesEnabled: "recall_engine.injection_preset_overrides_enabled",
  budgetChars: "recall_engine.injection_budget_chars",
  memoryMaxChars: "recall_engine.injection_memory_max_chars",
  metadataMaxChars: "recall_engine.injection_metadata_max_chars",
  includeKeyFacts: "recall_engine.injection_include_key_facts",
  includeTopics: "recall_engine.injection_include_topics",
  includeParticipants: "recall_engine.injection_include_participants",
  compactHeader: "recall_engine.injection_compact_header",
  retentionDays: "recall_engine.injection_decision_retention_days",
  maxRows: "recall_engine.injection_decision_max_rows",
} as const satisfies Record<keyof InjectionStrategyDraft, string>;

export const DEFAULT_INJECTION_STRATEGY: InjectionStrategyDraft = {
  routingMode: "manual",
  manualPreset: "balanced",
  autoFallbackPreset: "balanced",
  hybridBasePreset: "balanced",
  hybridMinPreset: "low_cost",
  hybridMaxPreset: "quality",
  deliveryOverride: "auto",
  overridesEnabled: false,
  budgetChars: 0,
  memoryMaxChars: 0,
  metadataMaxChars: 0,
  includeKeyFacts: true,
  includeTopics: true,
  includeParticipants: false,
  compactHeader: true,
  retentionDays: 30,
  maxRows: 100_000,
};

export const DEFAULT_INJECTION_FILTERS: InjectionDecisionFilters = {
  fromMs: null,
  toMs: null,
  routingMode: "",
  resolvedPreset: "",
  providerType: "",
  primaryReason: "",
  fallbackApplied: "",
  outcome: "",
};
