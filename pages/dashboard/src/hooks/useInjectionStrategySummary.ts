import { useCallback, useEffect, useRef, useState } from "react";

import { apiRequest, unwrapApiData } from "@/lib/bridge";
import type {
  InjectionLoadStatus,
  InjectionStrategySummary,
  InjectionSummaryWindow,
} from "@/types/injection";

interface SummaryState {
  status: InjectionLoadStatus;
  data: InjectionStrategySummary | null;
  error: string | null;
}

export function useInjectionStrategySummary(
  initialWindow: InjectionSummaryWindow = "24h",
  pollIntervalMs = 30_000
) {
  const [windowValue, setWindowValue] =
    useState<InjectionSummaryWindow>(initialWindow);
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
        `injection-strategy/summary?window=${encodeURIComponent(windowValue)}`,
        { retries: 0 }
      );
      const next = unwrapApiData<InjectionStrategySummary>(response);
      if (
        mountedRef.current &&
        generation === requestGenerationRef.current
      ) {
        setState({ status: "success", data: next, error: null });
      }
    } catch (error) {
      if (
        mountedRef.current &&
        generation === requestGenerationRef.current
      ) {
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
