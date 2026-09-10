import { JSDOM, ResourceLoader, VirtualConsole } from "jsdom";
import { readFile } from "node:fs/promises";
import path from "node:path";
import {
  assertConfigRuntimeCalls,
  assertEditingRuntimeCalls,
  instrumentRuntimeBridge,
  resolveRuntimeResourcePath,
  waitFor,
} from "./runtime_smoke_helpers.mjs";

const dashboardRoot = process.cwd();
const htmlPath = path.join(dashboardRoot, "index.html");
const html = await readFile(htmlPath, "utf8");
const errors = [];
const runtimeOrigin = "https://memora.runtime";

class LocalDashboardResourceLoader extends ResourceLoader {
  fetch(url) {
    const localPath = resolveRuntimeResourcePath(url, {
      runtimeOrigin,
      dashboardRoot,
    });
    return localPath ? readFile(localPath) : null;
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
    window.structuredClone = (value) => globalThis.structuredClone(value);
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

function setInputValue(input, value) {
  const valueSetter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )?.set;
  if (!valueSetter) throw new Error("Dashboard runtime could not access input value setter");
  valueSetter.call(input, String(value));
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  input.dispatchEvent(new window.Event("change", { bubbles: true }));
}

function editNumberField(pathName, value) {
  const pathCode = [...window.document.querySelectorAll("code")].find(
    (candidate) => candidate.textContent?.trim() === pathName,
  );
  const input = pathCode
    ?.closest('[data-slot="field"]')
    ?.querySelector('input[type="number"]');
  if (!input) throw new Error(`Dashboard runtime could not find numeric field: ${pathName}`);
  setInputValue(input, value);
}

function requireOkData(response, label) {
  if (response?.status !== "ok" || !response.data || typeof response.data !== "object") {
    throw new Error(`${label} failed: ${JSON.stringify(response)}`);
  }
  return response.data;
}

function requireEntityEnvelope(response, label) {
  const data = requireOkData(response, label);
  if (!data.entity || typeof data.entity !== "object" || typeof data.revision !== "string") {
    throw new Error(`${label} did not return an entity revision envelope`);
  }
  return data;
}

async function exerciseEditingRuntimeRoutes(bridge, calls) {
  const suffix = "task18-runtime";

  const socialIdentity = {
    from_user: `${suffix}-from`,
    to_user: `${suffix}-to`,
    relation_type: "colleague",
    group_id: "group_001",
  };
  const socialCreated = requireEntityEnvelope(await bridge.apiPost("page/social/create", {
    ...socialIdentity,
    strength: 0.4,
    tags: ["runtime"],
  }), "social create");
  requireOkData(await bridge.apiGet("page/social/relations", {
    group_id: socialIdentity.group_id,
  }), "social list");
  const socialUpdated = requireEntityEnvelope(await bridge.apiPost("page/social/update", {
    identity: socialIdentity,
    changes: { strength: 0.75, tags: ["runtime", "updated"] },
    expected_revision: socialCreated.revision,
  }), "social update");
  await bridge.apiPost("page/social/update", {
    identity: socialIdentity,
    changes: { strength: 0.1 },
    expected_revision: socialCreated.revision,
  });
  requireOkData(await bridge.apiPost("page/social/delete", {
    identity: socialIdentity,
    expected_revision: socialUpdated.revision,
  }), "social delete");
  const socialBatchIdentity = { ...socialIdentity, from_user: `${suffix}-batch-from` };
  const socialBatchCreated = requireEntityEnvelope(await bridge.apiPost("page/social/create", {
    ...socialBatchIdentity,
    strength: 0.5,
    tags: [],
  }), "social batch seed");
  requireOkData(await bridge.apiPost("page/social/batch", {
    action: "delete",
    items: [{ identity: socialBatchIdentity, expected_revision: socialBatchCreated.revision }],
    params: {},
  }), "social batch");

  const profileDraft = {
    user_id: `${suffix}-profile`,
    display_name: "Task 18 Runtime",
    preferences: {
      reply_style: "concise",
      preferred_topics: ["runtime"],
      avoided_topics: ["spoilers"],
      active_hours: [9, 17],
    },
    tags: [{ category: "interest", value: "runtime", confidence: 0.9 }],
  };
  const profileCreated = requireEntityEnvelope(
    await bridge.apiPost("page/profiles/create", profileDraft),
    "profile create",
  );
  requireOkData(await bridge.apiGet("page/profiles", { limit: "100", offset: "0" }), "profile list");
  requireOkData(await bridge.apiGet("page/profiles/detail", { user_id: profileDraft.user_id }), "profile detail");
  const profileUpdated = requireEntityEnvelope(await bridge.apiPost("page/profiles/update", {
    identity: { user_id: profileDraft.user_id },
    changes: { display_name: "Task 18 Runtime Updated" },
    expected_revision: profileCreated.revision,
  }), "profile update");
  requireOkData(await bridge.apiPost("page/profiles/delete", {
    identity: { user_id: profileDraft.user_id },
    expected_revision: profileUpdated.revision,
  }), "profile delete");
  const profileBatchDraft = { ...profileDraft, user_id: `${suffix}-profile-batch` };
  const profileBatchCreated = requireEntityEnvelope(
    await bridge.apiPost("page/profiles/create", profileBatchDraft),
    "profile batch seed",
  );
  requireOkData(await bridge.apiPost("page/profiles/batch", {
    action: "delete",
    items: [{
      identity: { user_id: profileBatchDraft.user_id },
      expected_revision: profileBatchCreated.revision,
    }],
    params: {},
  }), "profile batch");

  requireOkData(await bridge.apiGet("page/jargon/candidates", { group_id: "group_001" }), "jargon candidates");
  requireOkData(await bridge.apiGet("page/jargon/stats", { group_id: "group_001" }), "jargon stats");
  const jargonDraft = {
    term: `${suffix}-jargon`,
    group_id: "group_001",
    meaning: "Runtime smoke jargon",
    confidence: 0.8,
    is_jargon: true,
    is_confirmed: false,
    is_global: false,
  };
  const jargonIdentity = { term: jargonDraft.term, group_id: jargonDraft.group_id };
  const jargonCreated = requireEntityEnvelope(
    await bridge.apiPost("page/jargon/create", jargonDraft),
    "jargon create",
  );
  requireOkData(await bridge.apiGet("page/jargon/meanings", {
    group_id: jargonDraft.group_id,
    confirmed_only: "false",
  }), "jargon meanings");
  const jargonUpdated = requireEntityEnvelope(await bridge.apiPost("page/jargon/update", {
    identity: jargonIdentity,
    changes: { meaning: "Updated runtime jargon" },
    expected_revision: jargonCreated.revision,
  }), "jargon update");
  requireOkData(await bridge.apiPost("page/jargon/delete", {
    identity: jargonIdentity,
    expected_revision: jargonUpdated.revision,
  }), "jargon delete");
  const jargonBatchDraft = { ...jargonDraft, term: `${suffix}-jargon-batch` };
  const jargonBatchIdentity = {
    term: jargonBatchDraft.term,
    group_id: jargonBatchDraft.group_id,
  };
  const jargonBatchCreated = requireEntityEnvelope(
    await bridge.apiPost("page/jargon/create", jargonBatchDraft),
    "jargon batch seed",
  );
  requireOkData(await bridge.apiPost("page/jargon/batch", {
    action: "delete",
    items: [{ identity: jargonBatchIdentity, expected_revision: jargonBatchCreated.revision }],
  }), "jargon batch");

  requireOkData(await bridge.apiGet("page/affection/status", { group_id: "group_001" }), "affection status");
  await bridge.apiPost("page/affection/users/create", {
    group_id: "group_001",
    user_id: `${suffix}-invalid-score`,
    affection_score: 101,
  });
  const affectionDraft = {
    group_id: "group_001",
    user_id: `${suffix}-affection`,
    affection_score: 42,
  };
  const affectionIdentity = {
    group_id: affectionDraft.group_id,
    user_id: affectionDraft.user_id,
  };
  const affectionCreated = requireEntityEnvelope(
    await bridge.apiPost("page/affection/users/create", affectionDraft),
    "affection create",
  );
  requireOkData(await bridge.apiGet("page/affection/users", {
    group_id: affectionDraft.group_id,
    limit: "50",
    offset: "0",
  }), "affection users");
  const affectionUpdated = requireEntityEnvelope(await bridge.apiPost("page/affection/users/update", {
    identity: affectionIdentity,
    changes: { affection_score: 55 },
    expected_revision: affectionCreated.revision,
  }), "affection update");
  requireOkData(await bridge.apiPost("page/affection/users/delete", {
    identity: affectionIdentity,
    expected_revision: affectionUpdated.revision,
  }), "affection delete");
  const affectionBatchDraft = { ...affectionDraft, user_id: `${suffix}-affection-batch` };
  const affectionBatchIdentity = {
    group_id: affectionBatchDraft.group_id,
    user_id: affectionBatchDraft.user_id,
  };
  const affectionBatchCreated = requireEntityEnvelope(
    await bridge.apiPost("page/affection/users/create", affectionBatchDraft),
    "affection batch seed",
  );
  requireOkData(await bridge.apiPost("page/affection/users/batch", {
    action: "delete",
    items: [{
      identity: affectionBatchIdentity,
      expected_revision: affectionBatchCreated.revision,
    }],
    params: {},
  }), "affection batch");
  requireOkData(await bridge.apiPost("page/affection/mood/set", {
    group_id: "group_001",
    mood_type: "happy",
    intensity: 0.7,
    duration_hours: 2.5,
    description: "Task 18 runtime mood",
  }), "mood set");
  requireOkData(await bridge.apiGet("page/affection/moods/history", {
    group_id: "group_001",
    limit: "20",
  }), "mood history");
  requireOkData(
    await bridge.apiPost("page/affection/mood/reset", { group_id: "group_001" }),
    "mood reset",
  );

  return assertEditingRuntimeCalls(calls);
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
let editingTrace;
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
  let loseNextStaleApplyResponse = true;
  const { calls, forwardPost } = instrumentRuntimeBridge(bridge, {
    afterPost({ endpoint, response }) {
      if (
        !loseNextStaleApplyResponse
        || endpoint !== "page/config/apply"
        || response?.code !== "config_conflict"
      ) {
        return;
      }
      loseNextStaleApplyResponse = false;
      throw new Error("Runtime smoke lost the stale apply response");
    },
  });

  editingTrace = await exerciseEditingRuntimeRoutes(bridge, calls);

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

  const configSearch = window.document.querySelector('input[aria-label="搜索配置"]');
  if (!configSearch) throw new Error("Dashboard runtime could not find config search");
  setInputValue(configSearch, "recall_engine.top_k");
  await waitFor(
    () => {
      const codePaths = [...window.document.querySelectorAll("code")].map(
        (code) => code.textContent?.trim(),
      );
      return codePaths.includes("recall_engine.top_k")
        && !codePaths.includes("provider_settings.embedding_provider_id");
    },
    { timeoutMs: 5_000, description: "config search to narrow the rendered schema" },
  );

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
  const staleApplyCalls = calls.filter(
    (call) =>
      call.method === "POST"
      && call.endpoint === "page/config/apply"
      && call.body?.base_revision === initialRevision,
  );
  if (staleApplyCalls.length !== 1) {
    throw new Error(
      `Dashboard runtime expected one stale Apply POST, received ${staleApplyCalls.length}`,
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
  const successfulApplyCall = await waitFor(
    () => [...calls].reverse().find(
      (call) =>
        call.method === "POST"
        && call.endpoint === "page/config/apply"
        && call.response?.status === "ok",
    ),
    {
      timeoutMs: 5_000,
      description: "successful Apply response to enter reload state",
    },
  );
  const appliedRevision = successfulApplyCall?.response?.data?.revision;
  if (!appliedRevision) {
    throw new Error("Dashboard runtime did not capture the successful Apply revision");
  }
  await waitFor(
    async () => {
      window.dispatchEvent(new window.Event("focus"));
      await delay(50);
      return calls.some(
        (call) =>
          call.method === "GET"
          && call.endpoint === "page/config/state"
          && call.params?.revision === appliedRevision
          && /Mock plugin is reloading/i.test(String(call.error ?? "")),
      );
    },
    {
      timeoutMs: 700,
      intervalMs: 25,
      description: "reload state GET to observe the mock disconnect",
    },
  );
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
console.log(
  `Dashboard editing runtime routes passed: ${editingTrace.getEndpoints} GET and ${editingTrace.postEndpoints} POST endpoints.`,
);
console.log("Dashboard runtime smoke passed.");
