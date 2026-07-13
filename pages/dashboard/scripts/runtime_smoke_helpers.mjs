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

export function instrumentRuntimeBridge(bridge) {
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
