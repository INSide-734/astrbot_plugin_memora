import path from "node:path";

function cloneJson(value) {
  if (value === undefined) return undefined;
  return JSON.parse(JSON.stringify(value));
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function requireTrace(condition, message) {
  if (!condition) throw new Error(`Invalid config runtime trace: ${message}`);
}

function sameJson(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function assertEditorReadiness(
  { visibleTitles = [], loadingOverlayVisible, fixedFooterVisible },
  { expectedTitle },
) {
  const issues = [];
  if (!visibleTitles.some((title) => String(title).trim() === expectedTitle)) {
    issues.push(`expected title "${expectedTitle}" is not visible`);
  }
  if (loadingOverlayVisible) {
    issues.push("loading overlay is still visible");
  }
  if (!fixedFooterVisible) {
    issues.push("fixed editor footer is not visible");
  }
  if (issues.length > 0) {
    throw new Error(`Editor is not ready: ${issues.join("; ")}`);
  }
}

export function assertDialogActions(
  visibleActions,
  expectedActions,
  label = "dialog",
) {
  const actions = new Set(visibleActions.map((action) => String(action).trim()));
  const missing = expectedActions.filter((action) => !actions.has(action));
  if (missing.length > 0) {
    throw new Error(`${label} is missing actions: ${missing.join(", ")}`);
  }
}

const EDITING_RUNTIME_GET_ENDPOINTS = [
  "page/social/relations",
  "page/profiles",
  "page/profiles/detail",
  "page/jargon/candidates",
  "page/jargon/meanings",
  "page/jargon/stats",
  "page/affection/status",
  "page/affection/users",
  "page/affection/moods/history",
];

const EDITING_RUNTIME_POST_ENDPOINTS = [
  "page/social/create",
  "page/social/update",
  "page/social/delete",
  "page/social/batch",
  "page/profiles/create",
  "page/profiles/update",
  "page/profiles/delete",
  "page/profiles/batch",
  "page/jargon/create",
  "page/jargon/update",
  "page/jargon/delete",
  "page/jargon/batch",
  "page/affection/users/create",
  "page/affection/users/update",
  "page/affection/users/delete",
  "page/affection/users/batch",
  "page/affection/mood/set",
  "page/affection/mood/reset",
];

export function assertEditingRuntimeCalls(calls) {
  for (const endpoint of EDITING_RUNTIME_GET_ENDPOINTS) {
    const call = calls.find(
      (candidate) => candidate.method === "GET"
        && candidate.endpoint === endpoint
        && candidate.response?.status === "ok",
    );
    if (!call || !call.params || typeof call.params !== "object" || Array.isArray(call.params)) {
      throw new Error(`Editing runtime smoke is missing a successful GET ${endpoint}`);
    }
  }

  for (const endpoint of EDITING_RUNTIME_POST_ENDPOINTS) {
    const call = calls.find(
      (candidate) => candidate.method === "POST"
        && candidate.endpoint === endpoint
        && candidate.response?.status === "ok",
    );
    if (!call || !call.body || typeof call.body !== "object" || Array.isArray(call.body)) {
      throw new Error(`Editing runtime smoke is missing a successful POST ${endpoint}`);
    }
  }

  const invalidScore = calls.find(
    (call) => call.method === "POST"
      && call.endpoint === "page/affection/users/create"
      && call.response?.code === "validation_error"
      && typeof call.response?.field_errors?.affection_score === "string",
  );
  if (!invalidScore) {
    throw new Error("Editing runtime smoke is missing affection_score validation_error coverage");
  }

  const staleConflict = calls.find(
    (call) => call.method === "POST"
      && call.response?.code === "edit_conflict"
      && call.response?.data?.current_entity
      && typeof call.response?.data?.current_revision === "string",
  );
  if (!staleConflict) {
    throw new Error("Editing runtime smoke is missing an edit_conflict current-entity envelope");
  }

  return {
    getEndpoints: EDITING_RUNTIME_GET_ENDPOINTS.length,
    postEndpoints: EDITING_RUNTIME_POST_ENDPOINTS.length,
  };
}

export function resolveRuntimeResourcePath(
  url,
  { runtimeOrigin, dashboardRoot },
) {
  let resourceUrl;
  let relativePath;
  try {
    resourceUrl = new URL(url);
    relativePath = decodeURIComponent(resourceUrl.pathname).replace(/^\/+/, "");
  } catch {
    return null;
  }
  if (resourceUrl.origin !== runtimeOrigin || !relativePath) return null;

  const localPath = path.resolve(dashboardRoot, relativePath);
  const relativeToRoot = path.relative(dashboardRoot, localPath);
  if (relativeToRoot.startsWith("..") || path.isAbsolute(relativeToRoot)) {
    return null;
  }
  return localPath;
}

export async function waitFor(
  predicate,
  { timeoutMs = 5_000, intervalMs = 25, description = "condition" } = {},
) {
  const deadline = Date.now() + timeoutMs;
  while (true) {
    const result = await predicate();
    if (result) return result;
    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) break;
    await new Promise((resolve) =>
      setTimeout(resolve, Math.min(intervalMs, remainingMs)),
    );
  }
  throw new Error(`Timed out waiting for ${description} after ${timeoutMs}ms`);
}

export function assertConfigRuntimeCalls(
  calls,
  { changedPath, changedValue },
) {
  const configCalls = calls.filter(
    (call) => String(call.endpoint).startsWith("page/config/"),
  );
  const schemaCall = configCalls.find(
    (call) => call.method === "GET" && call.endpoint === "page/config/schema",
  );
  requireTrace(schemaCall, "missing schema GET");
  requireTrace(
    sameJson(schemaCall.params, {}),
    "schema GET must preserve empty params",
  );
  requireTrace(
    schemaCall.response?.status === "ok"
      && schemaCall.response?.data?.plugin_name === "astrbot_plugin_memora",
    "schema GET did not return the Memora schema",
  );

  const initialStateCall = configCalls.find(
    (call) =>
      call.method === "GET"
      && call.endpoint === "page/config/state"
      && sameJson(call.params, {})
      && call.response?.status === "ok"
      && call.response?.data?.changed === true,
  );
  requireTrace(initialStateCall, "missing initial full state GET");
  requireTrace(
    Object.prototype.hasOwnProperty.call(initialStateCall.response.data, "config"),
    "initial state GET must include config",
  );
  const initialRevision = initialStateCall.response.data.revision;
  const initialInstanceId = initialStateCall.response.data.instance_id;

  const applyCalls = configCalls.filter(
    (call) => call.method === "POST" && call.endpoint === "page/config/apply",
  );
  requireTrace(
    applyCalls.length === 2,
    `expected exactly two UI apply POSTs, received ${applyCalls.length}`,
  );
  const staleApplyCalls = applyCalls.filter(
    (call) => call.body?.base_revision === initialRevision,
  );
  requireTrace(
    staleApplyCalls.length === 1,
    `expected exactly one stale UI apply POST before rebase, received ${staleApplyCalls.length}`,
  );
  const expectedChanges = { [changedPath]: changedValue };
  requireTrace(
    sameJson(applyCalls[0].body?.changes, expectedChanges),
    "stale apply did not preserve the edited config change",
  );
  requireTrace(
    applyCalls[0].body?.base_revision === initialRevision,
    "stale apply did not use the initial revision",
  );
  requireTrace(
    applyCalls[0].response?.status === "error"
      && applyCalls[0].response?.code === "config_conflict",
    "first apply was not rejected as a revision conflict",
  );
  requireTrace(
    /lost.*stale apply response/i.test(String(applyCalls[0].error ?? "")),
    "missing lost stale apply response transport error",
  );
  const conflictRevision = applyCalls[0].response?.data?.current_revision;
  requireTrace(
    typeof conflictRevision === "string" && conflictRevision !== initialRevision,
    "conflict response did not expose a newer revision",
  );

  requireTrace(
    sameJson(applyCalls[1].body?.changes, expectedChanges),
    "rebased apply did not preserve the edited config change",
  );
  requireTrace(
    applyCalls[1].body?.base_revision === conflictRevision,
    "rebased apply did not use the conflict revision",
  );
  requireTrace(
    applyCalls[1].response?.status === "ok"
      && applyCalls[1].response?.data?.reload_scheduled === true,
    "rebased apply did not schedule hot reload",
  );
  const appliedRevision = applyCalls[1].response.data.revision;
  const appliedInstanceId = applyCalls[1].response.data.instance_id;

  const finalStateCall = configCalls.find(
    (call) =>
      call.method === "GET"
      && call.endpoint === "page/config/state"
      && call.params?.revision === appliedRevision
      && call.response?.status === "ok"
      && call.response?.data?.changed === false,
  );
  requireTrace(finalStateCall, "missing unchanged conditional state GET after reload");
  const successfulApplyIndex = configCalls.indexOf(applyCalls[1]);
  const finalStateIndex = configCalls.indexOf(finalStateCall);
  const reloadDisconnectCall = configCalls
    .slice(successfulApplyIndex + 1, finalStateIndex)
    .find(
      (call) =>
        call.method === "GET"
        && call.endpoint === "page/config/state"
        && call.params?.revision === appliedRevision
        && /Mock plugin is reloading/i.test(String(call.error ?? "")),
    );
  requireTrace(reloadDisconnectCall, "missing reload disconnect before final state");
  requireTrace(
    !Object.prototype.hasOwnProperty.call(finalStateCall.response.data, "config"),
    "unchanged conditional state must omit config",
  );
  const finalInstanceId = finalStateCall.response.data.instance_id;
  requireTrace(
    typeof finalInstanceId === "string"
      && finalInstanceId !== initialInstanceId
      && finalInstanceId !== appliedInstanceId,
    "hot reload did not produce a changed instance_id",
  );

  return {
    initialRevision,
    conflictRevision,
    appliedRevision,
    initialInstanceId,
    finalInstanceId,
  };
}

export function instrumentRuntimeBridge(bridge, { afterPost } = {}) {
  if (
    !bridge
    || typeof bridge.apiGet !== "function"
    || typeof bridge.apiPost !== "function"
  ) {
    throw new TypeError("Runtime smoke requires an AstrBot bridge with apiGet/apiPost");
  }

  const calls = [];
  const originalGet = bridge.apiGet;
  const originalPost = bridge.apiPost;

  const recordedGet = async function recordedGet(endpoint, params = {}) {
    const call = {
      method: "GET",
      endpoint: String(endpoint),
      params: cloneJson(params),
    };
    calls.push(call);
    try {
      const response = await originalGet.call(this, endpoint, params);
      call.response = cloneJson(response);
      return response;
    } catch (error) {
      call.error = errorMessage(error);
      throw error;
    }
  };

  const recordedPost = async function recordedPost(endpoint, body = {}) {
    const call = {
      method: "POST",
      endpoint: String(endpoint),
      body: cloneJson(body),
    };
    calls.push(call);
    try {
      const response = await originalPost.call(this, endpoint, body);
      call.response = cloneJson(response);
      if (typeof afterPost === "function") {
        await afterPost({
          endpoint: String(endpoint),
          body: cloneJson(body),
          response: cloneJson(response),
        });
      }
      return response;
    } catch (error) {
      call.error = errorMessage(error);
      throw error;
    }
  };

  bridge.apiGet = recordedGet;
  bridge.apiPost = recordedPost;

  return {
    calls,
    forwardPost(endpoint, body = {}) {
      return originalPost.call(bridge, endpoint, body);
    },
    restore() {
      if (bridge.apiGet === recordedGet) bridge.apiGet = originalGet;
      if (bridge.apiPost === recordedPost) bridge.apiPost = originalPost;
    },
  };
}
