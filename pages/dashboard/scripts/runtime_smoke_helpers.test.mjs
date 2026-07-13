import { describe, expect, it } from "vitest";

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

describe("runtime smoke bridge instrumentation", () => {
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

    expect(forwarded).toEqual([
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
    expect(calls).toEqual([
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
    expect(bridge.apiGet).toBe(originalGet);
    expect(bridge.apiPost).toBe(originalPost);
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

    await expect(
      bridge.apiPost("page/config/apply", {
        base_revision: "revision-1",
        changes: { "recall_engine.top_k": 9 },
      }),
    ).rejects.toThrow("Runtime smoke lost the stale apply response");

    expect(forwardedCount).toBe(1);
    expect(calls).toEqual([
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

    expect(response).toEqual({ status: "ok", data: { revision: "revision-2" } });
    expect(forwarded).toHaveLength(1);
    expect(instrumentation.calls).toEqual([]);
  });

  it("accepts a complete conflict, rebase, apply, and reload request trace", () => {
    expect(runtimeHelpers.assertConfigRuntimeCalls).toBeTypeOf("function");

    expect(
      runtimeHelpers.assertConfigRuntimeCalls(configLifecycleCalls(), {
        changedPath: "recall_engine.top_k",
        changedValue: 9,
      }),
    ).toEqual({
      initialRevision: "revision-1",
      conflictRevision: "revision-2",
      appliedRevision: "revision-3",
      initialInstanceId: "instance-1",
      finalInstanceId: "instance-3",
    });
  });

  it("rejects a request trace containing an automatic apply retry", () => {
    expect(runtimeHelpers.assertConfigRuntimeCalls).toBeTypeOf("function");
    const calls = configLifecycleCalls();
    calls.splice(3, 0, JSON.parse(JSON.stringify(calls[2])));

    expect(() =>
      runtimeHelpers.assertConfigRuntimeCalls(calls, {
        changedPath: "recall_engine.top_k",
        changedValue: 9,
      }),
    ).toThrow(/exactly two UI apply POSTs/);
  });

  it("rejects a conflict trace without the simulated lost response", () => {
    const calls = configLifecycleCalls();
    delete calls[2].error;

    expect(() =>
      runtimeHelpers.assertConfigRuntimeCalls(calls, {
        changedPath: "recall_engine.top_k",
        changedValue: 9,
      }),
    ).toThrow(/lost stale apply response/);
  });

  it("rejects a reload trace without a recorded disconnect", () => {
    const calls = configLifecycleCalls().filter(
      (call) => call.error !== "Mock plugin is reloading",
    );

    expect(() =>
      runtimeHelpers.assertConfigRuntimeCalls(calls, {
        changedPath: "recall_engine.top_k",
        changedValue: 9,
      }),
    ).toThrow(/reload disconnect/);
  });

  it("times out bounded waits with the pending condition in the error", async () => {
    expect(runtimeHelpers.waitFor).toBeTypeOf("function");

    await expect(
      runtimeHelpers.waitFor(() => false, {
        timeoutMs: 20,
        intervalMs: 1,
        description: "configuration page to become synced",
      }),
    ).rejects.toThrow(/configuration page to become synced.*20ms/);
  });
});
