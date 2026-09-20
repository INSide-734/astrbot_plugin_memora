import { useCallback, useEffect, useRef, useState } from "react";

import { apiRequest, unwrapApiData } from "@/lib/bridge";
import type {
  QualityFunnelStage,
  QualityFunnelStageId,
  QualityFunnelStageState,
  QualityFunnelSummary,
  QualityFunnelTrendPoint,
  QualityFunnelWindow,
} from "@/types/qualityFunnel";

type QualityFunnelLoadStatus = "loading" | "success" | "error";

interface SummaryState {
  status: QualityFunnelLoadStatus;
  data: QualityFunnelSummary | null;
  error: string | null;
}

export const QUALITY_FUNNEL_WINDOWS: QualityFunnelWindow[] = [
  "1h",
  "24h",
  "7d",
  "30d",
];

export const QUALITY_FUNNEL_STAGES: QualityFunnelStageId[] = [
  "candidates",
  "facts",
  "dedup",
  "injection",
];

const STAGE_STATES: QualityFunnelStageState[] = [
  "available",
  "degraded",
  "unavailable",
];

/** 与后端闭集一致：每个 stage 的计数、实测标量与派生比率键。 */
const STAGE_COUNT_KEYS: Record<QualityFunnelStageId, string[]> = {
  candidates: [
    "windows",
    "candidates",
    "exact_reuse",
    "duplicate_topics",
    "identity_drops",
    "budget_exceeded",
    "catalog_degraded",
  ],
  facts: [
    "windows",
    "canonical",
    "merged",
    "quarantined",
    "discarded",
    "mark_write",
    "failed",
    "skipped",
    "facts_rejected",
  ],
  dedup: [
    "checked",
    "hit",
    "merged",
    "fact_mismatch",
    "fact_overlap",
    "conflict",
    "failed",
  ],
  injection: [
    "decisions",
    "selected",
    "dropped",
    "truncated",
    "memory_present",
    "payload_injected",
  ],
};

const STAGE_VALUE_KEYS: Record<QualityFunnelStageId, string[]> = {
  candidates: [],
  facts: [],
  dedup: [],
  injection: ["budget_utilization"],
};

const STAGE_RATE_KEYS: Record<QualityFunnelStageId, string[]> = {
  candidates: ["reuse_rate", "degraded_rate"],
  facts: ["merge_rate", "discard_rate"],
  dedup: ["hit_rate", "guard_rate", "overlap_rate", "failure_rate"],
  injection: ["memory_present_rate", "payload_injected_rate"],
};

const TREND_FIELDS = [
  "candidates",
  "canonical",
  "merged",
  "facts_rejected",
  "dedup_checked",
  "dedup_hit",
  "decisions",
  "selected",
] as const;

function asCount(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? Math.floor(value)
    : 0;
}

function asRate(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : 0;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asNumbers(
  source: unknown,
  keys: readonly string[],
  normalize: (value: unknown) => number
): Record<string, number> {
  const record = asRecord(source);
  return Object.fromEntries(
    keys.map((key) => [key, normalize(record[key])])
  ) as Record<string, number>;
}

function normalizeStage(
  source: unknown,
  id: QualityFunnelStageId
): QualityFunnelStage {
  const record = asRecord(source);
  const state = STAGE_STATES.includes(record.state as QualityFunnelStageState)
    ? (record.state as QualityFunnelStageState)
    : "unavailable";
  const reason =
    typeof record.reason === "string" && record.reason ? record.reason : "read_failed";
  if (state === "unavailable") {
    return { id, state, reason, counts: null, values: null, rates: null };
  }
  return {
    id,
    state,
    reason,
    counts: asNumbers(record.counts, STAGE_COUNT_KEYS[id], asCount),
    values: asNumbers(record.values, STAGE_VALUE_KEYS[id], asRate),
    rates: asNumbers(record.rates, STAGE_RATE_KEYS[id], asRate),
  };
}

/**
 * 把任意载荷归一化为面板契约。
 *
 * 观测端点与后端 stage 契约一致：state 闭集之外的取值降级为 ``unavailable``，
 * 缺字段、非法数值或非对象载荷统一归零，避免单个坏响应让整个 Insights 页抛错。
 */
export function normalizeQualityFunnelSummary(
  value: unknown
): QualityFunnelSummary {
  const record = asRecord(value);
  const stagesSource = Array.isArray(record.stages) ? record.stages : [];
  const stages = QUALITY_FUNNEL_STAGES.flatMap((id) => {
    const source = stagesSource.find((item) => asRecord(item).id === id);
    return source === undefined ? [] : [normalizeStage(source, id)];
  });
  const trendSource = Array.isArray(record.trend) ? record.trend : [];
  return {
    window: QUALITY_FUNNEL_WINDOWS.includes(record.window as QualityFunnelWindow)
      ? (record.window as QualityFunnelWindow)
      : "24h",
    bucket: "utc_day",
    advisory: record.advisory !== false,
    stages,
    trend: trendSource
      .map((point) => {
        const pointRecord = asRecord(point);
        const day = pointRecord.day;
        return {
          day: typeof day === "string" && day.length === 10 ? day : "",
          ...(Object.fromEntries(
            TREND_FIELDS.map((field) => [field, asCount(pointRecord[field])])
          ) as Record<(typeof TREND_FIELDS)[number], number>),
        };
      })
      .filter((point): point is QualityFunnelTrendPoint => Boolean(point.day)),
  };
}

export function useQualityFunnelMetrics(
  initialWindow: QualityFunnelWindow = "24h",
  pollIntervalMs = 60_000
) {
  const [windowValue, setWindowValue] =
    useState<QualityFunnelWindow>(initialWindow);
  const [state, setState] = useState<SummaryState>({
    status: "loading",
    data: null,
    error: null,
  });
  const mountedRef = useRef(true);
  const requestGenerationRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestGenerationRef.current += 1;
    };
  }, []);

  const refresh = useCallback(async () => {
    const generation = ++requestGenerationRef.current;
    setState((previous) => ({
      ...previous,
      status: "loading",
      error: null,
    }));
    try {
      const response = await apiRequest(
        `metrics/quality-funnel?window=${encodeURIComponent(windowValue)}`,
        { retries: 0 }
      );
      const next = normalizeQualityFunnelSummary(
        unwrapApiData<unknown>(response)
      );
      if (mountedRef.current && generation === requestGenerationRef.current) {
        setState({ status: "success", data: next, error: null });
      }
    } catch (error) {
      if (mountedRef.current && generation === requestGenerationRef.current) {
        setState({
          status: "error",
          data: null,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }
  }, [windowValue]);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, pollIntervalMs);
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      requestGenerationRef.current += 1;
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [pollIntervalMs, refresh]);

  return {
    windowValue,
    setWindowValue,
    status: state.status,
    data: state.data,
    error: state.error,
    refresh,
  };
}
