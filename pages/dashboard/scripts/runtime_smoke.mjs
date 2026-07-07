import { JSDOM, VirtualConsole } from "jsdom";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const dashboardRoot = process.cwd();
const htmlPath = path.join(dashboardRoot, "index.html");
const html = await readFile(htmlPath, "utf8");
const errors = [];

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
  url: pathToFileURL(htmlPath).href,
  runScripts: "dangerously",
  resources: "usable",
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

await new Promise((resolve) => window.addEventListener("load", resolve, { once: true }));
await delay(1000);

if (errors.length > 0) {
  throw new Error(`Dashboard runtime reported errors:\n${errors.join("\n")}`);
}

const root = window.document.querySelector("#root");
if (!root || root.children.length === 0) {
  throw new Error("Dashboard production bundle did not mount into #root");
}

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

dom.window.close();
console.log("Dashboard runtime smoke passed.");
