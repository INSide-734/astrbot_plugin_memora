import { describe, expect, it } from "vitest";

import type {
  ConfigApiResponse,
  ConfigApplyData,
  ConfigObject,
  ConfigSchemaData,
  ConfigSchemaNode,
  ConfigStateData,
} from "@/types/config";

import { createMockConfigServer } from "./configServer";
import { MOCK_PROMPT_DEFAULTS } from "./data";
import { handleApiGet, handleApiPost } from "./server";

function successData<T>(response: ConfigApiResponse<T>): T {
  expect(response.status).toBe("ok");
  if (response.status !== "ok") {
    throw new Error(`Expected success, received ${response.code}`);
  }
  return response.data;
}

function countSchemaLeaves(schema: Record<string, ConfigSchemaNode>): number {
  return Object.values(schema).reduce(
    (count, node) =>
      count +
      (node.type === "object" ? countSchemaLeaves(node.items) : 1),
    0
  );
}

function fullState(server: ReturnType<typeof createMockConfigServer>) {
  const response = server.handleGet("config/state");
  expect(response).toBeDefined();
  const data = successData(response as ConfigApiResponse<ConfigStateData>);
  expect(data.changed).toBe(true);
  if (!data.changed) throw new Error("Expected a full config snapshot");
  return data;
}

describe("createMockConfigServer", () => {
  it("serves the complete authoritative schema with providers and isolated copies", () => {
    const server = createMockConfigServer();

    const first = successData(
      server.handleGet("config/schema") as ConfigApiResponse<ConfigSchemaData>
    );

    expect(Object.keys(first.schema)).toHaveLength(42);
    expect(countSchemaLeaves(first.schema)).toBe(229);
    expect(first.schema).not.toHaveProperty("index_management");
    expect(first.plugin_name).toBe("astrbot_plugin_memora");
    expect(first.provider_options).toEqual({
      llm: [
        { id: "mock-llm-primary", label: "Mock GPT Primary" },
        { id: "mock-llm-fast", label: "Mock GPT Fast" },
      ],
      embedding: [
        { id: "mock-embedding-primary", label: "Mock Embedding Primary" },
      ],
    });
    expect(first.capabilities).toEqual({ hot_reload: true });
    expect(first.schema.provider_settings).toMatchObject({
      type: "object",
      description: "模型提供商",
      items: {
        llm_provider_id: {
          type: "string",
          _special: "select_provider",
          default: "",
        },
      },
    });

    first.schema.provider_settings.description = "mutated response";
    const second = successData(
      server.handleGet("config/schema") as ConfigApiResponse<ConfigSchemaData>
    );
    expect(second.schema.provider_settings.description).toBe("模型提供商");
  });

  it("returns conditional state and keeps returned config snapshots isolated", () => {
    const server = createMockConfigServer();
    const initial = fullState(server);
    const config = initial.config as ConfigObject;
    expect(config.recall_engine).toMatchObject({
      top_k: 5,
      pre_llm_soft_budget_ms: 800,
    });

    (config.recall_engine as ConfigObject).top_k = 999;

    const unchanged = successData(
      server.handleGet("config/state", {
        revision: initial.revision,
      }) as ConfigApiResponse<ConfigStateData>
    );
    expect(unchanged).toEqual({
      revision: initial.revision,
      instance_id: initial.instance_id,
      changed: false,
      prompt_defaults: MOCK_PROMPT_DEFAULTS,
    });
    expect(
      (fullState(server).config.recall_engine as ConfigObject).top_k
    ).toBe(5);
  });

  it("exposes quality.gate scalar leaves and seeds the composite gate config", () => {
    const server = createMockConfigServer();
    const schema = successData(
      server.handleGet("config/schema") as ConfigApiResponse<ConfigSchemaData>
    );
    const quality = schema.schema.quality as ConfigSchemaNode & {
      items: Record<string, ConfigSchemaNode>;
    };
    const gate = quality.items.gate as ConfigSchemaNode & {
      items: Record<string, ConfigSchemaNode>;
    };
    expect(gate.items.enabled).toMatchObject({ type: "bool", default: true });
    expect(gate.items.default_profile).toMatchObject({
      type: "string",
      default: "private",
    });

    const state = fullState(server);
    const gateConfig = (state.config.quality as ConfigObject)
      .gate as ConfigObject;
    expect(gateConfig).toMatchObject({
      enabled: true,
      default_profile: "private",
    });
    expect(gateConfig.bindings).toHaveLength(2);
    expect(gateConfig.profiles).toHaveLength(2);
  });

  it("applies quality.gate composite changes and rejects non-array values", () => {
    const server = createMockConfigServer();
    const initial = fullState(server);
    const gateConfig = (initial.config.quality as ConfigObject)
      .gate as ConfigObject;

    const applied = successData(
      server.handlePost("config/apply", {
        base_revision: initial.revision,
        changes: {
          "quality.gate.enabled": false,
          "quality.gate.profiles": gateConfig.profiles,
        },
      }) as ConfigApiResponse<ConfigApplyData>
    );
    expect(applied.changed_paths).toEqual([
      "quality.gate.enabled",
      "quality.gate.profiles",
    ]);
    expect(applied.revision).not.toBe(initial.revision);

    const snapshot = server.controls.snapshot();
    expect((snapshot.config.quality as ConfigObject).gate).toMatchObject({
      enabled: false,
    });

    const invalid = server.handlePost("config/apply", {
      base_revision: applied.revision,
      changes: { "quality.gate.bindings": { not: "array" } },
    }) as ConfigApiResponse<ConfigApplyData>;
    expect(invalid.status).toBe("error");
    if (invalid.status === "error") {
      expect(invalid.code).toBe("validation_failed");
      expect(invalid.data?.field_errors).toEqual({
        "quality.gate.bindings": "Expected an array",
      });
    }
  });


  it("applies valid leaf changes to memory and persisted state with a new revision", () => {
    const server = createMockConfigServer();
    const initial = fullState(server);

    const applied = successData(
      server.handlePost("config/apply", {
        base_revision: initial.revision,
        changes: {
          "provider_settings.llm_provider_id": "mock-llm-primary",
          "recall_engine.top_k": 9,
        },
      }) as ConfigApiResponse<ConfigApplyData>
    );

    expect(applied).toMatchObject({
      changed_paths: [
        "provider_settings.llm_provider_id",
        "recall_engine.top_k",
      ],
      instance_id: initial.instance_id,
      reload_scheduled: true,
    });
    expect(applied.revision).not.toBe(initial.revision);

    const snapshot = server.controls.snapshot();
    expect(snapshot.revision).toBe(applied.revision);
    expect(snapshot.config).toEqual(snapshot.persistedConfig);
    expect(snapshot.config.provider_settings).toMatchObject({
      llm_provider_id: "mock-llm-primary",
    });
    expect(snapshot.config.recall_engine).toMatchObject({ top_k: 9 });
    expect(snapshot.pendingReload).toBe(true);
  });

  it("rejects stale revisions without overwriting an external AstrBot change", () => {
    const server = createMockConfigServer();
    const initial = fullState(server);

    const external = server.controls.applyExternalChanges({
      "recall_engine.top_k": 7,
    });
    const response = server.handlePost("config/apply", {
      base_revision: initial.revision,
      changes: { "recall_engine.top_k": 12 },
    });

    expect(response).toEqual({
      status: "error",
      code: "config_conflict",
      message: "Configuration has changed in AstrBot",
      data: { current_revision: external.revision },
    });
    expect(server.controls.snapshot().config.recall_engine).toMatchObject({
      top_k: 7,
    });
  });

  it("reports every unknown or mistyped schema leaf without mutating state", () => {
    const server = createMockConfigServer();
    const initial = server.controls.snapshot();

    const response = server.handlePost("config/apply", {
      base_revision: initial.revision,
      changes: {
        "recall_engine.top_k": "nine",
        "recall_engine.not_a_field": true,
        bot_language: "not-a-language",
      },
    });

    expect(response).toEqual({
      status: "error",
      code: "validation_failed",
      message: "Configuration validation failed",
      data: {
        field_errors: {
          bot_language: "Value must be one of: zh, en, ru",
          "recall_engine.not_a_field": "Path is not in the AstrBot schema",
          "recall_engine.top_k": "Expected an integer",
        },
      },
    });
    expect(server.controls.snapshot()).toEqual(initial);
  });

  it.each([
    ["recall_engine.top_k", -1, "Value must be at least 0"],
    ["recall_engine.top_k", 51, "Value must be at most 50"],
    [
      "graph_memory.cross_route_bonus",
      0.51,
      "Value must be at most 0.5",
    ],
  ] as const)(
    "rejects %s=%s outside its schema numeric bounds",
    (path, value, expectedError) => {
      const server = createMockConfigServer();
      const before = server.controls.snapshot();

      const response = server.handlePost("config/apply", {
        base_revision: before.revision,
        changes: { [path]: value },
      });

      expect(response?.status).toBe("error");
      if (response?.status !== "error") {
        throw new Error("Expected the numeric boundary to be enforced");
      }
      expect(response.code).toBe("validation_failed");
      expect(response.data?.field_errors).toEqual({
        [path]: expectedError,
      });
      expect(server.controls.snapshot()).toEqual(before);
    }
  );

  it("accepts values exactly on inclusive numeric boundaries", () => {
    const server = createMockConfigServer({ hotReload: false });
    const before = server.controls.snapshot();

    const response = server.handlePost("config/apply", {
      base_revision: before.revision,
      changes: {
        "graph_memory.cross_route_bonus": 0.5,
        "recall_engine.top_k": 0,
      },
    });

    expect(response?.status).toBe("ok");
    expect(server.controls.snapshot().config).toMatchObject({
      graph_memory: { cross_route_bonus: 0.5 },
      recall_engine: { top_k: 0 },
    });
  });

  it.each([
    "__proto__",
    "prototype",
    "constructor",
    "recall_engine.__proto__",
  ])("rejects the dangerous path %s without scheduling reload", (path) => {
    const server = createMockConfigServer();
    const before = server.controls.snapshot();
    const changes = JSON.parse(`{"${path}":true}`) as Record<
      string,
      boolean
    >;

    const response = server.handlePost("config/apply", {
      base_revision: before.revision,
      changes,
    });

    expect(response?.status).toBe("error");
    if (response?.status !== "error") {
      throw new Error("Expected the unsafe path to be rejected");
    }
    expect(response.code).toBe("validation_failed");
    expect(Object.keys(response.data?.field_errors ?? {})).toEqual([path]);
    expect(response.data?.field_errors?.[path]).toBe(
      "Path is not in the AstrBot schema"
    );
    expect(server.controls.snapshot()).toEqual(before);
  });

  it("rolls back memory, persisted state, and revision after persistence failure", () => {
    const server = createMockConfigServer();
    const before = server.controls.snapshot();
    server.controls.failNextPersistence("mock disk full");

    const response = server.handlePost("config/apply", {
      base_revision: before.revision,
      changes: { "recall_engine.top_k": 11 },
    });

    expect(response).toEqual({
      status: "error",
      code: "persist_failed",
      message: "mock disk full",
    });
    expect(server.controls.snapshot()).toEqual(before);
  });

  it("exposes external revision changes through the conditional state contract", () => {
    const server = createMockConfigServer();
    const initial = fullState(server);

    const external = server.controls.applyExternalChanges({
      "recall_engine.max_chain_hops": 2,
    });
    const remote = successData(
      server.handleGet("config/state", {
        revision: initial.revision,
      }) as ConfigApiResponse<ConfigStateData>
    );

    expect(remote.changed).toBe(true);
    expect(remote.revision).toBe(external.revision);
    if (!remote.changed) throw new Error("Expected the external config snapshot");
    expect(remote.config.recall_engine).toMatchObject({ max_chain_hops: 2 });
  });

  it("simulates reload disconnect and completion with a changed instance id", () => {
    const server = createMockConfigServer({ disconnectDuringReload: true });
    const initial = fullState(server);
    const applied = successData(
      server.handlePost("config/apply", {
        base_revision: initial.revision,
        changes: { "recall_engine.top_k": 10 },
      }) as ConfigApiResponse<ConfigApplyData>
    );

    expect(() =>
      server.handleGet("config/state", { revision: applied.revision })
    ).toThrow("Mock plugin is reloading");

    const completed = server.controls.completeReload();
    expect(completed.instanceId).not.toBe(initial.instance_id);
    const state = successData(
      server.handleGet("config/state", {
        revision: applied.revision,
      }) as ConfigApiResponse<ConfigStateData>
    );
    expect(state).toEqual({
      revision: applied.revision,
      instance_id: completed.instanceId,
      changed: false,
      prompt_defaults: MOCK_PROMPT_DEFAULTS,
    });
  });

  it("keeps factory instances independent and reset restores deterministic defaults", () => {
    const first = createMockConfigServer();
    const second = createMockConfigServer();
    const initial = first.controls.snapshot();

    first.controls.applyExternalChanges({ "recall_engine.top_k": 17 });

    expect(first.controls.snapshot().config.recall_engine).toMatchObject({
      top_k: 17,
    });
    expect(second.controls.snapshot()).toEqual(initial);
    first.controls.reset();
    expect(first.controls.snapshot()).toEqual(initial);
  });
});

describe("mock API config routes", () => {
  it("wires schema, conditional state, and revision-guarded apply", async () => {
    const schema = await handleApiGet("page/config/schema");
    expect(schema.status).toBe("ok");
    expect((schema.data as ConfigSchemaData).plugin_name).toBe(
      "astrbot_plugin_memora"
    );

    const firstState = await handleApiGet("page/config/state");
    expect(firstState.status).toBe("ok");
    const state = firstState.data as ConfigStateData;
    expect(state.changed).toBe(true);

    const unchanged = await handleApiGet("page/config/state", {
      revision: state.revision,
    });
    expect(unchanged.data).toEqual({
      revision: state.revision,
      instance_id: state.instance_id,
      changed: false,
      prompt_defaults: MOCK_PROMPT_DEFAULTS,
    });

    const applied = await handleApiPost("page/config/apply", {
      base_revision: state.revision,
      changes: { "recall_engine.top_k": 8 },
    });
    expect(applied.status).toBe("ok");
    expect(applied.data).toMatchObject({
      changed_paths: ["recall_engine.top_k"],
      reload_scheduled: true,
    });
  });
});
