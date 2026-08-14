import schemaSource from "virtual:memora-config-schema";

import type {
  ConfigApiError,
  ConfigApiResponse,
  ConfigApplyData,
  ConfigObject,
  ConfigProviderOptions,
  ConfigSchemaData,
  ConfigSchemaNode,
  ConfigStateData,
  JsonValue,
  PromptDefaults,
} from "@/types/config";
import { configEffectsForChangedPaths } from "@/lib/configRuntimeEffects";
import { MOCK_GATE_CONFIG, MOCK_PROMPT_DEFAULTS } from "./data";
const PLUGIN_NAME = "astrbot_plugin_memora";
const INITIAL_REVISION_SEQUENCE = 1;
const INITIAL_INSTANCE_SEQUENCE = 1;
const DANGEROUS_PATH_SEGMENTS = new Set([
   "__proto__",
   "prototype",
   "constructor",
 ]);

/**
 * quality.gate 复合分支路径：bindings/profiles 为对象数组，schema 只表达
 * enabled/default_profile 两个标量叶，复合值由后端 Pydantic 兜底校验，
 * mock 只做结构检查（必须为数组），不复制规则引擎。
 */
const GATE_COMPOSITE_PATHS: Record<string, true> = {
  "quality.gate.bindings": true,
  "quality.gate.profiles": true,
};

const DEFAULT_PROVIDER_OPTIONS: ConfigProviderOptions = {
  llm: [
    { id: "mock-llm-primary", label: "Mock GPT Primary" },
    { id: "mock-llm-fast", label: "Mock GPT Fast" },
  ],
  embedding: [
    { id: "mock-embedding-primary", label: "Mock Embedding Primary" },
  ],
};

const CONFIG_SCHEMA = JSON.parse(schemaSource) as Record<
  string,
  ConfigSchemaNode
>;

type ConfigEndpointData = ConfigSchemaData | ConfigStateData | ConfigApplyData;
type ConfigEndpointResponse = ConfigApiResponse<ConfigEndpointData>;

interface LeafSchemaEntry {
  node: Exclude<ConfigSchemaNode, { type: "object" }>;
}

export interface MockConfigServerOptions {
  disconnectDuringReload?: boolean;
  autoCompleteReloadMs?: number;
  hotReload?: boolean;
  providerOptions?: ConfigProviderOptions;
  promptDefaults?: PromptDefaults;
}

export interface MockConfigServerSnapshot {
  config: ConfigObject;
  persistedConfig: ConfigObject;
  revision: string;
  instanceId: string;
  pendingReload: boolean;
}

interface MutableConfigState {
  config: ConfigObject;
  persistedConfig: ConfigObject;
  revision: string;
  revisionSequence: number;
  instanceId: string;
  instanceSequence: number;
  pendingReload: boolean;
  reloadDisconnected: boolean;
  nextPersistenceFailure: string | null;
}

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function hasOwn(value: object, key: PropertyKey): boolean {
  return Object.prototype.hasOwnProperty.call(value, key);
}

function success<T>(data: T): ConfigApiResponse<T> {
  return { status: "ok", data };
}

function error(
  code: ConfigApiError["code"],
  message: string,
  data?: ConfigApiError["data"]
): ConfigApiError {
  return {
    status: "error",
    code,
    message,
    ...(data ? { data: cloneJson(data) } : {}),
  };
}

function revisionId(sequence: number): string {
  return `mock-revision-${String(sequence).padStart(8, "0")}`;
}

function instanceId(sequence: number): string {
  return `mock-instance-${String(sequence).padStart(4, "0")}`;
}

function defaultLeafValue(node: Exclude<ConfigSchemaNode, { type: "object" }>) {
  if (hasOwn(node, "default")) return cloneJson(node.default);
  if (node.type === "bool") return false;
  if (node.type === "int" || node.type === "float") return 0;
  return "";
}

function buildDefaultConfig(
  schema: Record<string, ConfigSchemaNode>
): ConfigObject {
  const config: ConfigObject = {};
  for (const [key, node] of Object.entries(schema)) {
    config[key] =
      node.type === "object"
        ? buildDefaultConfig(node.items)
        : defaultLeafValue(node);
  }
  return config;
}

function collectLeafSchemas(
  schema: Record<string, ConfigSchemaNode>,
  prefix = "",
  leaves = new Map<string, LeafSchemaEntry>()
): Map<string, LeafSchemaEntry> {
  for (const [key, node] of Object.entries(schema)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (node.type === "object") {
      collectLeafSchemas(node.items, path, leaves);
    } else {
      leaves.set(path, { node });
    }
  }
  return leaves;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSafePath(path: string): boolean {
  const segments = path.split(".");
  return (
    path.length > 0 &&
    segments.every(
      (segment) =>
        segment.length > 0 && !DANGEROUS_PATH_SEGMENTS.has(segment)
    )
  );
}

function expectedTypeMessage(node: LeafSchemaEntry["node"]): string {
  if (node.type === "bool") return "Expected a boolean";
  if (node.type === "int") return "Expected an integer";
  if (node.type === "float") return "Expected a finite number";
  return "Expected a string";
}

function matchesSchemaType(
  node: LeafSchemaEntry["node"],
  value: unknown
): boolean {
  if (node.type === "bool") return typeof value === "boolean";
  if (node.type === "int") return Number.isInteger(value);
  if (node.type === "float") {
    return typeof value === "number" && Number.isFinite(value);
  }
  return typeof value === "string";
}

function validateChanges(
  changes: Record<string, unknown>,
  leaves: ReadonlyMap<string, LeafSchemaEntry>
): Record<string, string> {
  const fieldErrors = Object.create(null) as Record<string, string>;
  for (const path of Object.keys(changes).sort()) {
    // quality.gate 复合分支（bindings/profiles 数组）由后端 Pydantic 校验，
    // mock 只检查值是数组，不复制规则引擎语义。
    if (GATE_COMPOSITE_PATHS[path] === true) {
      if (!Array.isArray(changes[path])) {
        fieldErrors[path] = "Expected an array";
      }
      continue;
    }
    const entry = isSafePath(path) ? leaves.get(path) : undefined;
    if (!entry) {
      fieldErrors[path] = "Path is not in the AstrBot schema";
      continue;
    }

    const value = changes[path];
    if (!matchesSchemaType(entry.node, value)) {
      fieldErrors[path] = expectedTypeMessage(entry.node);
      continue;
    }

    if (
      typeof value === "number" &&
      entry.node.min !== undefined &&
      value < entry.node.min
    ) {
      fieldErrors[path] = `Value must be at least ${entry.node.min}`;
      continue;
    }
    if (
      typeof value === "number" &&
      entry.node.max !== undefined &&
      value > entry.node.max
    ) {
      fieldErrors[path] = `Value must be at most ${entry.node.max}`;
      continue;
    }

    if (
      entry.node.options &&
      !entry.node.options.some((option) => Object.is(option, value))
    ) {
      fieldErrors[path] = `Value must be one of: ${entry.node.options.join(", ")}`;
    }
  }
  return fieldErrors;
}

function setConfigValue(
  config: ConfigObject,
  path: string,
  value: JsonValue
): void {
  const segments = path.split(".");
  let current = config;
  for (const segment of segments.slice(0, -1)) {
    const nested = current[segment];
    if (!isRecord(nested)) {
      current[segment] = {};
    }
    current = current[segment] as ConfigObject;
  }
  current[segments[segments.length - 1]] = cloneJson(value);
}

function applyChanges(
  config: ConfigObject,
  changes: Record<string, JsonValue>
): ConfigObject {
  const candidate = cloneJson(config);
  for (const path of Object.keys(changes).sort()) {
    setConfigValue(candidate, path, changes[path]);
  }
  return candidate;
}

function configEquals(left: ConfigObject, right: ConfigObject): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function makeInitialState(): MutableConfigState {
  const config = buildDefaultConfig(CONFIG_SCHEMA);
  // 门禁复合配置（profiles/bindings/rules）不在 schema 中逐叶表达，
  // 初始 state 直接并入样例数据，与后端 config 对象结构一致。
  config.quality = { gate: cloneJson(MOCK_GATE_CONFIG) };
  return {
    config,
    persistedConfig: cloneJson(config),
    revision: revisionId(INITIAL_REVISION_SEQUENCE),
    revisionSequence: INITIAL_REVISION_SEQUENCE,
    instanceId: instanceId(INITIAL_INSTANCE_SEQUENCE),
    instanceSequence: INITIAL_INSTANCE_SEQUENCE,
    pendingReload: false,
    reloadDisconnected: false,
    nextPersistenceFailure: null,
  };
}

export function createMockConfigServer(options: MockConfigServerOptions = {}) {
  const leaves = collectLeafSchemas(CONFIG_SCHEMA);
  const hotReload = options.hotReload ?? true;
  const disconnectDuringReload = options.disconnectDuringReload ?? false;
  const providerOptions = cloneJson(
    options.providerOptions ?? DEFAULT_PROVIDER_OPTIONS
  );
  const promptDefaults = cloneJson(
    options.promptDefaults ?? MOCK_PROMPT_DEFAULTS
  );
  let state = makeInitialState();
  let reloadTimer: ReturnType<typeof setTimeout> | null = null;

  const clearReloadTimer = () => {
    if (reloadTimer !== null) {
      clearTimeout(reloadTimer);
      reloadTimer = null;
    }
  };

  const snapshot = (): MockConfigServerSnapshot => ({
    config: cloneJson(state.config),
    persistedConfig: cloneJson(state.persistedConfig),
    revision: state.revision,
    instanceId: state.instanceId,
    pendingReload: state.pendingReload,
  });

  const completeReload = () => {
    clearReloadTimer();
    if (state.pendingReload) {
      state.instanceSequence += 1;
      state.instanceId = instanceId(state.instanceSequence);
    }
    state.pendingReload = false;
    state.reloadDisconnected = false;
    return { instanceId: state.instanceId };
  };

  const scheduleReload = (): boolean => {
    if (!hotReload) return false;
    state.pendingReload = true;
    state.reloadDisconnected = disconnectDuringReload;
    if (options.autoCompleteReloadMs !== undefined) {
      clearReloadTimer();
      reloadTimer = setTimeout(
        completeReload,
        Math.max(0, options.autoCompleteReloadMs)
      );
    }
    return true;
  };

  const commitCandidate = (candidate: ConfigObject) => {
    if (!configEquals(candidate, state.config)) {
      state.revisionSequence += 1;
      state.revision = revisionId(state.revisionSequence);
    }
    state.config = cloneJson(candidate);
    state.persistedConfig = cloneJson(candidate);
  };

  const handleSchema = (): ConfigApiResponse<ConfigSchemaData> =>
    success({
      plugin_name: PLUGIN_NAME,
      schema: cloneJson(CONFIG_SCHEMA),
      provider_options: cloneJson(providerOptions),
      capabilities: { hot_reload: hotReload },
    });

  const handleState = (
    params: Record<string, string>
  ): ConfigApiResponse<ConfigStateData> => {
    if (state.reloadDisconnected) {
      throw new Error("Mock plugin is reloading");
    }
    const changed = params.revision !== state.revision;
    return success(
      changed
        ? {
            revision: state.revision,
            instance_id: state.instanceId,
            changed: true,
            config: cloneJson(state.config),
            prompt_defaults: cloneJson(promptDefaults),
          }
        : {
            revision: state.revision,
            instance_id: state.instanceId,
            changed: false,
            prompt_defaults: cloneJson(promptDefaults),
          }
    );
  };

  const handleApply = (body: unknown): ConfigEndpointResponse => {
    if (!isRecord(body)) {
      return error("invalid_request", "Request body must be a JSON object");
    }
    if (
      Object.keys(body).sort().join(",") !== "base_revision,changes" ||
      typeof body.base_revision !== "string" ||
      body.base_revision.trim().length === 0 ||
      !isRecord(body.changes)
    ) {
      return error(
        "invalid_request",
        "Request must contain base_revision and changes"
      );
    }
    if (body.base_revision !== state.revision) {
      return error(
        "config_conflict",
        "Configuration has changed in AstrBot",
        { current_revision: state.revision }
      );
    }

    const changes = body.changes;
    const fieldErrors = validateChanges(changes, leaves);
    if (Object.keys(fieldErrors).length > 0) {
      return error(
        "validation_failed",
        "Configuration validation failed",
        { field_errors: fieldErrors }
      );
    }

    const normalizedChanges = changes as Record<string, JsonValue>;
    const candidate = applyChanges(state.config, normalizedChanges);
    if (state.nextPersistenceFailure) {
      const message = state.nextPersistenceFailure;
      state.nextPersistenceFailure = null;
      return error("persist_failed", message);
    }

    commitCandidate(candidate);
    const changedPaths = Object.keys(normalizedChanges).sort();
    const reloadScheduled =
      changedPaths.length > 0 ? scheduleReload() : false;
    const runtimeEffects = configEffectsForChangedPaths(changedPaths);
    return success({
      revision: state.revision,
      changed_paths: changedPaths,
      reload_scheduled: reloadScheduled,
      restart_required: runtimeEffects.restartRequired,
      rebuild_required: runtimeEffects.rebuildRequired,
      instance_id: state.instanceId,
    });
  };

  return {
    handleGet(
      path: string,
      params: Record<string, string> = {}
    ): ConfigEndpointResponse | undefined {
      if (path === "config/schema") return handleSchema();
      if (path === "config/state") return handleState(params);
      return undefined;
    },
    handlePost(path: string, body: unknown = {}): ConfigEndpointResponse | undefined {
      if (path === "config/apply") return handleApply(body);
      return undefined;
    },
    controls: {
      snapshot,
      reset() {
        clearReloadTimer();
        state = makeInitialState();
      },
      failNextPersistence(message = "Mock configuration persistence failed") {
        state.nextPersistenceFailure = message;
      },
      applyExternalChanges(changes: Record<string, JsonValue>) {
        const fieldErrors = validateChanges(changes, leaves);
        if (Object.keys(fieldErrors).length > 0) {
          throw new Error(JSON.stringify(fieldErrors));
        }
        commitCandidate(applyChanges(state.config, changes));
        return { revision: state.revision };
      },
      completeReload,
    },
  };
}
