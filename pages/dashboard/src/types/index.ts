export type PageId =
  | "graph" | "memory" | "recall" | "system" | "config"
  | "profiles" | "knowledge" | "notes" | "learning"
  | "preview" | "timeline"
  | "jargon" | "affection" | "social" | "intelligence";

export * from "./intelligence";
export * from "./navigation";
export * from "./editing";

export interface MemoryItem {
  id: string;
  content?: string;
  text?: string;
  summary?: string;
  type?: string;
  importance?: number;
  status?: string;
  created_at?: string;
  updated_at?: string;
  session_id?: string;
  [key: string]: unknown;
}

export interface GraphNode {
  id: string;
  label?: string;
  type?: string;
  weight?: number;
  memory_count?: number;
  degree?: number;
  entry_count?: number;
}

export interface GraphEdge {
  source: string;
  target: string;
  type?: string;
  weight?: number;
}

export interface PageActions {
  init?: () => void;
  fetch?: () => void;
}

export interface RecallResult {
  id: string;
  content?: string;
  text?: string;
  summary?: string;
  score?: number;
  doc_kw_score?: number;
  doc_vec_score?: number;
  graph_kw_score?: number;
  graph_vec_score?: number;
  importance?: number;
  type?: string;
  created_at?: string;
  [key: string]: unknown;
}

// ---- v1.0.0+ new subsystem types ----

export interface JargonCandidate {
  term: string;
  group_id: string;
  score: number;
  frequency: number;
  unique_users: number;
  idf_score: number;
  burst_score: number;
  concentration_score: number;
  first_seen: number;
  context_examples: string[];
}

export interface JargonMeaning {
  term: string;
  group_id: string;
  meaning: string;
  confidence: number;
  is_jargon: boolean;
  is_confirmed: boolean;
  is_global: boolean;
  is_complete: boolean;
  count: number;
  last_inference_count: number;
  created_at: number;
  updated_at: number;
}

export interface JargonStats {
  group_id: string;
  total_terms: number;
  candidate_count: number;
  top_candidates: JargonCandidate[];
  store_total?: number;
  store_confirmed?: number;
}

export interface AffectionUserEntry {
  user_id: string;
  group_id: string;
  affection_score: number;
  affection_level: string;
  level_name: string;
  interaction_count: number;
  last_interaction: number;
}

export interface BotMoodStatus {
  mood_type: string;
  intensity: number;
  description: string;
  is_active: boolean;
}

export interface AffectionStatus {
  group_id: string;
  total_affection: number;
  max_total_affection: number;
  user_count: number;
  top_users: AffectionUserEntry[];
  current_mood: BotMoodStatus;
}

export interface SocialRelationEntry {
  from_user: string;
  to_user: string;
  relation_type: string;
  strength: number;
  frequency: number;
  last_interaction: number;
  group_id: string;
  tags: string[];
  category: string;
}

export interface QualityScoreEntry {
  atom_id: number;
  overall: number;
  consistency: number;
  coherence: number;
  relevance: number;
  freshness: number;
  accuracy: number;
  timestamp: number;
}

export interface QualityAlertEntry {
  id: number;
  level: string;
  dimension: string;
  score: number;
  threshold: number;
  message: string;
  suggestion: string;
  timestamp: number;
}

export interface QualityStats {
  avg_overall: number;
  avg_consistency: number;
  avg_coherence: number;
  avg_relevance: number;
  avg_freshness: number;
  avg_accuracy: number;
  total_scored: number;
  paused: boolean;
  pause_reason: string;
  alert_counts: Record<string, number>;
}

export interface DelegationStatus {
  self_learning_active: boolean;
  self_learning_label: string;
  chatplus_active: boolean;
  chatplus_label: string;
  delegated_jargon: boolean;
  delegated_expression: boolean;
  delegated_affection: boolean;
  delegated_reply: boolean;
}
