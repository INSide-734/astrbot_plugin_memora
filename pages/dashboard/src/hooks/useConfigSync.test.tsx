import {
  act,
  cleanup,
  renderHook,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ConfigApiError,
  ConfigApiResponse,
  ConfigApplyData,
  ConfigObject,
  ConfigSchemaData,
  ConfigStateData,
  ConfigSyncOptions,
} from "@/types/config";

import { useConfigSync } from "./useConfigSync";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
}

type BridgeReply<T> = ConfigApiResponse<T> | Error;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

const BASE_CONFIG: ConfigObject = {
  bot_language: "zh",
  recall_engine: { top_k: 8, mode: "hybrid" },
  provider_settings: {
    llm_provider_id: "",
    embedding_provider_id: "",
  },
};

function schemaSuccess(): ConfigApiResponse<ConfigSchemaData> {
  return {
    status: "ok",
    data: {
      plugin_name: "astrbot_plugin_memora",
      schema: {
        bot_language: {
          type: "string",
          description: "Bot language",
          options: ["zh", "en", "ru"],
        },
      },
      provider_options: {
        llm: [{ id: "llm-primary", label: "GPT Primary" }],
        embedding: [{ id: "embed-primary", label: "Embedding Primary" }],
      },
      capabilities: { hot_reload: true },
    },
  };
}

function stateSuccess(
  config: ConfigObject,
  revision = "rev-1",
  instanceId = "instance-1"
): ConfigApiResponse<ConfigStateData> {
  return {
    status: "ok",
    data: {
      revision,
      instance_id: instanceId,
      changed: true,
      config,
      prompt_defaults: { gate_judge: "", group_chat: "", private_chat: "" },
    },
  };
}

function stateUnchanged(
  revision = "rev-1",
  instanceId = "instance-1"
): ConfigApiResponse<ConfigStateData> {
  return {
    status: "ok",
    data: {
      revision,
      instance_id: instanceId,
      changed: false,
      prompt_defaults: { gate_judge: "", group_chat: "", private_chat: "" },
    },
  };
}

function applySuccess(
  overrides: Partial<ConfigApplyData> = {}
): ConfigApiResponse<ConfigApplyData> {
  return {
    status: "ok",
    data: {
      revision: "rev-2",
      changed_paths: ["recall_engine.top_k"],
      reload_scheduled: false,
      restart_required: true,
      rebuild_required: false,
      instance_id: "instance-1",
      ...overrides,
    },
  };
}

function configError(
  code: ConfigApiError["code"],
  message: string,
  data?: ConfigApiError["data"]
): ConfigApiError {
  return { status: "error", code, message, ...(data ? { data } : {}) };
}

async function resolveReply<T>(reply: BridgeReply<T>): Promise<ApiResponse> {
  if (reply instanceof Error) throw reply;
  return reply as ApiResponse;
}

describe("useConfigSync", () => {
  let bridge: BridgeMock;
  let visibility: DocumentVisibilityState;
  let schemaHandler: () => Promise<ApiResponse>;
  let stateHandler: (params: Record<string, string>) => Promise<ApiResponse>;
  let postHandler: (body: unknown) => Promise<ApiResponse>;

  const stateCalls = () =>
    bridge.apiGet.mock.calls.filter(([endpoint]) => endpoint === "page/config/state");

  const queueStates = (...replies: Array<BridgeReply<ConfigStateData>>) => {
    let index = 0;
    stateHandler = async () => {
      const reply = replies[Math.min(index, replies.length - 1)];
      index += 1;
      return resolveReply(reply);
    };
  };

  const renderSync = (options: ConfigSyncOptions = {}) =>
    renderHook(() =>
      useConfigSync({
        pollIntervalMs: 60_000,
        reloadTimeoutMs: 30_000,
        ...options,
      })
    );

  const waitForLoaded = async (
    hook: ReturnType<typeof renderSync>,
    status: "synced" | "offline" | "error" = "synced"
  ) => {
    await waitFor(() => expect(hook.result.current.status).toBe(status));
  };

  const flushMicrotasks = async () => {
    await act(async () => {
      for (let index = 0; index < 8; index += 1) {
        await Promise.resolve();
      }
    });
  };

  beforeEach(() => {
    visibility = "visible";
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => visibility,
    });

    schemaHandler = () => resolveReply(schemaSuccess());
    stateHandler = () => resolveReply(stateSuccess(BASE_CONFIG));
    postHandler = () => resolveReply(applySuccess());

    bridge = {
      apiGet: vi.fn((endpoint: string, params: Record<string, string> = {}) => {
        if (endpoint === "page/config/schema") return schemaHandler();
        if (endpoint === "page/config/state") return stateHandler(params);
        return Promise.reject(new Error(`Unexpected GET endpoint: ${endpoint}`));
      }),
      apiPost: vi.fn((endpoint: string, body: unknown) => {
        if (endpoint === "page/config/apply") return postHandler(body);
        return Promise.reject(new Error(`Unexpected POST endpoint: ${endpoint}`));
      }),
    };

    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: bridge,
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  it("loads schema metadata and a full config snapshot into synced state", async () => {
    const hook = renderSync();

    expect(hook.result.current.status).toBe("loading");
    await waitForLoaded(hook);

    expect(hook.result.current.schemaData).toEqual(schemaSuccess().data);
    expect(hook.result.current.baseConfig).toEqual(BASE_CONFIG);
    expect(hook.result.current.draft).toEqual(BASE_CONFIG);
    expect(hook.result.current.draft).not.toBe(hook.result.current.baseConfig);
    expect(hook.result.current.revision).toBe("rev-1");
    expect(hook.result.current.instanceId).toBe("instance-1");
    expect(hook.result.current.dirtyPaths).toEqual([]);
  });

  it("loads configuration data unwrapped by the AstrBot page bridge", async () => {
    schemaHandler = async () =>
      schemaSuccess().data as unknown as ApiResponse;
    stateHandler = async () =>
      stateSuccess(BASE_CONFIG).data as unknown as ApiResponse;

    const hook = renderSync();

    await waitForLoaded(hook);
    expect(hook.result.current.schemaData).toEqual(schemaSuccess().data);
    expect(hook.result.current.baseConfig).toEqual(BASE_CONFIG);
    expect(hook.result.current.revision).toBe("rev-1");
    expect(hook.result.current.instanceId).toBe("instance-1");
  });

  it("changes a field immutably and exposes sorted dirty/local paths", async () => {
    const hook = renderSync();
    await waitForLoaded(hook);
    const base = hook.result.current.baseConfig;

    act(() => hook.result.current.changeField("recall_engine.top_k", 12));

    expect(hook.result.current.status).toBe("dirty");
    expect(hook.result.current.baseConfig).toBe(base);
    expect(hook.result.current.baseConfig).toEqual(BASE_CONFIG);
    expect(hook.result.current.draft).toEqual({
      ...BASE_CONFIG,
      recall_engine: { top_k: 12, mode: "hybrid" },
    });
    expect(hook.result.current.dirtyPaths).toEqual(["recall_engine.top_k"]);
    expect(hook.result.current.localPaths).toEqual(["recall_engine.top_k"]);
  });

  it("discards the local draft without sending a request", async () => {
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    expect(hook.result.current.status).toBe("dirty");

    act(() => hook.result.current.discardLocal());

    expect(hook.result.current.draft).toEqual(BASE_CONFIG);
    expect(hook.result.current.dirtyPaths).toEqual([]);
    expect(hook.result.current.status).toBe("synced");
    expect(bridge.apiPost).not.toHaveBeenCalled();
  });

  it("discardLocal adopts a populated conflict snapshot without saving", async () => {
    const remote = {
      ...BASE_CONFIG,
      recall_engine: { top_k: 20, mode: "vector" },
    };
    queueStates(stateSuccess(BASE_CONFIG), stateSuccess(remote, "rev-2"));
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    await act(async () => hook.result.current.refresh());
    expect(hook.result.current.status).toBe("conflict");
    expect(hook.result.current.overlapPaths).toEqual(["recall_engine.top_k"]);

    act(() => hook.result.current.discardLocal());

    expect(hook.result.current.baseConfig).toEqual(remote);
    expect(hook.result.current.draft).toEqual(remote);
    expect(hook.result.current.revision).toBe("rev-2");
    expect(hook.result.current.remoteConfig).toBeNull();
    expect(hook.result.current.overlapPaths).toEqual([]);
    expect(hook.result.current.dirtyPaths).toEqual([]);
    expect(hook.result.current.status).toBe("synced");
    expect(bridge.apiPost).not.toHaveBeenCalled();
  });

  it("polls a visible page conditionally and refreshes immediately on focus", async () => {
    vi.useFakeTimers();
    queueStates(stateSuccess(BASE_CONFIG), stateUnchanged());
    const hook = renderSync({ pollIntervalMs: 5_000 });
    await flushMicrotasks();
    expect(hook.result.current.status).toBe("synced");
    expect(stateCalls()).toHaveLength(1);

    await act(async () => vi.advanceTimersByTimeAsync(4_999));
    expect(stateCalls()).toHaveLength(1);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(stateCalls()).toHaveLength(2);
    expect(stateCalls()[1]).toEqual([
      "page/config/state",
      { revision: "rev-1" },
    ]);

    act(() => window.dispatchEvent(new Event("focus")));
    await flushMicrotasks();
    expect(stateCalls()).toHaveLength(3);
  });

  it("pauses interval refresh while hidden and refreshes immediately when visible", async () => {
    vi.useFakeTimers();
    queueStates(stateSuccess(BASE_CONFIG), stateUnchanged());
    const hook = renderSync({ pollIntervalMs: 100 });
    await flushMicrotasks();
    expect(hook.result.current.status).toBe("synced");

    visibility = "hidden";
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await act(async () => vi.advanceTimersByTimeAsync(500));
    expect(stateCalls()).toHaveLength(1);

    visibility = "visible";
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await flushMicrotasks();
    expect(stateCalls()).toHaveLength(2);
  });

  it("ignores an older refresh response that resolves after a newer one", async () => {
    const olderResponse = deferred<ApiResponse>();
    const newerResponse = deferred<ApiResponse>();
    const olderConfig = { ...BASE_CONFIG, bot_language: "en" };
    const newerConfig = { ...BASE_CONFIG, bot_language: "ru" };
    let requestCount = 0;
    stateHandler = () => {
      requestCount += 1;
      if (requestCount === 1) return resolveReply(stateSuccess(BASE_CONFIG));
      if (requestCount === 2) return olderResponse.promise;
      return newerResponse.promise;
    };
    const hook = renderSync();
    await waitForLoaded(hook);

    let olderRefresh!: Promise<void>;
    let newerRefresh!: Promise<void>;
    act(() => {
      olderRefresh = hook.result.current.refresh();
      newerRefresh = hook.result.current.refresh();
    });
    expect(stateCalls()).toHaveLength(3);

    newerResponse.resolve(
      await resolveReply(stateSuccess(newerConfig, "rev-3"))
    );
    await act(async () => newerRefresh);
    expect(hook.result.current.revision).toBe("rev-3");

    olderResponse.resolve(
      await resolveReply(stateSuccess(olderConfig, "rev-2"))
    );
    await act(async () => olderRefresh);

    expect(hook.result.current.revision).toBe("rev-3");
    expect(hook.result.current.baseConfig).toEqual(newerConfig);
    expect(hook.result.current.draft).toEqual(newerConfig);
  });

  it("automatically accepts a new remote snapshot when the local draft is clean", async () => {
    const remote = {
      ...BASE_CONFIG,
      recall_engine: { top_k: 20, mode: "vector" },
    };
    queueStates(
      stateSuccess(BASE_CONFIG),
      stateSuccess(remote, "rev-2", "instance-1")
    );
    const hook = renderSync();
    await waitForLoaded(hook);

    await act(async () => hook.result.current.refresh());

    expect(hook.result.current.status).toBe("synced");
    expect(hook.result.current.revision).toBe("rev-2");
    expect(hook.result.current.baseConfig).toEqual(remote);
    expect(hook.result.current.draft).toEqual(remote);
  });

  it("preserves dirty local values and exposes remote and overlapping paths", async () => {
    const remote = {
      ...BASE_CONFIG,
      recall_engine: { top_k: 20, mode: "vector" },
    };
    queueStates(stateSuccess(BASE_CONFIG), stateSuccess(remote, "rev-2"));
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));

    await act(async () => hook.result.current.refresh());

    expect(hook.result.current.status).toBe("conflict");
    expect(hook.result.current.draft).toEqual({
      ...BASE_CONFIG,
      recall_engine: { top_k: 12, mode: "hybrid" },
    });
    expect(hook.result.current.remoteConfig).toEqual(remote);
    expect(hook.result.current.remoteRevision).toBe("rev-2");
    expect(hook.result.current.remotePaths).toEqual([
      "recall_engine.mode",
      "recall_engine.top_k",
    ]);
    expect(hook.result.current.overlapPaths).toEqual(["recall_engine.top_k"]);
  });

  it("does not report a conflict when a changed revision has no remote value changes", async () => {
    queueStates(
      stateSuccess(BASE_CONFIG),
      stateSuccess(BASE_CONFIG, "rev-2")
    );
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));

    await act(async () => hook.result.current.refresh());

    expect(hook.result.current.status).toBe("dirty");
    expect(hook.result.current.revision).toBe("rev-2");
    expect(hook.result.current.remoteConfig).toBeNull();
    expect(hook.result.current.dirtyPaths).toEqual(["recall_engine.top_k"]);
  });

  it("acceptRemote discards local edits and adopts the full remote snapshot", async () => {
    const remote = { ...BASE_CONFIG, bot_language: "en" };
    queueStates(stateSuccess(BASE_CONFIG), stateSuccess(remote, "rev-2"));
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("bot_language", "ru"));
    await act(async () => hook.result.current.refresh());

    act(() => hook.result.current.acceptRemote());

    expect(hook.result.current.status).toBe("synced");
    expect(hook.result.current.baseConfig).toEqual(remote);
    expect(hook.result.current.draft).toEqual(remote);
    expect(hook.result.current.revision).toBe("rev-2");
    expect(hook.result.current.remoteConfig).toBeNull();
  });

  it("ignores an in-flight refresh after accepting the remote snapshot", async () => {
    const lateResponse = deferred<ApiResponse>();
    const remote = {
      ...BASE_CONFIG,
      recall_engine: { top_k: 20, mode: "vector" },
    };
    let requestCount = 0;
    stateHandler = () => {
      requestCount += 1;
      if (requestCount === 1) return resolveReply(stateSuccess(BASE_CONFIG));
      if (requestCount === 2) {
        return resolveReply(stateSuccess(remote, "rev-2"));
      }
      return lateResponse.promise;
    };
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    await act(async () => hook.result.current.refresh());
    expect(hook.result.current.status).toBe("conflict");

    let lateRefresh!: Promise<void>;
    act(() => {
      lateRefresh = hook.result.current.refresh();
    });
    expect(stateCalls()[2]).toEqual([
      "page/config/state",
      { revision: "rev-1" },
    ]);

    act(() => {
      hook.result.current.acceptRemote();
      hook.result.current.changeField("bot_language", "en");
    });
    expect(hook.result.current.status).toBe("dirty");

    lateResponse.resolve(
      await resolveReply(stateSuccess(remote, "rev-2"))
    );
    await act(async () => lateRefresh);

    expect(hook.result.current.status).toBe("dirty");
    expect(hook.result.current.revision).toBe("rev-2");
    expect(hook.result.current.baseConfig).toEqual(remote);
    expect(hook.result.current.draft).toEqual({
      ...remote,
      bot_language: "en",
    });
    expect(hook.result.current.remoteConfig).toBeNull();
    expect(bridge.apiPost).not.toHaveBeenCalled();
  });

  it("rebaseRemote overlays only local dirty values and does not save", async () => {
    const remote = {
      ...BASE_CONFIG,
      recall_engine: { top_k: 20, mode: "vector" },
    };
    queueStates(stateSuccess(BASE_CONFIG), stateSuccess(remote, "rev-2"));
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    await act(async () => hook.result.current.refresh());

    act(() => hook.result.current.rebaseRemote());

    expect(hook.result.current.status).toBe("dirty");
    expect(hook.result.current.baseConfig).toEqual(remote);
    expect(hook.result.current.draft).toEqual({
      ...remote,
      recall_engine: { top_k: 12, mode: "vector" },
    });
    expect(hook.result.current.revision).toBe("rev-2");
    expect(hook.result.current.dirtyPaths).toEqual(["recall_engine.top_k"]);
    expect(bridge.apiPost).not.toHaveBeenCalled();
  });

  it("ignores an in-flight refresh after rebasing onto the remote snapshot", async () => {
    const lateResponse = deferred<ApiResponse>();
    const remote = {
      ...BASE_CONFIG,
      recall_engine: { top_k: 20, mode: "vector" },
    };
    let requestCount = 0;
    stateHandler = () => {
      requestCount += 1;
      if (requestCount === 1) return resolveReply(stateSuccess(BASE_CONFIG));
      if (requestCount === 2) {
        return resolveReply(stateSuccess(remote, "rev-2"));
      }
      return lateResponse.promise;
    };
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    await act(async () => hook.result.current.refresh());
    expect(hook.result.current.status).toBe("conflict");

    let lateRefresh!: Promise<void>;
    act(() => {
      lateRefresh = hook.result.current.refresh();
    });
    expect(stateCalls()[2]).toEqual([
      "page/config/state",
      { revision: "rev-1" },
    ]);

    act(() => hook.result.current.rebaseRemote());
    expect(hook.result.current.status).toBe("dirty");

    lateResponse.resolve(
      await resolveReply(stateSuccess(remote, "rev-2"))
    );
    await act(async () => lateRefresh);

    expect(hook.result.current.status).toBe("dirty");
    expect(hook.result.current.revision).toBe("rev-2");
    expect(hook.result.current.baseConfig).toEqual(remote);
    expect(hook.result.current.draft).toEqual({
      ...remote,
      recall_engine: { top_k: 12, mode: "vector" },
    });
    expect(hook.result.current.remoteConfig).toBeNull();
    expect(bridge.apiPost).not.toHaveBeenCalled();
  });

  it("applies only dirty dotted changes and becomes synced without reload", async () => {
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));

    await act(async () => hook.result.current.apply());

    expect(bridge.apiPost).toHaveBeenCalledWith("page/config/apply", {
      base_revision: "rev-1",
      changes: { "recall_engine.top_k": 12 },
    });
    expect(hook.result.current.status).toBe("synced");
    expect(hook.result.current.revision).toBe("rev-2");
    expect(hook.result.current.baseConfig).toEqual(hook.result.current.draft);
    expect(hook.result.current.dirtyPaths).toEqual([]);
  });

  it("rejects an undefined draft change locally without discarding it", async () => {
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() =>
      hook.result.current.changeField("recall_engine.top_k", undefined)
    );

    await act(async () => hook.result.current.apply());

    expect(bridge.apiPost).not.toHaveBeenCalled();
    expect(stateCalls()).toHaveLength(1);
    expect(hook.result.current.status).toBe("error");
    expect(hook.result.current.error).toMatchObject({
      kind: "protocol",
      code: "invalid_request",
    });
    expect(hook.result.current.error?.message).toMatch(/json/i);
    expect(hook.result.current.baseConfig).toEqual(BASE_CONFIG);
    const recallEngine = hook.result.current.draft
      ?.recall_engine as ConfigObject;
    expect(Object.prototype.hasOwnProperty.call(recallEngine, "top_k")).toBe(
      true
    );
    expect(recallEngine.top_k).toBeUndefined();
    expect(hook.result.current.dirtyPaths).toEqual(["recall_engine.top_k"]);
  });

  it("suppresses repeated apply and refreshes while apply is pending", async () => {
    vi.useFakeTimers();
    const pendingPost = deferred<ApiResponse>();
    queueStates(stateSuccess(BASE_CONFIG), stateUnchanged());
    postHandler = () => pendingPost.promise;
    const hook = renderSync({ pollIntervalMs: 50 });
    await flushMicrotasks();
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));

    let applyPromise!: Promise<void>;
    let repeatedApplyPromise!: Promise<void>;
    act(() => {
      applyPromise = hook.result.current.apply();
      repeatedApplyPromise = hook.result.current.apply();
    });
    await flushMicrotasks();
    expect(hook.result.current.status).toBe("applying");

    await act(async () => hook.result.current.refresh());
    await act(async () => vi.advanceTimersByTimeAsync(150));

    expect(stateCalls()).toHaveLength(1);
    expect(hook.result.current.status).toBe("applying");

    pendingPost.resolve(await resolveReply(applySuccess()));
    await act(async () => Promise.all([applyPromise, repeatedApplyPromise]));
    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
  });

  it("preserves edits made while a successful apply is pending", async () => {
    const pendingPost = deferred<ApiResponse>();
    postHandler = () => pendingPost.promise;
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));

    let applyPromise!: Promise<void>;
    act(() => {
      applyPromise = hook.result.current.apply();
    });
    await waitFor(() => expect(hook.result.current.status).toBe("applying"));
    act(() => hook.result.current.changeField("bot_language", "en"));
    expect(hook.result.current.status).toBe("applying");

    pendingPost.resolve(await resolveReply(applySuccess()));
    await act(async () => applyPromise);

    expect(hook.result.current.baseConfig).toEqual({
      ...BASE_CONFIG,
      recall_engine: { top_k: 12, mode: "hybrid" },
    });
    expect(hook.result.current.draft).toEqual({
      ...BASE_CONFIG,
      bot_language: "en",
      recall_engine: { top_k: 12, mode: "hybrid" },
    });
    expect(hook.result.current.status).toBe("dirty");
    expect(hook.result.current.dirtyPaths).toEqual(["bot_language"]);
  });

  it("loads the latest full snapshot after a stale apply conflict", async () => {
    const remote = {
      ...BASE_CONFIG,
      recall_engine: { top_k: 20, mode: "vector" },
    };
    queueStates(stateSuccess(BASE_CONFIG), stateSuccess(remote, "rev-2"));
    postHandler = () =>
      resolveReply(
        configError("config_conflict", "stale revision", {
          current_revision: "rev-2",
        })
      );
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));

    await act(async () => hook.result.current.apply());

    expect(hook.result.current.status).toBe("conflict");
    expect(hook.result.current.error).toEqual({
      kind: "protocol",
      code: "config_conflict",
      message: "stale revision",
      data: { current_revision: "rev-2" },
    });
    expect(hook.result.current.revision).toBe("rev-1");
    expect(hook.result.current.remoteRevision).toBe("rev-2");
    expect(hook.result.current.remoteConfig).toEqual(remote);
    expect(hook.result.current.draft).toEqual({
      ...BASE_CONFIG,
      recall_engine: { top_k: 12, mode: "hybrid" },
    });
  });

  it("keeps validation errors path-indexed without discarding the draft", async () => {
    postHandler = () =>
      resolveReply(
        configError("validation_failed", "invalid config", {
          field_errors: { "recall_engine.top_k": "Must be positive" },
        })
      );
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", -1));

    await act(async () => hook.result.current.apply());

    expect(hook.result.current.status).toBe("error");
    expect(hook.result.current.fieldErrors).toEqual({
      "recall_engine.top_k": "Must be positive",
    });
    expect(hook.result.current.error).toEqual({
      kind: "protocol",
      code: "validation_failed",
      message: "invalid config",
      data: {
        field_errors: { "recall_engine.top_k": "Must be positive" },
      },
    });
    expect(hook.result.current.draft).toEqual({
      ...BASE_CONFIG,
      recall_engine: { top_k: -1, mode: "hybrid" },
    });
    expect(hook.result.current.dirtyPaths).toEqual(["recall_engine.top_k"]);
  });

  it("keeps persist failures retryable", async () => {
    let attempt = 0;
    postHandler = () =>
      resolveReply(
        attempt++ === 0
          ? configError("persist_failed", "disk full")
          : applySuccess()
      );
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));

    await act(async () => hook.result.current.apply());
    expect(hook.result.current.status).toBe("error");
    expect(hook.result.current.dirtyPaths).toEqual(["recall_engine.top_k"]);

    await act(async () => hook.result.current.apply());
    expect(bridge.apiPost).toHaveBeenCalledTimes(2);
    expect(hook.result.current.status).toBe("synced");
  });

  it("reconciles a lost POST response when persisted values match", async () => {
    const persisted = {
      ...BASE_CONFIG,
      bot_language: "en",
      recall_engine: { top_k: 12, mode: "hybrid" },
    };
    queueStates(stateSuccess(BASE_CONFIG), stateSuccess(persisted, "rev-2"));
    postHandler = () => Promise.reject(new Error("connection reset"));
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));

    await act(async () => hook.result.current.apply());

    expect(hook.result.current.status).toBe("synced");
    expect(hook.result.current.revision).toBe("rev-2");
    expect(hook.result.current.baseConfig).toEqual(persisted);
    expect(hook.result.current.draft).toEqual(persisted);
  });

  it("preserves pending edits after lost-response reconciliation", async () => {
    const pendingPost = deferred<ApiResponse>();
    const persisted = {
      ...BASE_CONFIG,
      recall_engine: { top_k: 12, mode: "hybrid" },
    };
    queueStates(stateSuccess(BASE_CONFIG), stateSuccess(persisted, "rev-2"));
    postHandler = () => pendingPost.promise;
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));

    let applyPromise!: Promise<void>;
    act(() => {
      applyPromise = hook.result.current.apply();
    });
    await waitFor(() => expect(hook.result.current.status).toBe("applying"));
    act(() => hook.result.current.changeField("bot_language", "en"));
    expect(hook.result.current.status).toBe("applying");

    pendingPost.reject(new Error("connection reset"));
    await act(async () => applyPromise);

    expect(hook.result.current.baseConfig).toEqual(persisted);
    expect(hook.result.current.draft).toEqual({
      ...persisted,
      bot_language: "en",
    });
    expect(hook.result.current.status).toBe("dirty");
    expect(hook.result.current.dirtyPaths).toEqual(["bot_language"]);
  });

  it("does not retry a lost POST and keeps an unconfirmed draft retryable", async () => {
    vi.useFakeTimers();
    queueStates(stateSuccess(BASE_CONFIG), stateUnchanged());
    postHandler = () => Promise.reject(new Error("connection reset"));
    const hook = renderSync({ pollIntervalMs: 60_000 });
    await flushMicrotasks();
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));

    await act(async () => hook.result.current.apply());
    await act(async () => vi.advanceTimersByTimeAsync(10_000));

    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
    expect(stateCalls()).toHaveLength(2);
    expect(stateCalls()[1]).toEqual([
      "page/config/state",
      { revision: "rev-1" },
    ]);
    expect(hook.result.current.status).toBe("offline");
    expect(hook.result.current.draft).toEqual({
      ...BASE_CONFIG,
      recall_engine: { top_k: 12, mode: "hybrid" },
    });
    expect(hook.result.current.dirtyPaths).toEqual(["recall_engine.top_k"]);
  });

  it("tolerates reload disconnects until a changed instance confirms success", async () => {
    vi.useFakeTimers();
    queueStates(
      stateSuccess(BASE_CONFIG),
      new Error("restarting"),
      stateUnchanged("rev-2", "instance-1"),
      stateUnchanged("rev-2", "instance-2")
    );
    postHandler = () =>
      resolveReply(applySuccess({ reload_scheduled: true }));
    const hook = renderSync({ pollIntervalMs: 50, reloadTimeoutMs: 500 });
    await flushMicrotasks();
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    await act(async () => hook.result.current.apply());
    expect(hook.result.current.status).toBe("reloading");

    await act(async () => vi.advanceTimersByTimeAsync(50));
    expect(hook.result.current.status).toBe("reloading");
    await act(async () => vi.advanceTimersByTimeAsync(50));
    expect(hook.result.current.status).toBe("reloading");
    await act(async () => vi.advanceTimersByTimeAsync(50));

    expect(hook.result.current.status).toBe("synced");
    expect(hook.result.current.instanceId).toBe("instance-2");
    expect(hook.result.current.revision).toBe("rev-2");
  });

  it("keeps pending edits through reload and returns to dirty on confirmation", async () => {
    vi.useFakeTimers();
    const pendingPost = deferred<ApiResponse>();
    queueStates(
      stateSuccess(BASE_CONFIG),
      stateUnchanged("rev-2", "instance-2")
    );
    postHandler = () => pendingPost.promise;
    const hook = renderSync({ pollIntervalMs: 50, reloadTimeoutMs: 500 });
    await flushMicrotasks();
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));

    let applyPromise!: Promise<void>;
    act(() => {
      applyPromise = hook.result.current.apply();
    });
    await flushMicrotasks();
    expect(hook.result.current.status).toBe("applying");
    act(() => hook.result.current.changeField("bot_language", "en"));
    expect(hook.result.current.status).toBe("applying");

    pendingPost.resolve(
      await resolveReply(applySuccess({ reload_scheduled: true }))
    );
    await act(async () => applyPromise);
    expect(hook.result.current.status).toBe("reloading");
    expect(hook.result.current.draft).toEqual({
      ...BASE_CONFIG,
      bot_language: "en",
      recall_engine: { top_k: 12, mode: "hybrid" },
    });

    await act(async () => vi.advanceTimersByTimeAsync(50));

    expect(hook.result.current.status).toBe("dirty");
    expect(hook.result.current.instanceId).toBe("instance-2");
    expect(hook.result.current.dirtyPaths).toEqual(["bot_language"]);
  });

  it("suppresses reload GETs while hidden and checks immediately when visible", async () => {
    vi.useFakeTimers();
    queueStates(
      stateSuccess(BASE_CONFIG),
      stateUnchanged("rev-2", "instance-2")
    );
    postHandler = () =>
      resolveReply(applySuccess({ reload_scheduled: true }));
    const hook = renderSync({ pollIntervalMs: 50, reloadTimeoutMs: 500 });
    await flushMicrotasks();

    visibility = "hidden";
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    await act(async () => hook.result.current.apply());
    await act(async () => vi.advanceTimersByTimeAsync(200));

    expect(hook.result.current.status).toBe("reloading");
    expect(stateCalls()).toHaveLength(1);

    visibility = "visible";
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    await flushMicrotasks();

    expect(stateCalls()).toHaveLength(2);
    expect(hook.result.current.status).toBe("synced");
    expect(hook.result.current.instanceId).toBe("instance-2");
  });

  it("checks the reload instance immediately on window focus", async () => {
    vi.useFakeTimers();
    queueStates(
      stateSuccess(BASE_CONFIG),
      stateUnchanged("rev-2", "instance-2")
    );
    postHandler = () =>
      resolveReply(applySuccess({ reload_scheduled: true }));
    const hook = renderSync({ pollIntervalMs: 500, reloadTimeoutMs: 1_000 });
    await flushMicrotasks();
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    await act(async () => hook.result.current.apply());

    act(() => window.dispatchEvent(new Event("focus")));
    await flushMicrotasks();

    expect(stateCalls()).toHaveLength(2);
    expect(hook.result.current.status).toBe("synced");
    expect(hook.result.current.instanceId).toBe("instance-2");
  });

  it("keeps a single reload polling chain after an immediate focus check", async () => {
    vi.useFakeTimers();
    queueStates(stateSuccess(BASE_CONFIG), stateUnchanged("rev-2", "instance-1"));
    postHandler = () =>
      resolveReply(applySuccess({ reload_scheduled: true }));
    const hook = renderSync({ pollIntervalMs: 50, reloadTimeoutMs: 500 });
    await flushMicrotasks();
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    await act(async () => hook.result.current.apply());

    await act(async () => vi.advanceTimersByTimeAsync(10));
    act(() => window.dispatchEvent(new Event("focus")));
    await flushMicrotasks();
    expect(stateCalls()).toHaveLength(2);

    await act(async () => vi.advanceTimersByTimeAsync(90));

    expect(hook.result.current.status).toBe("reloading");
    expect(stateCalls()).toHaveLength(3);
  });

  it("enforces the reload deadline while a state request never settles", async () => {
    vi.useFakeTimers();
    let requestCount = 0;
    stateHandler = () => {
      requestCount += 1;
      if (requestCount === 1) return resolveReply(stateSuccess(BASE_CONFIG));
      return new Promise<ApiResponse>(() => undefined);
    };
    postHandler = () =>
      resolveReply(applySuccess({ reload_scheduled: true }));
    const hook = renderSync({ pollIntervalMs: 20, reloadTimeoutMs: 50 });
    await flushMicrotasks();
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    await act(async () => hook.result.current.apply());

    await act(async () => vi.advanceTimersByTimeAsync(20));
    expect(stateCalls()).toHaveLength(2);
    await act(async () => vi.advanceTimersByTimeAsync(30));

    expect(hook.result.current.status).toBe("error");
    expect(hook.result.current.error?.message).toMatch(/reload.*timed out/i);
  });

  it("ignores a reload response that arrives after the hard deadline", async () => {
    vi.useFakeTimers();
    const lateState = deferred<ApiResponse>();
    let requestCount = 0;
    stateHandler = () => {
      requestCount += 1;
      return requestCount === 1
        ? resolveReply(stateSuccess(BASE_CONFIG))
        : lateState.promise;
    };
    postHandler = () =>
      resolveReply(applySuccess({ reload_scheduled: true }));
    const hook = renderSync({ pollIntervalMs: 20, reloadTimeoutMs: 50 });
    await flushMicrotasks();
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    await act(async () => hook.result.current.apply());

    await act(async () => vi.advanceTimersByTimeAsync(50));
    expect(hook.result.current.status).toBe("error");

    lateState.resolve(
      await resolveReply(stateUnchanged("rev-2", "instance-2"))
    );
    await flushMicrotasks();

    expect(hook.result.current.status).toBe("error");
    expect(hook.result.current.instanceId).toBe("instance-1");
  });

  it("rejects a reload response when wall time passes a delayed deadline timer", async () => {
    vi.useFakeTimers();
    const lateState = deferred<ApiResponse>();
    let requestCount = 0;
    stateHandler = () => {
      requestCount += 1;
      return requestCount === 1
        ? resolveReply(stateSuccess(BASE_CONFIG))
        : lateState.promise;
    };
    postHandler = () =>
      resolveReply(applySuccess({ reload_scheduled: true }));
    const hook = renderSync({ pollIntervalMs: 20, reloadTimeoutMs: 50 });
    await flushMicrotasks();
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    await act(async () => hook.result.current.apply());

    await act(async () => vi.advanceTimersByTimeAsync(20));
    expect(stateCalls()).toHaveLength(2);
    vi.setSystemTime(Date.now() + 100);

    lateState.resolve(
      await resolveReply(stateUnchanged("rev-2", "instance-2"))
    );
    await flushMicrotasks();

    expect(hook.result.current.status).toBe("error");
    expect(hook.result.current.error?.message).toMatch(/reload.*timed out/i);
    expect(hook.result.current.instanceId).toBe("instance-1");
  });

  it("keeps the hard reload deadline while the document is hidden", async () => {
    vi.useFakeTimers();
    postHandler = () =>
      resolveReply(applySuccess({ reload_scheduled: true }));
    const hook = renderSync({ pollIntervalMs: 100, reloadTimeoutMs: 50 });
    await flushMicrotasks();

    visibility = "hidden";
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    await act(async () => hook.result.current.apply());
    await act(async () => vi.advanceTimersByTimeAsync(50));

    expect(stateCalls()).toHaveLength(1);
    expect(hook.result.current.status).toBe("error");
    expect(hook.result.current.error?.message).toMatch(/reload.*timed out/i);
  });

  it("stops reload polling at the configured timeout", async () => {
    vi.useFakeTimers();
    queueStates(stateSuccess(BASE_CONFIG), stateUnchanged("rev-2", "instance-1"));
    postHandler = () =>
      resolveReply(applySuccess({ reload_scheduled: true }));
    const hook = renderSync({ pollIntervalMs: 20, reloadTimeoutMs: 50 });
    await flushMicrotasks();
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    await act(async () => hook.result.current.apply());

    await act(async () => vi.advanceTimersByTimeAsync(80));

    expect(hook.result.current.status).toBe("error");
    expect(hook.result.current.error?.message).toMatch(/reload.*timed out/i);
  });

  it("cleans focus, visibility, interval, and reload timers on unmount", async () => {
    vi.useFakeTimers();
    const windowRemove = vi.spyOn(window, "removeEventListener");
    const documentRemove = vi.spyOn(document, "removeEventListener");
    postHandler = () =>
      resolveReply(applySuccess({ reload_scheduled: true }));
    const hook = renderSync({ pollIntervalMs: 50, reloadTimeoutMs: 500 });
    await flushMicrotasks();
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));
    await act(async () => hook.result.current.apply());
    expect(vi.getTimerCount()).toBeGreaterThanOrEqual(2);

    hook.unmount();

    expect(vi.getTimerCount()).toBe(0);
    expect(windowRemove).toHaveBeenCalledWith("focus", expect.any(Function));
    expect(documentRemove).toHaveBeenCalledWith(
      "visibilitychange",
      expect.any(Function)
    );
  });

  it("preserves loaded data offline and recovers to dirty after a successful refresh", async () => {
    queueStates(
      stateSuccess(BASE_CONFIG),
      new Error("network down"),
      stateUnchanged()
    );
    const hook = renderSync();
    await waitForLoaded(hook);
    act(() => hook.result.current.changeField("recall_engine.top_k", 12));

    await act(async () => hook.result.current.refresh());
    expect(hook.result.current.status).toBe("offline");
    expect(hook.result.current.draft).toEqual({
      ...BASE_CONFIG,
      recall_engine: { top_k: 12, mode: "hybrid" },
    });

    await act(async () => hook.result.current.refresh());
    expect(hook.result.current.status).toBe("dirty");
    expect(hook.result.current.error).toBeNull();
  });

  it("distinguishes server protocol errors from transport offline failures", async () => {
    schemaHandler = () =>
      resolveReply(configError("schema_unavailable", "schema missing"));
    const protocolHook = renderSync();
    await waitForLoaded(protocolHook, "error");
    expect(protocolHook.result.current.error).toEqual({
      kind: "protocol",
      code: "schema_unavailable",
      message: "schema missing",
    });
    protocolHook.unmount();

    schemaHandler = () => Promise.reject(new Error("bridge disconnected"));
    const offlineHook = renderSync();
    await waitForLoaded(offlineHook, "offline");
    expect(offlineHook.result.current.error).toEqual({
      kind: "transport",
      message: "bridge disconnected",
    });
  });

  it("retries the full schema and state load after an initial transport failure", async () => {
    let schemaAttempts = 0;
    schemaHandler = () => {
      schemaAttempts += 1;
      return schemaAttempts === 1
        ? Promise.reject(new Error("bridge disconnected"))
        : resolveReply(schemaSuccess());
    };
    const hook = renderSync();
    await waitForLoaded(hook, "offline");

    expect(hook.result.current.schemaData).toBeNull();
    expect(hook.result.current.revision).toBeNull();

    await act(async () => hook.result.current.refresh());

    expect(hook.result.current.status).toBe("synced");
    expect(hook.result.current.schemaData).toEqual(schemaSuccess().data);
    expect(hook.result.current.draft).toEqual(BASE_CONFIG);
    expect(bridge.apiGet).toHaveBeenCalledWith("page/config/schema", {});
    expect(stateCalls()).toHaveLength(2);
  });
});
