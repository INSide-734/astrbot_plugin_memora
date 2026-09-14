import { useCallback, useEffect, useRef, useState } from "react";

import { apiRequest, unwrapApiData } from "@/lib/bridge";
import type {
  MemoryDedupOutcome,
  MemoryDedupSummary,
  MemoryDedupSummaryWindow,
} from "@/types/memoryDedup";

type MemoryDedupLoadStatus = "loading" | "success" | "error";

interface SummaryState {
  status: MemoryDedupLoadStatus;
  data: MemoryDedupSummary | null;
  error: string | null;
}

export const MEMORY_DEDUP_WINDOWS: MemoryDedupSummaryWindow[] = [
  "1h",
  "24h",
  "7d",
  "30d",
];

const MEMORY_DEDUP_OUTCOMES: MemoryDedupOutcome[] = [
  "checked",
  "hit",
  "merged",
  "fact_mismatch",
  "fact_overlap",
  "conflict",
  "failed",
];

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

function asCounts(source: unknown): Record<MemoryDedupOutcome, number> {
  const record =
    source && typeof source === "object"
      ? (source as Record<string, unknown>)
      : {};
  return Object.fromEntries(
    MEMORY_DEDUP_OUTCOMES.map((outcome) => [outcome, asCount(record[outcome])])
  ) as Record<MemoryDedupOutcome, number>;
}

/**
 * 把任意载荷归一化为面板契约。
 *
 * 观测端点与后端 Store 的零值契约一致：缺字段、非法数值或非对象载荷一律降级为
 * 零值，避免单个坏响应让整个页面抛错（面板只是只读观测，不应拖垮宿主页面）。
 */
export function normalizeMemoryDedupSummary(value: unknown): MemoryDedupSummary {
  const record =
    value && typeof value === "object"
      ? (value as Record<string, unknown>)
      : {};
  const windowValue = MEMORY_DEDUP_WINDOWS.includes(
    record.window as MemoryDedupSummaryWindow
  )
    ? (record.window as MemoryDedupSummaryWindow)
    : "24h";
  const byModeRecord =
    record.by_mode && typeof record.by_mode === "object"
      ? (record.by_mode as Record<string, unknown>)
      : {};
  const trendSource = Array.isArray(record.trend) ? record.trend : [];
  return {
    window: windowValue,
    ...asCounts(record),
    hit_rate: asRate(record.hit_rate),
    guard_rate: asRate(record.guard_rate),
    overlap_rate: asRate(record.overlap_rate),
    failure_rate: asRate(record.failure_rate),
    by_mode: {
      observe: asCounts(byModeRecord.observe),
      enforce: asCounts(byModeRecord.enforce),
    },
    trend: trendSource
      .filter(
        (point): point is Record<string, unknown> =>
          Boolean(point) && typeof point === "object"
      )
      .map((point) => ({
        ...asCounts(point),
        bucket_ms: asCount(point.bucket_ms),
      })),
  };
}

export function useMemoryDedupMetrics(
  initialWindow: MemoryDedupSummaryWindow = "24h",
  pollIntervalMs = 30_000
) {
  const [windowValue, setWindowValue] =
    useState<MemoryDedupSummaryWindow>(initialWindow);
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
        `memory-dedup/metrics?window=${encodeURIComponent(windowValue)}`,
        { retries: 0 }
      );
      const next = normalizeMemoryDedupSummary(
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
