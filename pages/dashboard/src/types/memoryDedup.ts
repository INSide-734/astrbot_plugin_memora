/** 跨窗口去重观测面的窗口摘要契约（与 Page API 白名单一致）。 */

export type MemoryDedupSummaryWindow = "1h" | "24h" | "7d" | "30d";
export type MemoryDedupMode = "observe" | "enforce";
export type MemoryDedupOutcome =
  | "checked"
  | "hit"
  | "merged"
  | "fact_mismatch"
  | "conflict"
  | "failed";

export type MemoryDedupCounts = Record<MemoryDedupOutcome, number>;

export interface MemoryDedupTrendPoint extends MemoryDedupCounts {
  bucket_ms: number;
}

export interface MemoryDedupSummary extends MemoryDedupCounts {
  window: MemoryDedupSummaryWindow;
  hit_rate: number;
  guard_rate: number;
  failure_rate: number;
  by_mode: Record<MemoryDedupMode, MemoryDedupCounts>;
  trend: MemoryDedupTrendPoint[];
}
