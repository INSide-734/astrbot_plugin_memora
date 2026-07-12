import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { apiRequest } from "@/lib/bridge";
import {
  buildConfigChanges,
  cloneConfig,
  configValueEquals,
  diffConfigLeafPaths,
  getConfigValue,
  rebaseConfig,
  setConfigValue,
} from "@/lib/config";
import type {
  ConfigApiResponse,
  ConfigApiError,
  ConfigApplyData,
  ConfigObject,
  ConfigRemoteSnapshot,
  ConfigSchemaData,
  ConfigStateData,
  ConfigSyncError,
  ConfigSyncOptions,
  ConfigSyncStatus,
  ConfigValue,
} from "@/types/config";

export interface ConfigSyncResult {
  schemaData: ConfigSchemaData | null;
  baseConfig: ConfigObject | null;
  draft: ConfigObject | null;
  revision: string | null;
  instanceId: string | null;
  remoteConfig: ConfigObject | null;
  remoteRevision: string | null;
  remoteInstanceId: string | null;
  dirtyPaths: string[];
  localPaths: string[];
  remotePaths: string[];
  overlapPaths: string[];
  fieldErrors: Record<string, string>;
  status: ConfigSyncStatus;
  error: ConfigSyncError | null;
  changeField: (path: string, value: ConfigValue) => void;
  refresh: () => Promise<void>;
  apply: () => Promise<void>;
  acceptRemote: () => void;
  rebaseRemote: () => void;
}

interface SyncState {
  schemaData: ConfigSchemaData | null;
  baseConfig: ConfigObject | null;
  draft: ConfigObject | null;
  revision: string | null;
  instanceId: string | null;
  remote: ConfigRemoteSnapshot | null;
  remoteRevisionHint: string | null;
  fieldErrors: Record<string, string>;
  status: ConfigSyncStatus;
  error: ConfigSyncError | null;
}

const INITIAL_STATE: SyncState = {
  schemaData: null,
  baseConfig: null,
  draft: null,
  revision: null,
  instanceId: null,
  remote: null,
  remoteRevisionHint: null,
  fieldErrors: {},
  status: "loading",
  error: null,
};

class ConfigProtocolError extends Error {
  constructor(readonly response: ConfigApiError) {
    super(response.message);
  }
}

function successData<T>(response: ApiResponse): T {
  const configResponse = response as ConfigApiResponse<T>;
  if (configResponse.status === "error") {
    throw new ConfigProtocolError(configResponse);
  }
  if (configResponse.status !== "ok" || !("data" in configResponse)) {
    throw new ConfigProtocolError({
      status: "error",
      code: "invalid_request",
      message: "Unexpected configuration response",
    });
  }
  return configResponse.data;
}

function syncError(error: unknown): ConfigSyncError {
  if (error instanceof ConfigProtocolError) {
    return {
      kind: "protocol",
      code: error.response.code,
      message: error.response.message,
    };
  }
  return {
    kind: "transport",
    message: error instanceof Error ? error.message : String(error),
  };
}

export function useConfigSync(options: ConfigSyncOptions = {}): ConfigSyncResult {
  const pollIntervalMs = options.pollIntervalMs ?? 5_000;
  const reloadTimeoutMs = options.reloadTimeoutMs ?? 30_000;
  const [state, setState] = useState<SyncState>(INITIAL_STATE);
  const stateRef = useRef(state);
  const mountedRef = useRef(true);
  stateRef.current = state;

  useEffect(() => {
    let active = true;
    mountedRef.current = true;

    const load = async () => {
      try {
        const [schemaResponse, stateResponse] = await Promise.all([
          apiRequest("config/schema", { retries: 0 }),
          apiRequest("config/state", { retries: 0 }),
        ]);
        const schemaData = successData<ConfigSchemaData>(schemaResponse);
        const stateData = successData<ConfigStateData>(stateResponse);
        if (!stateData.changed || !active) return;

        setState({
          schemaData,
          baseConfig: cloneConfig(stateData.config),
          draft: cloneConfig(stateData.config),
          revision: stateData.revision,
          instanceId: stateData.instance_id,
          remote: null,
          remoteRevisionHint: null,
          fieldErrors: {},
          status: "synced",
          error: null,
        });
      } catch (error) {
        if (!active) return;
        setState((previous) => ({
          ...previous,
          status: error instanceof ConfigProtocolError ? "error" : "offline",
          error: syncError(error),
        }));
      }
    };

    void load();
    return () => {
      active = false;
      mountedRef.current = false;
    };
  }, []);

  const changeField = useCallback((path: string, value: ConfigValue) => {
    setState((previous) => {
      if (!previous.baseConfig || !previous.draft) return previous;
      const draft = setConfigValue(previous.draft, path, value);
      const dirtyPaths = diffConfigLeafPaths(previous.baseConfig, draft);
      const fieldErrors = { ...previous.fieldErrors };
      delete fieldErrors[path];
      return {
        ...previous,
        draft,
        status: previous.remote
          ? "conflict"
          : dirtyPaths.length > 0
            ? "dirty"
            : "synced",
        error: null,
        fieldErrors,
      };
    });
  }, []);

  const dirtyPaths = useMemo(
    () =>
      state.baseConfig && state.draft
        ? diffConfigLeafPaths(state.baseConfig, state.draft)
        : [],
    [state.baseConfig, state.draft]
  );

  const refresh = useCallback(async () => {
    const current = stateRef.current;
    if (!current.revision) return;

    try {
      const response = await apiRequest(
        `config/state?revision=${encodeURIComponent(current.revision)}`,
        { retries: 0 }
      );
      const stateData = successData<ConfigStateData>(response);
      if (!mountedRef.current) return;

      setState((previous) => {
        const localPaths =
          previous.baseConfig && previous.draft
            ? diffConfigLeafPaths(previous.baseConfig, previous.draft)
            : [];

        if (!stateData.changed) {
          return {
            ...previous,
            instanceId: stateData.instance_id,
            status: previous.remote
              ? "conflict"
              : localPaths.length > 0
                ? "dirty"
                : "synced",
            error: null,
          };
        }
        if (localPaths.length > 0) {
          return {
            ...previous,
            remote: {
              config: cloneConfig(stateData.config),
              revision: stateData.revision,
              instanceId: stateData.instance_id,
            },
            status: "conflict",
            error: null,
          };
        }

        return {
          ...previous,
          baseConfig: cloneConfig(stateData.config),
          draft: cloneConfig(stateData.config),
          revision: stateData.revision,
          instanceId: stateData.instance_id,
          remote: null,
          remoteRevisionHint: null,
          status: "synced",
          error: null,
        };
      });
    } catch (error) {
      if (!mountedRef.current) return;
      setState((previous) => ({
        ...previous,
        status: error instanceof ConfigProtocolError ? "error" : "offline",
        error: syncError(error),
      }));
    }
  }, []);

  const acceptRemote = useCallback(() => {
    setState((previous) => {
      if (!previous.remote) return previous;
      return {
        ...previous,
        baseConfig: cloneConfig(previous.remote.config),
        draft: cloneConfig(previous.remote.config),
        revision: previous.remote.revision,
        instanceId: previous.remote.instanceId,
        remote: null,
        remoteRevisionHint: null,
        fieldErrors: {},
        status: "synced",
        error: null,
      };
    });
  }, []);

  const rebaseRemote = useCallback(() => {
    setState((previous) => {
      if (!previous.remote || !previous.baseConfig || !previous.draft) {
        return previous;
      }
      const localPaths = diffConfigLeafPaths(
        previous.baseConfig,
        previous.draft
      );
      const baseConfig = cloneConfig(previous.remote.config);
      const draft = rebaseConfig(baseConfig, previous.draft, localPaths);
      return {
        ...previous,
        baseConfig,
        draft,
        revision: previous.remote.revision,
        instanceId: previous.remote.instanceId,
        remote: null,
        remoteRevisionHint: null,
        fieldErrors: {},
        status:
          diffConfigLeafPaths(baseConfig, draft).length > 0 ? "dirty" : "synced",
        error: null,
      };
    });
  }, []);

  const apply = useCallback(async () => {
    const current = stateRef.current;
    if (!current.baseConfig || !current.draft || !current.revision) return;
    const paths = diffConfigLeafPaths(current.baseConfig, current.draft);
    if (paths.length === 0) return;

    const savedDraft = cloneConfig(current.draft);
    setState((previous) => ({
      ...previous,
      status: "applying",
      error: null,
      fieldErrors: {},
    }));

    try {
      const response = await apiRequest("config/apply", {
        method: "POST",
        body: {
          base_revision: current.revision,
          changes: buildConfigChanges(savedDraft, paths),
        },
        retries: 0,
      });
      const applyData = successData<ConfigApplyData>(response);
      if (!mountedRef.current) return;

      setState((previous) => ({
        ...previous,
        baseConfig: cloneConfig(savedDraft),
        draft: cloneConfig(savedDraft),
        revision: applyData.revision,
        instanceId: applyData.instance_id,
        remote: null,
        remoteRevisionHint: null,
        fieldErrors: {},
        status: applyData.reload_scheduled ? "reloading" : "synced",
        error: null,
      }));
    } catch (error) {
      if (!mountedRef.current) return;

      if (error instanceof ConfigProtocolError) {
        const response = error.response;
        if (response.code === "config_conflict") {
          let remote: ConfigRemoteSnapshot | null = null;
          try {
            const stateResponse = await apiRequest(
              `config/state?revision=${encodeURIComponent(current.revision)}`,
              { retries: 0 }
            );
            const stateData = successData<ConfigStateData>(stateResponse);
            if (stateData.changed) {
              remote = {
                config: cloneConfig(stateData.config),
                revision: stateData.revision,
                instanceId: stateData.instance_id,
              };
            }
          } catch {
            // Keep the conflict revision hint; a later refresh can fill the snapshot.
          }
          if (!mountedRef.current) return;
          setState((previous) => ({
            ...previous,
            remote,
            remoteRevisionHint:
              response.data?.current_revision ?? remote?.revision ?? null,
            status: "conflict",
            error: syncError(error),
            fieldErrors: {},
          }));
          return;
        }

        setState((previous) => ({
          ...previous,
          status: "error",
          error: syncError(error),
          fieldErrors:
            response.code === "validation_failed"
              ? { ...(response.data?.field_errors ?? {}) }
              : {},
        }));
        return;
      }

      try {
        const stateResponse = await apiRequest(
          `config/state?revision=${encodeURIComponent(current.revision)}`,
          { retries: 0 }
        );
        const stateData = successData<ConfigStateData>(stateResponse);
        if (!mountedRef.current) return;

        if (stateData.changed) {
          const persisted = paths.every((path) =>
            configValueEquals(
              getConfigValue(stateData.config, path),
              getConfigValue(savedDraft, path)
            )
          );
          if (persisted) {
            setState((previous) => ({
              ...previous,
              baseConfig: cloneConfig(stateData.config),
              draft: cloneConfig(stateData.config),
              revision: stateData.revision,
              instanceId: stateData.instance_id,
              remote: null,
              remoteRevisionHint: null,
              fieldErrors: {},
              status: "synced",
              error: null,
            }));
            return;
          }

          setState((previous) => ({
            ...previous,
            remote: {
              config: cloneConfig(stateData.config),
              revision: stateData.revision,
              instanceId: stateData.instance_id,
            },
            remoteRevisionHint: stateData.revision,
            status: "conflict",
            error: syncError(error),
          }));
          return;
        }
      } catch {
        // The original transport failure remains the actionable state.
      }

      setState((previous) => ({
        ...previous,
        status: "offline",
        error: syncError(error),
      }));
    }
  }, []);

  useEffect(() => {
    const refreshIfVisible = () => {
      const current = stateRef.current;
      if (
        document.visibilityState === "visible" &&
        current.revision &&
        current.status !== "loading" &&
        current.status !== "reloading"
      ) {
        void refresh();
      }
    };
    const onFocus = () => refreshIfVisible();
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") refreshIfVisible();
    };
    const interval = window.setInterval(refreshIfVisible, pollIntervalMs);

    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [pollIntervalMs, refresh]);

  useEffect(() => {
    if (
      state.status !== "reloading" ||
      !state.revision ||
      !state.instanceId
    ) {
      return;
    }

    let active = true;
    let timer: number | null = null;
    const revision = state.revision;
    const previousInstanceId = state.instanceId;
    const startedAt = Date.now();

    const failOnTimeout = () => {
      if (!active || !mountedRef.current) return;
      setState((previous) => ({
        ...previous,
        status: "error",
        error: {
          kind: "protocol",
          message: "Configuration reload timed out",
        },
      }));
    };

    const scheduleNext = () => {
      if (!active) return;
      timer = window.setTimeout(checkInstance, pollIntervalMs);
    };

    const checkInstance = async () => {
      if (!active) return;
      if (Date.now() - startedAt >= reloadTimeoutMs) {
        failOnTimeout();
        return;
      }

      try {
        const response = await apiRequest(
          `config/state?revision=${encodeURIComponent(revision)}`,
          { retries: 0 }
        );
        const stateData = successData<ConfigStateData>(response);
        if (!active || !mountedRef.current) return;

        if (stateData.instance_id !== previousInstanceId) {
          setState((previous) => ({
            ...previous,
            baseConfig: stateData.changed
              ? cloneConfig(stateData.config)
              : previous.baseConfig,
            draft: stateData.changed
              ? cloneConfig(stateData.config)
              : previous.draft,
            revision: stateData.revision,
            instanceId: stateData.instance_id,
            remote: null,
            remoteRevisionHint: null,
            fieldErrors: {},
            status: "synced",
            error: null,
          }));
          return;
        }
      } catch (error) {
        if (error instanceof ConfigProtocolError) {
          setState((previous) => ({
            ...previous,
            status: "error",
            error: syncError(error),
          }));
          return;
        }
        // A plugin process can briefly disappear while AstrBot reloads it.
      }

      if (Date.now() - startedAt >= reloadTimeoutMs) {
        failOnTimeout();
      } else {
        scheduleNext();
      }
    };

    scheduleNext();
    return () => {
      active = false;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [
    pollIntervalMs,
    reloadTimeoutMs,
    state.instanceId,
    state.revision,
    state.status,
  ]);

  const remotePaths = useMemo(
    () =>
      state.baseConfig && state.remote
        ? diffConfigLeafPaths(state.baseConfig, state.remote.config)
        : [],
    [state.baseConfig, state.remote]
  );
  const overlapPaths = useMemo(() => {
    const remoteSet = new Set(remotePaths);
    return dirtyPaths.filter((path) => remoteSet.has(path));
  }, [dirtyPaths, remotePaths]);

  return {
    schemaData: state.schemaData,
    baseConfig: state.baseConfig,
    draft: state.draft,
    revision: state.revision,
    instanceId: state.instanceId,
    remoteConfig: state.remote?.config ?? null,
    remoteRevision: state.remote?.revision ?? state.remoteRevisionHint,
    remoteInstanceId: state.remote?.instanceId ?? null,
    dirtyPaths,
    localPaths: dirtyPaths,
    remotePaths,
    overlapPaths,
    fieldErrors: state.fieldErrors,
    status: state.status,
    error: state.error,
    changeField,
    refresh,
    apply,
    acceptRemote,
    rebaseRemote,
  };
}
