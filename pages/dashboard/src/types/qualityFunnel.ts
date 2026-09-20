/** 质量 funnel 观测面的窗口摘要契约（与 Page API 白名单一致）。 */

export type QualityFunnelWindow = "1h" | "24h" | "7d" | "30d";
export type QualityFunnelStageId = "candidates" | "facts" | "dedup" | "injection";
export type QualityFunnelStageState = "available" | "degraded" | "unavailable";

/** stage 状态桶：计数、实测标量与派生比率；不可用时全部为 null。 */
export interface QualityFunnelStage {
  id: QualityFunnelStageId;
  state: QualityFunnelStageState;
  reason: string;
  counts: Record<string, number> | null;
  values: Record<string, number> | null;
  rates: Record<string, number> | null;
}

export interface QualityFunnelTrendPoint {
  day: string;
  candidates: number;
  canonical: number;
  merged: number;
  facts_rejected: number;
  dedup_checked: number;
  dedup_hit: number;
  decisions: number;
  selected: number;
}

export interface QualityFunnelSummary {
  window: QualityFunnelWindow;
  bucket: "utc_day";
  advisory: boolean;
  stages: QualityFunnelStage[];
  trend: QualityFunnelTrendPoint[];
}
