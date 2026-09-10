import { readFileSync } from "node:fs";

const schemaSource = readFileSync(
  new URL("../../../_conf_schema.json", import.meta.url),
  "utf8",
);
const CONFIG_SCHEMA = JSON.parse(schemaSource);
const PROVIDER_OPTIONS = {
  llm: [
    { id: "mock-llm-primary", label: "Mock GPT Primary" },
    { id: "mock-llm-fast", label: "Mock GPT Fast" },
  ],
  embedding: [
    { id: "mock-embedding-primary", label: "Mock Embedding Primary" },
  ],
};

function cloneJson(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function defaultLeafValue(node) {
  if (Object.prototype.hasOwnProperty.call(node, "default")) {
    return cloneJson(node.default);
  }
  if (node.type === "bool") return false;
  if (node.type === "int" || node.type === "float") return 0;
  return "";
}

function buildDefaultConfig(schema) {
  const config = {};
  for (const [key, node] of Object.entries(schema)) {
    config[key] = node.type === "object"
      ? buildDefaultConfig(node.items)
      : defaultLeafValue(node);
  }
  return config;
}

function collectLeaves(schema, prefix = "", leaves = new Map()) {
  for (const [key, node] of Object.entries(schema)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (node.type === "object") {
      collectLeaves(node.items, path, leaves);
    } else {
      leaves.set(path, node);
    }
  }
  return leaves;
}

function setConfigValue(config, path, value) {
  const segments = path.split(".");
  let current = config;
  for (const segment of segments.slice(0, -1)) {
    if (!current[segment] || typeof current[segment] !== "object") {
      current[segment] = {};
    }
    current = current[segment];
  }
  current[segments[segments.length - 1]] = cloneJson(value);
}

function isValidValue(node, value) {
  if (node.type === "bool" && typeof value !== "boolean") return false;
  if (node.type === "int" && !Number.isInteger(value)) return false;
  if (node.type === "float" && (typeof value !== "number" || !Number.isFinite(value))) {
    return false;
  }
  if (node.type === "string" && typeof value !== "string") return false;
  if (typeof value === "number" && node.min !== undefined && value < node.min) return false;
  if (typeof value === "number" && node.max !== undefined && value > node.max) return false;
  return !node.options || node.options.some((option) => Object.is(option, value));
}

function responseError(code, message, data = undefined) {
  return {
    __memoraEditingResponse: {
      status: "error",
      code,
      message,
      ...(data ? { data: cloneJson(data) } : {}),
    },
  };
}

function revisionId(sequence) {
  return `browser-smoke-config-${String(sequence).padStart(4, "0")}`;
}

function instanceId(sequence) {
  return `browser-smoke-instance-${String(sequence).padStart(4, "0")}`;
}

export function createConfigSmokeFixture() {
  const leaves = collectLeaves(CONFIG_SCHEMA);
  let config = buildDefaultConfig(CONFIG_SCHEMA);
  let revisionSequence = 0;
  let instanceSequence = 0;
  let revision = "browser-smoke-config";
  let instance = "browser-smoke-instance";
  let reloadUntil = 0;

  function completeReloadIfReady() {
    if (!reloadUntil || Date.now() < reloadUntil) return;
    reloadUntil = 0;
    instanceSequence += 1;
    instance = instanceId(instanceSequence);
  }

  function stateResponse(params = {}) {
    completeReloadIfReady();
    if (reloadUntil) throw new Error("Mock plugin is reloading");
    const changed = params.revision !== revision;
    return changed
      ? {
          revision,
          instance_id: instance,
          changed: true,
          config: cloneJson(config),
        }
      : {
          revision,
          instance_id: instance,
          changed: false,
        };
  }

  function applyResponse(body = {}) {
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      return responseError("invalid_request", "Request body must be an object");
    }
    if (body.base_revision !== revision) {
      return responseError(
        "config_conflict",
        "Configuration has changed in AstrBot",
        { current_revision: revision },
      );
    }
    const changes = body.changes;
    if (!changes || typeof changes !== "object" || Array.isArray(changes)) {
      return responseError("invalid_request", "Request must contain changes");
    }
    const fieldErrors = {};
    for (const [path, value] of Object.entries(changes)) {
      const node = leaves.get(path);
      if (!node) {
        fieldErrors[path] = "Path is not in the AstrBot schema";
      } else if (!isValidValue(node, value)) {
        fieldErrors[path] = "Configuration value does not match the schema";
      }
    }
    if (Object.keys(fieldErrors).length > 0) {
      return responseError("validation_failed", "Configuration validation failed", {
        field_errors: fieldErrors,
      });
    }

    const changedPaths = Object.keys(changes).sort();
    for (const path of changedPaths) setConfigValue(config, path, changes[path]);
    revisionSequence += 1;
    revision = revisionId(revisionSequence);
    reloadUntil = Date.now() + 550;
    return {
      revision,
      changed_paths: changedPaths,
      reload_scheduled: changedPaths.length > 0,
      instance_id: instance,
    };
  }

  return {
    handle(method, path, payload = {}) {
      if (method === "GET" && path === "config/schema") {
        return {
          plugin_name: "astrbot_plugin_memora",
          schema: cloneJson(CONFIG_SCHEMA),
          provider_options: cloneJson(PROVIDER_OPTIONS),
          capabilities: { hot_reload: true },
        };
      }
      if (method === "GET" && path === "config/state") {
        return stateResponse(payload);
      }
      if (method === "POST" && path === "config/apply") {
        return applyResponse(payload);
      }
      return undefined;
    },
  };
}
