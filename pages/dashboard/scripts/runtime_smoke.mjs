import { JSDOM, ResourceLoader, VirtualConsole } from "jsdom";
import { readFile } from "node:fs/promises";
import path from "node:path";
import {
  assertConfigRuntimeCalls,
  instrumentRuntimeBridge,
  waitFor,
} from "./runtime_smoke_helpers.mjs";

const dashboardRoot = process.cwd();
const htmlPath = path.join(dashboardRoot, "index.html");
const html = await readFile(htmlPath, "utf8");
const errors = [];
const runtimeOrigin = "https://memora.runtime";

class LocalDashboardResourceLoader extends ResourceLoader {
  fetch(url, options) {
    const resourceUrl = new URL(url);
    if (resourceUrl.origin !== runtimeOrigin) return super.fetch(url, options);
    const relativePath = decodeURIComponent(resourceUrl.pathname).replace(/^\/+/, "");
    const localPath = path.resolve(dashboardRoot, relativePath);
    const relativeToRoot = path.relative(dashboardRoot, localPath);
    if (relativeToRoot.startsWith("..") || path.isAbsolute(relativeToRoot)) {
      return null;
    }
    return readFile(localPath);
  }
}

if (html.includes("/src/main") || html.includes('type="module"')) {
  throw new Error("Dashboard index.html is not a production AstrBot-compatible build");
}

const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (error) => {
  if (error?.message?.includes("Could not parse CSS stylesheet")) return;
  errors.push(`jsdom: ${error?.message ?? String(error)}`);
});
virtualConsole.on("error", (...args) => {
  const message = args.map(String).join(" ");
  if (message.includes("[GraphPage] G6 render") && message.includes("clearRect")) return;
  errors.push(`console.error: ${message}`);
});

const dom = new JSDOM(html, {
  url: `${runtimeOrigin}/index.html`,
  runScripts: "dangerously",
  resources: new LocalDashboardResourceLoader(),
  pretendToBeVisual: true,
  virtualConsole,
  beforeParse(window) {
    window.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
    window.matchMedia = () => ({
      matches: false,
      media: "",
      onchange: null,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent() {
        return false;
      },
    });
    window.HTMLCanvasElement.prototype.getContext = () => null;
  },
});

const { window } = dom;

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function rootText() {
  return window.document.querySelector("#root")?.textContent?.replace(/\s+/g, " ").trim() ?? "";
}

function assertRootContains(expectedText, route) {
  const text = rootText();
  const expectedItems = Array.isArray(expectedText) ? expectedText : [expectedText];
  for (const item of expectedItems) {
    if (!text.includes(item)) {
      throw new Error(`Dashboard route ${route} did not render expected text: ${item}`);
    }
  }
}

function findButton(text, scope = window.document) {
  const button = [...scope.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.replace(/\s+/g, " ").trim() === text,
  );
  if (!button) throw new Error(`Dashboard runtime could not find button: ${text}`);
  return button;
}

function editNumberField(pathName, value) {
  const pathCode = [...window.document.querySelectorAll("code")].find(
    (candidate) => candidate.textContent?.trim() === pathName,
  );
  const input = pathCode
    ?.closest('[data-slot="field"]')
    ?.querySelector('input[type="number"]');
  if (!input) throw new Error(`Dashboard runtime could not find numeric field: ${pathName}`);
  const valueSetter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )?.set;
  if (!valueSetter) throw new Error("Dashboard runtime could not access input value setter");
  valueSetter.call(input, String(value));
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  input.dispatchEvent(new window.Event("change", { bubbles: true }));
}

async function waitForRootText(expected, description, timeoutMs = 10_000) {
  const expectedItems = Array.isArray(expected) ? expected : [expected];
  try {
    return await waitFor(
      () => {
        const text = rootText();
        return expectedItems.every((item) => text.includes(item)) ? text : false;
      },
      { timeoutMs, description },
    );
  } catch (error) {
    const text = rootText();
    const missing = expectedItems.filter((item) => !text.includes(item));
    throw new Error(
      `${error.message}; hash=${window.location.hash}; missing=${JSON.stringify(missing)}; root=${text.slice(0, 1_000)}`,
    );
  }
}

let configTrace;
try {
  await new Promise((resolve) => window.addEventListener("load", resolve, { once: true }));
  await delay(1000);

  if (errors.length > 0) {
    throw new Error(`Dashboard runtime reported errors:\n${errors.join("\n")}`);
  }

  const root = window.document.querySelector("#root");
  if (!root || root.children.length === 0) {
    throw new Error("Dashboard production bundle did not mount into #root");
  }

  const bridge = window.AstrBotPluginPage;
  if (!bridge) throw new Error("Dashboard production bundle did not install its mock bridge");
  const { calls, forwardPost } = instrumentRuntimeBridge(bridge);

  const routes = [
    ["#/graph", "知识图谱"],
    ["#/memory", "记忆管理"],
    ["#/system", ["系统概览", "运行观测"]],
    ["#/jargon", "黑话"],
  ];

  for (const [hash, expectedText] of routes) {
    window.location.hash = hash;
    window.dispatchEvent(new window.HashChangeEvent("hashchange"));
    await delay(350);
    assertRootContains(expectedText, hash);
  }

  window.location.hash = "#/config";
  window.dispatchEvent(new window.HashChangeEvent("hashchange"));
  await waitForRootText(
    ["配置", "单次召回数量", "recall_engine.top_k", "已同步"],
    "configuration schema to render and become synced",
  );
  const loadedText = rootText();
  for (const loadingText of ["正在加载配置", "加载中...", "Loading configuration", "Загрузка конфигурации"]) {
    if (loadedText.includes(loadingText)) {
      throw new Error(`Dashboard config loading state remained visible: ${loadingText}`);
    }
  }

  editNumberField("recall_engine.top_k", 9);
  await waitForRootText("有未保存更改", "top_k edit to mark the page dirty");
  const initialStateCall = calls.find(
    (call) => call.endpoint === "page/config/state" && call.response?.data?.changed === true,
  );
  const initialRevision = initialStateCall?.response?.data?.revision;
  if (!initialRevision) throw new Error("Dashboard runtime did not capture the initial revision");
  const seeded = await forwardPost("page/config/apply", {
    base_revision: initialRevision,
    changes: { "recall_engine.max_k": 11 },
  });
  if (seeded?.status !== "ok") {
    throw new Error("Dashboard runtime could not seed an external config revision");
  }
  await delay(850);
  findButton("应用配置").click();
  try {
    await waitFor(
      () => window.document.body.textContent?.includes("AstrBot 中的配置已更改"),
      { timeoutMs: 5_000, description: "stale apply to open the conflict dialog" },
    );
  } catch (error) {
    const configCalls = calls.filter((call) => String(call.endpoint).includes("config/"));
    throw new Error(
      `${error.message}\nBody: ${(window.document.body.textContent ?? "").replace(/\s+/g, " ").trim()}\nConfig calls: ${JSON.stringify(configCalls)}`,
    );
  }

  await waitFor(
    () => {
      const text = window.document.body.textContent ?? "";
      return text.includes("在最新版本上重新应用我的更改");
    },
    { timeoutMs: 5_000, description: "latest AstrBot config to populate the conflict dialog" },
  );
  findButton("在最新版本上重新应用我的更改", window.document.body).click();
  await waitForRootText("有未保存更改", "rebased draft to remain dirty");

  findButton("应用配置").click();
  await waitForRootText("正在重载", "successful apply to enter reload state", 5_000);
  await delay(850);
  window.dispatchEvent(new window.Event("focus"));
  await waitForRootText("已同步", "changed plugin instance to finish reload", 5_000);

  configTrace = assertConfigRuntimeCalls(calls, {
    changedPath: "recall_engine.top_k",
    changedValue: 9,
  });
  assertRootContains(configTrace.finalInstanceId, "#/config");

  if (errors.length > 0) {
    throw new Error(`Dashboard runtime reported errors:\n${errors.join("\n")}`);
  }
} finally {
  dom.window.close();
}

console.log(
  `Dashboard config runtime trace passed: ${configTrace.initialRevision} -> ${configTrace.appliedRevision}; ${configTrace.initialInstanceId} -> ${configTrace.finalInstanceId}.`,
);
console.log("Dashboard runtime smoke passed.");
