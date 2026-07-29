import assert from "node:assert/strict";
import path from "node:path";

const NODE_TEST_SPECIFIER = "node:test";
const { describe, it } = process.env.VITEST
  ? await import("vitest")
  : await import(/* @vite-ignore */ NODE_TEST_SPECIFIER);

import * as runtimeHelpers from "./runtime_smoke_helpers.mjs";

const { instrumentRuntimeBridge } = runtimeHelpers;

function configLifecycleCalls() {
  return [
    {
      method: "GET",
      endpoint: "page/config/schema",
      params: {},
      response: { status: "ok", data: { plugin_name: "astrbot_plugin_memora" } },
    },
    {
      method: "GET",
      endpoint: "page/config/state",
      params: {},
      response: {
        status: "ok",
        data: {
          revision: "revision-1",
          instance_id: "instance-1",
          changed: true,
          config: { recall_engine: { top_k: 5, max_k: 10 } },
        },
      },
    },
    {
      method: "POST",
      endpoint: "page/config/apply",
      body: {
        base_revision: "revision-1",
        changes: { "recall_engine.top_k": 9 },
      },
      response: {
        status: "error",
        code: "config_conflict",
        data: { current_revision: "revision-2" },
      },
      error: "Runtime smoke lost the stale apply response",
    },
    {
      method: "GET",
      endpoint: "page/config/state",
      params: { revision: "revision-1" },
      response: {
        status: "ok",
        data: {
          revision: "revision-2",
          instance_id: "instance-2",
          changed: true,
          config: { recall_engine: { top_k: 5, max_k: 11 } },
        },
      },
    },
    {
      method: "POST",
      endpoint: "page/config/apply",
      body: {
        base_revision: "revision-2",
        changes: { "recall_engine.top_k": 9 },
      },
      response: {
        status: "ok",
        data: {
          revision: "revision-3",
          instance_id: "instance-2",
          changed_paths: ["recall_engine.top_k"],
          reload_scheduled: true,
        },
      },
    },
    {
      method: "GET",
      endpoint: "page/config/state",
      params: { revision: "revision-3" },
      error: "Mock plugin is reloading",
    },
    {
      method: "GET",
      endpoint: "page/config/state",
      params: { revision: "revision-3" },
      response: {
        status: "ok",
        data: {
          revision: "revision-3",
          instance_id: "instance-3",
          changed: false,
        },
      },
    },
  ];
}

function editingRuntimeCalls() {
  const getEndpoints = [
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
  const postEndpoints = [
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
  const calls = [
    ...getEndpoints.map((endpoint) => ({
      method: "GET",
      endpoint,
      params: {},
      response: { status: "ok", data: {} },
    })),
    ...postEndpoints.map((endpoint) => ({
      method: "POST",
      endpoint,
      body: {},
      response: { status: "ok", data: {} },
    })),
  ];
  calls.push(
    {
      method: "POST",
      endpoint: "page/affection/users/create",
      body: { group_id: "g", user_id: "invalid", affection_score: 101 },
      response: {
        status: "error",
        code: "validation_error",
        field_errors: { affection_score: "必须在 -100 到 100 之间" },
      },
    },
    {
      method: "POST",
      endpoint: "page/social/update",
      body: { expected_revision: "stale" },
      response: {
        status: "error",
        code: "edit_conflict",
        data: { current_entity: { from_user: "a" }, current_revision: "rev-current" },
      },
    },
  );
  return calls;
}

describe("runtime smoke bridge instrumentation", () => {
  it("accepts complete editing CRUD route and error-envelope coverage", () => {
    assert.equal(typeof runtimeHelpers.assertEditingRuntimeCalls, "function");
    assert.doesNotThrow(() => runtimeHelpers.assertEditingRuntimeCalls(editingRuntimeCalls()));
  });

  it("rejects missing editing routes and required error envelopes", () => {
    const missingRoute = editingRuntimeCalls().filter(
      (call) => call.endpoint !== "page/jargon/batch",
    );
    assert.throws(
      () => runtimeHelpers.assertEditingRuntimeCalls(missingRoute),
      /page\/jargon\/batch/,
    );

    const missingValidation = editingRuntimeCalls().filter(
      (call) => call.response?.code !== "validation_error",
    );
    assert.throws(
      () => runtimeHelpers.assertEditingRuntimeCalls(missingValidation),
      /affection_score.*validation/i,
    );

    const missingConflict = editingRuntimeCalls().filter(
      (call) => call.response?.code !== "edit_conflict",
    );
    assert.throws(
      () => runtimeHelpers.assertEditingRuntimeCalls(missingConflict),
      /edit_conflict/i,
    );
  });

  it("accepts an editor with the expected title, no loader, and a fixed footer", () => {
    assert.equal(typeof runtimeHelpers.assertEditorReadiness, "function");

    assert.doesNotThrow(() => runtimeHelpers.assertEditorReadiness({
      visibleTitles: ["Edit social relation"],
      loadingOverlayVisible: false,
      fixedFooterVisible: true,
    }, {
      expectedTitle: "Edit social relation",
    }));
  });

  it("rejects an editor until its title, loader, and fixed footer are ready", () => {
    assert.throws(
      () => runtimeHelpers.assertEditorReadiness({
        visibleTitles: ["Social relation"],
        loadingOverlayVisible: true,
        fixedFooterVisible: false,
      }, {
        expectedTitle: "Edit social relation",
      }),
      /expected title.*loading overlay.*fixed editor footer/i,
    );
  });

  it("requires the expected conflict and unsaved dialog actions", () => {
    assert.equal(typeof runtimeHelpers.assertDialogActions, "function");

    assert.doesNotThrow(() => runtimeHelpers.assertDialogActions(
      ["Load latest", "Overwrite"],
      ["Load latest", "Overwrite"],
      "conflict dialog",
    ));
    assert.doesNotThrow(() => runtimeHelpers.assertDialogActions(
      ["Keep editing", "Discard"],
      ["Keep editing", "Discard"],
      "unsaved dialog",
    ));
    assert.throws(
      () => runtimeHelpers.assertDialogActions(
        ["Keep editing"],
        ["Keep editing", "Discard"],
        "unsaved dialog",
      ),
      /unsaved dialog.*Discard/i,
    );
  });

  it("allows only contained same-origin runtime resources", () => {
    assert.equal(typeof runtimeHelpers.resolveRuntimeResourcePath, "function");
    const dashboardRoot = path.resolve("runtime-smoke-dashboard");
    const options = {
      runtimeOrigin: "https://memora.runtime",
      dashboardRoot,
    };

    assert.equal(
      runtimeHelpers.resolveRuntimeResourcePath(
        "https://memora.runtime/assets/index.js",
        options,
      ),
      path.join(dashboardRoot, "assets", "index.js"),
    );
    assert.equal(
      runtimeHelpers.resolveRuntimeResourcePath(
        "https://evil.example/script.js",
        options,
      ),
      null,
    );
    assert.equal(
      runtimeHelpers.resolveRuntimeResourcePath(
        "https://memora.runtime/%2e%2e%2fsecret.js",
        options,
      ),
      null,
    );
  });

  it("records exact GET params and POST bodies while forwarding through the bridge", async () => {
    const forwarded = [];
    const bridge = {
      async apiGet(endpoint, params) {
        forwarded.push({
          method: "GET",
          endpoint,
          params: JSON.parse(JSON.stringify(params)),
        });
        return { status: "ok", data: { changed: false } };
      },
      async apiPost(endpoint, body) {
        forwarded.push({
          method: "POST",
          endpoint,
          body: JSON.parse(JSON.stringify(body)),
        });
        return { status: "ok", data: { revision: "revision-2" } };
      },
    };
    const originalGet = bridge.apiGet;
    const originalPost = bridge.apiPost;
    const { calls, restore } = instrumentRuntimeBridge(bridge);

    const params = { revision: "revision-1" };
    const body = {
      base_revision: "revision-1",
      changes: { "recall_engine.top_k": 9 },
    };
    await bridge.apiGet("page/config/state", params);
    await bridge.apiPost("page/config/apply", body);
    params.revision = "mutated";
    body.changes["recall_engine.top_k"] = 99;

    assert.deepEqual(forwarded, [
      {
        method: "GET",
        endpoint: "page/config/state",
        params: { revision: "revision-1" },
      },
      {
        method: "POST",
        endpoint: "page/config/apply",
        body: {
          base_revision: "revision-1",
          changes: { "recall_engine.top_k": 9 },
        },
      },
    ]);
    assert.deepEqual(calls, [
      {
        method: "GET",
        endpoint: "page/config/state",
        params: { revision: "revision-1" },
        response: { status: "ok", data: { changed: false } },
      },
      {
        method: "POST",
        endpoint: "page/config/apply",
        body: {
          base_revision: "revision-1",
          changes: { "recall_engine.top_k": 9 },
        },
        response: { status: "ok", data: { revision: "revision-2" } },
      },
    ]);

    restore();
    assert.equal(bridge.apiGet, originalGet);
    assert.equal(bridge.apiPost, originalPost);
  });

  it("records a POST response before a one-shot afterPost transport failure", async () => {
    let loseNextApplyResponse = true;
    let forwardedCount = 0;
    const bridge = {
      async apiGet() {
        return { status: "ok" };
      },
      async apiPost() {
        forwardedCount += 1;
        return {
          status: "error",
          code: "config_conflict",
          data: { current_revision: "revision-2" },
        };
      },
    };
    const { calls } = instrumentRuntimeBridge(bridge, {
      afterPost({ response }) {
        if (!loseNextApplyResponse || response.code !== "config_conflict") return;
        loseNextApplyResponse = false;
        throw new Error("Runtime smoke lost the stale apply response");
      },
    });

    await assert.rejects(
      bridge.apiPost("page/config/apply", {
        base_revision: "revision-1",
        changes: { "recall_engine.top_k": 9 },
      }),
      /Runtime smoke lost the stale apply response/,
    );

    assert.equal(forwardedCount, 1);
    assert.deepEqual(calls, [
      {
        method: "POST",
        endpoint: "page/config/apply",
        body: {
          base_revision: "revision-1",
          changes: { "recall_engine.top_k": 9 },
        },
        response: {
          status: "error",
          code: "config_conflict",
          data: { current_revision: "revision-2" },
        },
        error: "Runtime smoke lost the stale apply response",
      },
    ]);
  });

  it("exposes an unrecorded POST bypass for external AstrBot changes", async () => {
    const forwarded = [];
    const bridge = {
      async apiGet() {
        return { status: "ok" };
      },
      async apiPost(endpoint, body) {
        forwarded.push({ endpoint, body });
        return { status: "ok", data: { revision: "revision-2" } };
      },
    };
    const instrumentation = instrumentRuntimeBridge(bridge);

    const response = await instrumentation.forwardPost("page/config/apply", {
      base_revision: "revision-1",
      changes: { "recall_engine.max_k": 11 },
    });

    assert.deepEqual(response, { status: "ok", data: { revision: "revision-2" } });
    assert.equal(forwarded.length, 1);
    assert.deepEqual(instrumentation.calls, []);
  });

  it("accepts a complete conflict, rebase, apply, and reload request trace", () => {
    assert.equal(typeof runtimeHelpers.assertConfigRuntimeCalls, "function");

    assert.deepEqual(
      runtimeHelpers.assertConfigRuntimeCalls(configLifecycleCalls(), {
        changedPath: "recall_engine.top_k",
        changedValue: 9,
      }),
      {
      initialRevision: "revision-1",
      conflictRevision: "revision-2",
      appliedRevision: "revision-3",
      initialInstanceId: "instance-1",
      finalInstanceId: "instance-3",
      },
    );
  });

  it("rejects a request trace containing an automatic apply retry", () => {
    assert.equal(typeof runtimeHelpers.assertConfigRuntimeCalls, "function");
    const calls = configLifecycleCalls();
    calls.splice(3, 0, JSON.parse(JSON.stringify(calls[2])));

    assert.throws(
      () => runtimeHelpers.assertConfigRuntimeCalls(calls, {
        changedPath: "recall_engine.top_k",
        changedValue: 9,
      }),
      /exactly two UI apply POSTs/,
    );
  });

  it("rejects a conflict trace without the simulated lost response", () => {
    const calls = configLifecycleCalls();
    delete calls[2].error;

    assert.throws(
      () => runtimeHelpers.assertConfigRuntimeCalls(calls, {
        changedPath: "recall_engine.top_k",
        changedValue: 9,
      }),
      /lost stale apply response/,
    );
  });

  it("rejects a reload trace without a recorded disconnect", () => {
    const calls = configLifecycleCalls().filter(
      (call) => call.error !== "Mock plugin is reloading",
    );

    assert.throws(
      () => runtimeHelpers.assertConfigRuntimeCalls(calls, {
        changedPath: "recall_engine.top_k",
        changedValue: 9,
      }),
      /reload disconnect/,
    );
  });

  it("times out bounded waits with the pending condition in the error", async () => {
    assert.equal(typeof runtimeHelpers.waitFor, "function");

    await assert.rejects(
      runtimeHelpers.waitFor(() => false, {
        timeoutMs: 20,
        intervalMs: 1,
        description: "configuration page to become synced",
      }),
      /configuration page to become synced.*20ms/,
    );
  });
});
