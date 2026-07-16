import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ConfigApiResponse,
  ConfigApplyData,
  ConfigObject,
  ConfigSchemaData,
  ConfigStateData,
} from "@/types/config";
import type { InjectionStrategyCatalog } from "@/types/injection";

import { DEFAULT_INJECTION_STRATEGY } from "@/types/injection";
import { useInjectionStrategyConfig } from "./useInjectionStrategyConfig";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
}

const STRATEGY_CONFIG: ConfigObject = {
  recall_engine: {
    injection_routing_mode: "manual",
    injection_manual_preset: "balanced",
    injection_auto_fallback_preset: "balanced",
    injection_hybrid_base_preset: "balanced",
    injection_hybrid_min_preset: "low_cost",
    injection_hybrid_max_preset: "quality",
    injection_delivery_override: "auto",
    injection_preset_overrides_enabled: false,
    injection_budget_chars: 0,
    injection_memory_max_chars: 0,
    injection_metadata_max_chars: 0,
    injection_include_key_facts: true,
    injection_include_topics: true,
    injection_include_participants: false,
    injection_compact_header: true,
    injection_decision_retention_days: 30,
    injection_decision_max_rows: 100_000,
  },
};

const CATALOG: InjectionStrategyCatalog = {
  routing_modes: ["manual", "auto", "hybrid"],
  presets: [
    {
      name: "balanced",
      rank: 2,
      auto_inject: true,
      memory_budget_chars: 4_000,
      max_memories: 8,
      content_level: "COMPACT",
      cost_penalty_weight: 0.25,
      minimum_utility: 0.2,
      allow_tool_fallback: true,
      preferred_delivery: "extra_user_content",
    },
  ],
  deliveries: ["auto", "extra_user_content"],
  retention_options: [7, 30, 90, 180, 0],
  provider_tools_supported: true,
  memory_tool_available: true,
  recall_trace_available: true,
  effective_default_delivery: "extra_user_content",
};

function ok<T>(data: T): ConfigApiResponse<T> {
  return { status: "ok", data };
}

function schemaSuccess(): ConfigApiResponse<ConfigSchemaData> {
  return ok({
    plugin_name: "astrbot_plugin_memora",
    schema: {},
    provider_options: { llm: [], embedding: [] },
    capabilities: { hot_reload: true },
  });
}

function stateSuccess(
  config: ConfigObject = STRATEGY_CONFIG,
  revision = "rev-1"
): ConfigApiResponse<ConfigStateData> {
  return ok({
    revision,
    instance_id: "instance-1",
    changed: true,
    config,
  });
}

function applySuccess(): ConfigApiResponse<ConfigApplyData> {
  return ok({
    revision: "rev-2",
    changed_paths: [
      "recall_engine.injection_hybrid_max_preset",
      "recall_engine.injection_routing_mode",
    ],
    reload_scheduled: false,
    instance_id: "instance-1",
  });
}

describe("useInjectionStrategyConfig", () => {
  let bridge: BridgeMock;

  beforeEach(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    bridge = {
      apiGet: vi.fn((endpoint: string) => {
        if (endpoint === "page/config/schema") {
          return Promise.resolve(schemaSuccess() as ApiResponse);
        }
        if (endpoint === "page/config/state") {
          return Promise.resolve(stateSuccess() as ApiResponse);
        }
        if (endpoint === "page/injection-strategy/catalog") {
          return Promise.resolve(ok(CATALOG) as ApiResponse);
        }
        return Promise.reject(new Error(`Unexpected GET endpoint: ${endpoint}`));
      }),
      apiPost: vi.fn((endpoint: string) => {
        if (endpoint === "page/config/apply") {
          return Promise.resolve(applySuccess() as ApiResponse);
        }
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
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  it("projects strategy fields and saves only their changed leaf paths", async () => {
    const hook = renderHook(() => useInjectionStrategyConfig());
    await waitFor(() => expect(hook.result.current.status).toBe("synced"));
    expect(hook.result.current.draft).toEqual(DEFAULT_INJECTION_STRATEGY);

    act(() => {
      hook.result.current.change("routingMode", "hybrid");
      hook.result.current.change("hybridMaxPreset", "balanced");
    });
    await act(async () => hook.result.current.save());

    expect(bridge.apiPost).toHaveBeenCalledWith("page/config/apply", {
      base_revision: "rev-1",
      changes: {
        "recall_engine.injection_hybrid_max_preset": "balanced",
        "recall_engine.injection_routing_mode": "hybrid",
      },
    });
    expect(JSON.stringify(bridge.apiPost.mock.calls)).not.toContain(
      "injection_method"
    );
  });

  it("rejects an invalid hybrid order before save", async () => {
    const hook = renderHook(() => useInjectionStrategyConfig());
    await waitFor(() => expect(hook.result.current.status).toBe("synced"));
    act(() => {
      hook.result.current.change("routingMode", "hybrid");
      hook.result.current.change("hybridMinPreset", "quality");
      hook.result.current.change("hybridMaxPreset", "low_cost");
    });
    expect(hook.result.current.errors.hybridMinPreset).toBeTruthy();
    expect(hook.result.current.canSave).toBe(false);
    await act(async () => hook.result.current.save());
    expect(bridge.apiPost).not.toHaveBeenCalled();
  });

  it("restores defaults locally and discards them without saving", async () => {
    const custom = {
      recall_engine: {
        ...(STRATEGY_CONFIG.recall_engine as ConfigObject),
        injection_manual_preset: "quality",
      },
    };
    bridge.apiGet.mockImplementation((endpoint: string) => {
      if (endpoint === "page/config/schema") {
        return Promise.resolve(schemaSuccess() as ApiResponse);
      }
      if (endpoint === "page/config/state") {
        return Promise.resolve(stateSuccess(custom) as ApiResponse);
      }
      if (endpoint === "page/injection-strategy/catalog") {
        return Promise.resolve(ok(CATALOG) as ApiResponse);
      }
      return Promise.reject(new Error(`Unexpected GET endpoint: ${endpoint}`));
    });
    const hook = renderHook(() => useInjectionStrategyConfig());
    await waitFor(() => expect(hook.result.current.status).toBe("synced"));

    act(() => hook.result.current.restoreDefaults());
    expect(hook.result.current.draft).toEqual(DEFAULT_INJECTION_STRATEGY);
    expect(hook.result.current.dirty).toBe(true);
    act(() => hook.result.current.discard());

    expect(hook.result.current.draft?.manualPreset).toBe("quality");
    expect(hook.result.current.dirty).toBe(false);
    expect(bridge.apiPost).not.toHaveBeenCalled();
  });
});
