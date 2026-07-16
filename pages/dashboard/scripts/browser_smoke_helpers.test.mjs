import { describe, expect, it } from "vitest";
import {
  BROWSER_LAUNCH_CANDIDATES,
  createBrowserLaunchOptions,
  installBundledMockBridgeHarness,
  instrumentBrowserBridge,
  isRouteTextSettled,
} from "./browser_smoke_helpers.mjs";

describe("browser smoke helpers", () => {
  it("keeps route waits pending while expected text is present but loading text remains", () => {
    expect(isRouteTextSettled("知识图谱 加载中...", "知识图谱")).toBe(false);
    expect(isRouteTextSettled("Memory Loading...", "Memory")).toBe(false);
    expect(isRouteTextSettled("Граф Загрузка...", "Граф")).toBe(false);
  });

  it("allows route waits once every expected text is present and loading text is gone", () => {
    expect(isRouteTextSettled("系统概览 运行观测 Provider 状态", ["系统概览", "运行观测"])).toBe(true);
  });

  it("keeps route waits pending until every expected text is present", () => {
    expect(isRouteTextSettled("系统概览 Provider 状态", ["系统概览", "运行观测"])).toBe(false);
  });

  it("prefers isolated headless Chrome before falling back to system Edge", () => {
    expect(BROWSER_LAUNCH_CANDIDATES).toEqual([
      { channel: "chrome", label: "Google Chrome" },
      { channel: "msedge", label: "Microsoft Edge" },
      { channel: undefined, label: "Playwright Chromium" },
    ]);
  });

  it("opens a visible browser on local Windows runs", () => {
    expect(createBrowserLaunchOptions("chrome", { platform: "win32", ci: false })).toEqual({
      channel: "chrome",
      headless: false,
      slowMo: 50,
    });
  });

  it("keeps CI runs headless", () => {
    expect(createBrowserLaunchOptions(undefined, { platform: "win32", ci: true })).toEqual({
      headless: true,
    });
  });

  it("records immutable GET and POST transport snapshots while exposing a raw bypass", async () => {
    const forwarded = [];
    const sourceResponse = {
      status: "ok",
      data: { revision: "revision-2", nested: { value: 1 } },
    };
    const sourceBridge = {
      marker: "source-bridge",
      async apiGet(endpoint, params) {
        forwarded.push({ method: "GET", endpoint, params, receiver: this.marker });
        return sourceResponse;
      },
      async apiPost(endpoint, body) {
        forwarded.push({ method: "POST", endpoint, body, receiver: this.marker });
        return sourceResponse;
      },
    };
    const { bridge, calls, postCalls, raw } = instrumentBrowserBridge(sourceBridge);
    const params = { revision: "revision-1" };
    const body = {
      base_revision: "revision-1",
      changes: { "recall_engine.top_k": 9 },
    };

    const getResponse = await bridge.apiGet("page/config/state", params);
    await bridge.apiPost("page/config/apply", body);
    params.revision = "mutated";
    body.changes["recall_engine.top_k"] = 99;
    getResponse.data.nested.value = 99;

    expect(calls).toEqual([
      {
        method: "GET",
        endpoint: "page/config/state",
        params: { revision: "revision-1" },
        response: {
          status: "ok",
          data: { revision: "revision-2", nested: { value: 1 } },
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
          status: "ok",
          data: { revision: "revision-2", nested: { value: 1 } },
        },
      },
    ]);
    expect(postCalls).toEqual(["config/apply"]);

    await raw.apiPost("page/config/apply", {
      base_revision: "revision-2",
      changes: { "recall_engine.top_k": 8 },
    });
    expect(calls).toHaveLength(2);
    expect(postCalls).toEqual(["config/apply"]);
    expect(forwarded.map((call) => call.receiver)).toEqual([
      "source-bridge",
      "source-bridge",
      "source-bridge",
    ]);
  });

  it("redacts sensitive bridge-call snapshots without changing forwarded transport values", async () => {
    const forwarded = [];
    const getResponse = {
      status: "ok",
      data: {
        decision_id: "decision-secret",
        trace_id: "trace-secret",
        query: "raw query",
        nested: {
          endpoint: "https://provider.example/v1",
          api_key: "provider-key",
          safe: "kept",
        },
      },
    };
    const postResponse = {
      status: "ok",
      data: [{ stack_trace: "private stack", provider_endpoint: "https://provider.example" }],
    };
    const sourceBridge = {
      async apiGet(endpoint, params) {
        forwarded.push({ method: "GET", endpoint, payload: params });
        return getResponse;
      },
      async apiPost(endpoint, body) {
        forwarded.push({ method: "POST", endpoint, payload: body });
        return postResponse;
      },
    };
    const { bridge, calls } = instrumentBrowserBridge(sourceBridge);
    const params = {
      decision_id: "decision-secret",
      filters: { user_id: "user-secret", safe: "kept" },
    };
    const body = {
      prompt: "private prompt",
      memory_content: "private memory",
      nested: {
        memory_ids: ["memory-secret"],
        group_id: "group-secret",
        persona_id: "persona-secret",
        session_id: "session-secret",
        headers: { Authorization: "Bearer secret" },
        secret: "provider-secret",
        safe: "kept",
      },
    };

    expect(await bridge.apiGet("page/injection-strategy/decisions/detail", params)).toBe(
      getResponse,
    );
    expect(await bridge.apiPost("page/config/apply", body)).toBe(postResponse);

    expect(forwarded).toEqual([
      {
        method: "GET",
        endpoint: "page/injection-strategy/decisions/detail",
        payload: params,
      },
      { method: "POST", endpoint: "page/config/apply", payload: body },
    ]);
    expect(calls).toEqual([
      {
        method: "GET",
        endpoint: "page/injection-strategy/decisions/detail",
        params: { filters: { safe: "kept" } },
        response: { status: "ok", data: { nested: { safe: "kept" } } },
      },
      {
        method: "POST",
        endpoint: "page/config/apply",
        body: { nested: { safe: "kept" } },
        response: { status: "ok", data: [{}] },
      },
    ]);
  });

  it("lets the bundle install its mock bridge before wrapping it for the smoke harness", async () => {
    const target = {};
    const harness = installBundledMockBridgeHarness(target);

    expect(target.AstrBotPluginPage).toBeUndefined();
    const sourceBridge = {
      marker: "bundled-mock",
      async apiGet(endpoint, params) {
        return { status: "ok", data: { endpoint, params, marker: this.marker } };
      },
      async apiPost() {
        return { status: "ok" };
      },
    };
    target.AstrBotPluginPage = sourceBridge;

    await target.AstrBotPluginPage.apiGet("page/config/state", {
      revision: "revision-1",
    });
    expect(harness.calls).toEqual([
      {
        method: "GET",
        endpoint: "page/config/state",
        params: { revision: "revision-1" },
        response: {
          status: "ok",
          data: {
            params: { revision: "revision-1" },
            marker: "bundled-mock",
          },
        },
      },
    ]);
    expect(target.__memoraBridgeCalls).toBe(harness.calls);
    expect(target.__memoraRawBridge.apiGet).toBeTypeOf("function");
    expect(await target.__memoraRawBridge.apiGet("page/config/state", {})).toMatchObject({
      data: { marker: "bundled-mock" },
    });
    expect(harness.calls).toHaveLength(1);
  });

  it("records a response before an afterPost hook simulates a lost transport response", async () => {
    const sourceBridge = {
      async apiGet() {
        return { status: "ok" };
      },
      async apiPost() {
        return {
          status: "error",
          code: "config_conflict",
          data: { current_revision: "revision-2" },
        };
      },
    };
    const { bridge, calls } = instrumentBrowserBridge(sourceBridge, {
      afterPost({ response }) {
        if (response.code === "config_conflict") {
          throw new Error("Browser smoke lost the stale apply response");
        }
      },
    });

    await expect(
      bridge.apiPost("page/config/apply", {
        base_revision: "revision-1",
        changes: { "recall_engine.top_k": 9 },
      }),
    ).rejects.toThrow("Browser smoke lost the stale apply response");
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
        error: "Browser smoke lost the stale apply response",
      },
    ]);
  });
});
