import { useCallback, useEffect, useRef, useState } from "react";

import { apiRequest, unwrapApiData } from "@/lib/bridge";
import {
  DEFAULT_INJECTION_FILTERS,
  type InjectionDecisionDetail,
  type InjectionDecisionFilters,
  type InjectionDecisionPage,
  type InjectionDetailStatus,
  type InjectionLoadStatus,
} from "@/types/injection";

const FILTER_PARAM = {
  fromMs: "from_ms",
  toMs: "to_ms",
  routingMode: "routing_mode",
  resolvedPreset: "resolved_preset",
  providerType: "provider_type",
  primaryReason: "primary_reason",
  fallbackApplied: "fallback_applied",
  outcome: "outcome",
} as const satisfies Record<keyof InjectionDecisionFilters, string>;

interface ListState {
  status: InjectionLoadStatus;
  page: InjectionDecisionPage | null;
  error: string | null;
}

interface DetailState {
  status: InjectionDetailStatus;
  data: InjectionDecisionDetail | null;
  error: string | null;
}

interface UseInjectionDecisionsOptions {
  initialLimit?: number;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function clampLimit(value: number): number {
  if (!Number.isFinite(value)) return 25;
  return Math.min(100, Math.max(1, Math.trunc(value)));
}

function clampOffset(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.trunc(value));
}

function decisionQuery(
  filters: InjectionDecisionFilters,
  offset: number,
  limit: number
): string {
  const params = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  for (const key of Object.keys(
    FILTER_PARAM
  ) as Array<keyof InjectionDecisionFilters>) {
    const value = filters[key];
    if (value !== "" && value !== null) {
      params.set(FILTER_PARAM[key], String(value));
    }
  }
  return params.toString();
}

export function useInjectionDecisions(
  options: UseInjectionDecisionsOptions = {}
) {
  const [filters, setFiltersState] = useState<InjectionDecisionFilters>({
    ...DEFAULT_INJECTION_FILTERS,
  });
  const [offset, setOffsetState] = useState(0);
  const [limit, setLimitState] = useState(() =>
    clampLimit(options.initialLimit ?? 25)
  );
  const [listState, setListState] = useState<ListState>({
    status: "loading",
    page: null,
    error: null,
  });
  const [detailState, setDetailState] = useState<DetailState>({
    status: "idle",
    data: null,
    error: null,
  });
  const mountedRef = useRef(true);
  const listGenerationRef = useRef(0);
  const detailGenerationRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      listGenerationRef.current += 1;
      detailGenerationRef.current += 1;
    };
  }, []);

  const refresh = useCallback(async () => {
    const generation = ++listGenerationRef.current;
    setListState((previous) => ({
      ...previous,
      status: "loading",
      error: null,
    }));
    try {
      const response = await apiRequest(
        `injection-strategy/decisions?${decisionQuery(
          filters,
          offset,
          limit
        )}`,
        { retries: 0 }
      );
      const page = unwrapApiData<InjectionDecisionPage>(response);
      if (mountedRef.current && generation === listGenerationRef.current) {
        setListState({ status: "success", page, error: null });
      }
    } catch (error) {
      if (mountedRef.current && generation === listGenerationRef.current) {
        setListState((previous) => ({
          ...previous,
          status: "error",
          error: errorMessage(error),
        }));
      }
    }
  }, [filters, limit, offset]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const setFilter = useCallback(
    <K extends keyof InjectionDecisionFilters>(
      key: K,
      value: InjectionDecisionFilters[K]
    ) => {
      setFiltersState((previous) => ({ ...previous, [key]: value }));
      setOffsetState(0);
    },
    []
  );

  const setFilters = useCallback((next: InjectionDecisionFilters) => {
    setFiltersState({ ...next });
    setOffsetState(0);
  }, []);

  const setOffset = useCallback((next: number) => {
    setOffsetState(clampOffset(next));
  }, []);

  const setLimit = useCallback((next: number) => {
    setLimitState(clampLimit(next));
    setOffsetState(0);
  }, []);

  const loadDetail = useCallback(async (decisionId: string) => {
    const generation = ++detailGenerationRef.current;
    setDetailState({ status: "loading", data: null, error: null });
    try {
      const response = await apiRequest(
        `injection-strategy/decisions/detail?decision_id=${encodeURIComponent(
          decisionId
        )}`,
        { retries: 0 }
      );
      const data = unwrapApiData<InjectionDecisionDetail>(response);
      if (mountedRef.current && generation === detailGenerationRef.current) {
        setDetailState({ status: "success", data, error: null });
      }
    } catch (error) {
      if (mountedRef.current && generation === detailGenerationRef.current) {
        setDetailState({
          status: "error",
          data: null,
          error: errorMessage(error),
        });
      }
    }
  }, []);

  const clearDetail = useCallback(() => {
    detailGenerationRef.current += 1;
    setDetailState({ status: "idle", data: null, error: null });
  }, []);

  return {
    filters,
    page: listState.page,
    offset,
    limit,
    status: listState.status,
    error: listState.error,
    setFilter,
    setFilters,
    setOffset,
    setLimit,
    refresh,
    detailStatus: detailState.status,
    detail: detailState.data,
    detailError: detailState.error,
    loadDetail,
    clearDetail,
  };
}
