import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { getConfigValue } from "@/lib/config";
import type { ConfigObject } from "@/types/config";
import {
  DEFAULT_INJECTION_STRATEGY,
  INJECTION_STRATEGY_PATHS,
} from "@/types/injection";
import type {
  InjectionLoadStatus,
  InjectionPresetName,
  InjectionStrategyCatalog,
  InjectionStrategyDraft,
} from "@/types/injection";

import { useConfigSync } from "./useConfigSync";

const PRESET_RANK: Record<InjectionPresetName, number> = {
  tool_first: 0,
  low_cost: 1,
  balanced: 2,
  quality: 3,
};

const STRATEGY_PATH_SET = new Set<string>(
  Object.values(INJECTION_STRATEGY_PATHS)
);

function projectStrategy(
  config: ConfigObject | null
): InjectionStrategyDraft | null {
  if (!config) return null;
  const projected = { ...DEFAULT_INJECTION_STRATEGY };
  for (const key of Object.keys(
    INJECTION_STRATEGY_PATHS
  ) as Array<keyof InjectionStrategyDraft>) {
    const value = getConfigValue(config, INJECTION_STRATEGY_PATHS[key]);
    if (value !== undefined) projected[key] = value as never;
  }
  return projected;
}

export function validateInjectionStrategy(
  draft: InjectionStrategyDraft | null
): Partial<Record<keyof InjectionStrategyDraft, string>> {
  if (!draft) return {};
  const errors: Partial<Record<keyof InjectionStrategyDraft, string>> = {};
  if (
    PRESET_RANK[draft.hybridMinPreset] >
      PRESET_RANK[draft.hybridBasePreset] ||
    PRESET_RANK[draft.hybridBasePreset] >
      PRESET_RANK[draft.hybridMaxPreset]
  ) {
    errors.hybridMinPreset = "injection.validation.hybridOrder";
    errors.hybridBasePreset = "injection.validation.hybridOrder";
    errors.hybridMaxPreset = "injection.validation.hybridOrder";
  }
  if (![0, 7, 30, 90, 180].includes(draft.retentionDays)) {
    errors.retentionDays = "injection.validation.retention";
  }
  if (
    !Number.isInteger(draft.maxRows) ||
    draft.maxRows < 1_000 ||
    draft.maxRows > 1_000_000
  ) {
    errors.maxRows = "injection.validation.maxRows";
  }
  for (const [key, max] of [
    ["budgetChars", 10_000],
    ["memoryMaxChars", 2_000],
    ["metadataMaxChars", 500],
  ] as const) {
    const value = draft[key];
    if (!Number.isInteger(value) || value < 0 || value > max) {
      errors[key] = "injection.validation.budget";
    }
  }
  return errors;
}

interface CatalogState {
  status: InjectionLoadStatus;
  data: InjectionStrategyCatalog | null;
  error: string | null;
}

export function useInjectionStrategyConfig() {
  const sync = useConfigSync();
  const [catalogState, setCatalogState] = useState<CatalogState>({
    status: "loading",
    data: null,
    error: null,
  });
  const mountedRef = useRef(true);
  const catalogGenerationRef = useRef(0);
  const catalogRevisionRef = useRef<string | null>(null);

  const loadCatalog = useCallback(async () => {
    const generation = ++catalogGenerationRef.current;
    setCatalogState((previous) => ({
      ...previous,
      status: "loading",
      error: null,
    }));
    try {
      const response = await apiRequest("injection-strategy/catalog", {
        retries: 0,
      });
      const data = unwrapApiData<InjectionStrategyCatalog>(response);
      if (
        mountedRef.current &&
        generation === catalogGenerationRef.current
      ) {
        setCatalogState({ status: "success", data, error: null });
      }
    } catch (error) {
      if (
        mountedRef.current &&
        generation === catalogGenerationRef.current
      ) {
        setCatalogState({
          status: "error",
          data: null,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void loadCatalog();
    return () => {
      mountedRef.current = false;
      catalogGenerationRef.current += 1;
    };
  }, [loadCatalog]);

  useEffect(() => {
    if (!sync.revision) return;
    const previousRevision = catalogRevisionRef.current;
    catalogRevisionRef.current = sync.revision;
    if (previousRevision !== null && previousRevision !== sync.revision) {
      void loadCatalog();
    }
  }, [loadCatalog, sync.revision]);

  const draft = useMemo(() => projectStrategy(sync.draft), [sync.draft]);
  const base = useMemo(() => projectStrategy(sync.baseConfig), [sync.baseConfig]);
  const errors = useMemo(() => validateInjectionStrategy(draft), [draft]);
  const dirtyPaths = useMemo(
    () => sync.dirtyPaths.filter((path) => STRATEGY_PATH_SET.has(path)),
    [sync.dirtyPaths]
  );
  const remotePaths = useMemo(
    () => sync.remotePaths.filter((path) => STRATEGY_PATH_SET.has(path)),
    [sync.remotePaths]
  );
  const overlapPaths = useMemo(
    () => sync.overlapPaths.filter((path) => STRATEGY_PATH_SET.has(path)),
    [sync.overlapPaths]
  );
  const serverFieldErrors = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(sync.fieldErrors).filter(([path]) =>
          STRATEGY_PATH_SET.has(path)
        )
      ),
    [sync.fieldErrors]
  );
  const dirty = dirtyPaths.length > 0;
  const canSave =
    dirty &&
    Object.keys(errors).length === 0 &&
    sync.status !== "loading" &&
    sync.status !== "applying" &&
    sync.status !== "reloading" &&
    sync.status !== "conflict";

  const change = useCallback(
    <K extends keyof InjectionStrategyDraft>(
      key: K,
      value: InjectionStrategyDraft[K]
    ) => {
      sync.changeField(INJECTION_STRATEGY_PATHS[key], value);
    },
    [sync.changeField]
  );

  const restoreDefaults = useCallback(() => {
    for (const key of Object.keys(
      INJECTION_STRATEGY_PATHS
    ) as Array<keyof InjectionStrategyDraft>) {
      sync.changeField(
        INJECTION_STRATEGY_PATHS[key],
        DEFAULT_INJECTION_STRATEGY[key]
      );
    }
  }, [sync.changeField]);

  const save = useCallback(async () => {
    if (Object.keys(errors).length > 0) return;
    await sync.apply();
  }, [errors, sync.apply]);

  return {
    catalog: catalogState.data,
    catalogStatus: catalogState.status,
    catalogError: catalogState.error,
    retryCatalog: loadCatalog,
    draft,
    base,
    errors,
    serverFieldErrors,
    status: sync.status,
    revision: sync.revision,
    dirty,
    dirtyPaths,
    canSave,
    change,
    restoreDefaults,
    discard: sync.discardLocal,
    save,
    acceptRemote: sync.acceptRemote,
    rebaseRemote: sync.rebaseRemote,
    refresh: sync.refresh,
    localPaths: dirtyPaths,
    remotePaths,
    overlapPaths,
    remoteReady: sync.remoteConfig !== null,
  };
}
