import { useCallback, useEffect, useRef, useState } from "react";

import { apiRequest, unwrapApiData } from "@/lib/bridge";

export type RuntimeStatus =
  | "loading"
  | "ready"
  | "waiting"
  | "failed"
  | "offline"
  | "unknown";

export interface RuntimeStatusSnapshot {
  status: RuntimeStatus;
  missingProviders: string[];
  errorMessage: string | null;
}

interface RuntimeMetrics {
  provider?: {
    status?: unknown;
    missing_provider?: unknown;
    is_failed?: unknown;
    is_initialized?: unknown;
    error_message?: unknown;
  };
}

const INITIAL_STATE: RuntimeStatusSnapshot = {
  status: "loading",
  missingProviders: [],
  errorMessage: null,
};

function normalizeMissingProviders(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean)
    .slice(0, 4);
}

function normalizeErrorMessage(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const message = value.trim();
  return message ? message.slice(0, 240) : null;
}

function normalizeRuntimeMetrics(data: RuntimeMetrics): RuntimeStatusSnapshot {
  const provider = data.provider ?? {};
  const missingProviders = normalizeMissingProviders(provider.missing_provider);
  const rawStatus = typeof provider.status === "string"
    ? provider.status.trim().toLowerCase()
    : "";
  const failed = rawStatus === "failed" || provider.is_failed === true;
  const ready = rawStatus === "ready"
    || provider.is_initialized === true;

  return {
    status: failed
      ? "failed"
      : ready
        ? "ready"
        : rawStatus === "waiting" || missingProviders.length > 0
          ? "waiting"
          : "unknown",
    missingProviders,
    errorMessage: normalizeErrorMessage(provider.error_message),
  };
}

export function useRuntimeStatus(pollIntervalMs = 5_000) {
  const [state, setState] = useState<RuntimeStatusSnapshot>(INITIAL_STATE);
  const mountedRef = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const response = await apiRequest("metrics/summary", { retries: 0 });
      const data = unwrapApiData<RuntimeMetrics>(response);
      if (mountedRef.current) setState(normalizeRuntimeMetrics(data));
    } catch (error) {
      if (!mountedRef.current) return;
      setState({
        status: "offline",
        missingProviders: [],
        errorMessage: error instanceof Error ? error.message : null,
      });
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, pollIntervalMs);
    return () => {
      mountedRef.current = false;
      window.clearInterval(timer);
    };
  }, [pollIntervalMs, refresh]);

  return { ...state, refresh };
}
