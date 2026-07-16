import assert from "node:assert/strict";

const { describe, it } = process.env.VITEST
  ? await import("vitest")
  : await import("node:test");
import * as browserHelpers from "./browser_smoke_helpers.mjs";

const {
  BROWSER_LAUNCH_CANDIDATES,
  createBrowserLaunchOptions,
  installBundledMockBridgeHarness,
  instrumentBrowserBridge,
  isRouteTextSettled,
} = browserHelpers;

describe("browser smoke helpers", () => {
  it("accepts the last form field after scrolling it into the editor viewport", () => {
    assert.equal(typeof browserHelpers.assertEditorViewport, "function");

    assert.doesNotThrow(() => browserHelpers.assertEditorViewport({
      scrollViewport: { top: 120, bottom: 760 },
      lastField: { top: 680, bottom: 728 },
    }));
  });

  it("rejects a last form field hidden below the editor viewport", () => {
    assert.throws(
      () => browserHelpers.assertEditorViewport({
        scrollViewport: { top: 120, bottom: 760 },
        lastField: { top: 744, bottom: 792 },
      }),
      /last form field.*scrollable viewport/i,
    );
  });

  it("accepts a mobile viewport without horizontal overflow", () => {
    assert.equal(typeof browserHelpers.assertNoHorizontalOverflow, "function");

    assert.doesNotThrow(() => browserHelpers.assertNoHorizontalOverflow([
      { label: "document", clientWidth: 390, scrollWidth: 390 },
      { label: "editor", clientWidth: 358, scrollWidth: 359 },
    ]));
  });

  it("reports the mobile element that overflows horizontally", () => {
    assert.throws(
      () => browserHelpers.assertNoHorizontalOverflow([
        { label: "document", clientWidth: 390, scrollWidth: 390 },
        { label: "editor", clientWidth: 358, scrollWidth: 402 },
      ]),
      /editor.*44px/i,
    );
  });

  it("keeps route waits pending while expected text is present but loading text remains", () => {
    assert.equal(isRouteTextSettled("知识图谱 加载中...", "知识图谱"), false);
    assert.equal(isRouteTextSettled("Memory Loading...", "Memory"), false);
    assert.equal(isRouteTextSettled("Граф Загрузка...", "Граф"), false);
  });

  it("allows route waits once every expected text is present and loading text is gone", () => {
    assert.equal(isRouteTextSettled("系统概览 运行观测 Provider 状态", ["系统概览", "运行观测"]), true);
  });

  it("keeps route waits pending until every expected text is present", () => {
    assert.equal(isRouteTextSettled("系统概览 Provider 状态", ["系统概览", "运行观测"]), false);
  });

  it("prefers isolated headless Chrome before falling back to system Edge", () => {
    assert.deepEqual(BROWSER_LAUNCH_CANDIDATES, [
      { channel: "chrome", label: "Google Chrome" },
      { channel: "msedge", label: "Microsoft Edge" },
      { channel: undefined, label: "Playwright Chromium" },
    ]);
  });

  it("opens a visible browser on local Windows runs", () => {
    assert.deepEqual(createBrowserLaunchOptions("chrome", { platform: "win32", ci: false }), {
      channel: "chrome",
      headless: false,
      slowMo: 50,
    });
  });

  it("keeps CI runs headless", () => {
    assert.deepEqual(createBrowserLaunchOptions(undefined, { platform: "win32", ci: true }), {
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

    assert.deepEqual(calls, [
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
    assert.deepEqual(postCalls, ["config/apply"]);

    await raw.apiPost("page/config/apply", {
      base_revision: "revision-2",
      changes: { "recall_engine.top_k": 8 },
    });
    assert.equal(calls.length, 2);
    assert.deepEqual(postCalls, ["config/apply"]);
    assert.deepEqual(forwarded.map((call) => call.receiver), [
      "source-bridge",
      "source-bridge",
      "source-bridge",
    ]);
  });

  it("lets the bundle install its mock bridge before wrapping it for the smoke harness", async () => {
    const target = {};
    const harness = installBundledMockBridgeHarness(target);

    assert.equal(target.AstrBotPluginPage, undefined);
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
    assert.deepEqual(harness.calls, [
      {
        method: "GET",
        endpoint: "page/config/state",
        params: { revision: "revision-1" },
        response: {
          status: "ok",
          data: {
            endpoint: "page/config/state",
            params: { revision: "revision-1" },
            marker: "bundled-mock",
          },
        },
      },
    ]);
    assert.equal(target.__memoraBridgeCalls, harness.calls);
    assert.equal(typeof target.__memoraRawBridge.apiGet, "function");
    const rawResponse = await target.__memoraRawBridge.apiGet("page/config/state", {});
    assert.equal(rawResponse.data.marker, "bundled-mock");
    assert.equal(harness.calls.length, 1);
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

    await assert.rejects(
      bridge.apiPost("page/config/apply", {
        base_revision: "revision-1",
        changes: { "recall_engine.top_k": 9 },
      }),
      /Browser smoke lost the stale apply response/,
    );
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
        error: "Browser smoke lost the stale apply response",
      },
    ]);
  });
});
