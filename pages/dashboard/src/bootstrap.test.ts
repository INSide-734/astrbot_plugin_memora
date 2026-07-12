import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const currentDir = dirname(fileURLToPath(import.meta.url));
const dashboardHtml = readFileSync(resolve(currentDir, "../index.src.bak"), "utf-8");

type BootstrapWindow = typeof window & {
  doBootstrap: (action: "install" | "build") => Promise<void>;
};

let bootstrapTimeout: (() => void) | undefined;

function setBridge(value: unknown): void {
  Object.defineProperty(window, "AstrBotPluginPage", {
    configurable: true,
    value,
  });
}

function loadBootstrapPage(): string {
  document.documentElement.innerHTML = dashboardHtml;

  const scripts = Array.from(document.querySelectorAll("script"));
  const bootstrapScript = scripts[scripts.length - 1]?.textContent;
  if (!bootstrapScript) {
    throw new Error("Bootstrap script not found in dashboard index.src.bak");
  }

  vi.spyOn(window, "setTimeout").mockImplementation((handler) => {
    if (typeof handler === "function") bootstrapTimeout = handler as () => void;
    return 0 as unknown as ReturnType<typeof setTimeout>;
  });
  window.eval(bootstrapScript);
  return bootstrapScript;
}

function compactText(selector: string): string {
  return (document.querySelector(selector)?.textContent ?? "").replace(/\s+/g, " ").trim();
}

const staticLanguageCases = [
  {
    language: "zh",
    htmlLanguage: "zh-CN",
    title: "Memora 管理面板",
    subtitle: "首次使用前需要构建",
    install: "安装依赖",
    build: "构建页面",
    refresh: "刷新页面",
    output: "输出日志",
    waiting: "等待操作...",
    manual: "或手动运行：",
  },
  {
    language: "en",
    htmlLanguage: "en-US",
    title: "Memora Dashboard",
    subtitle: "needs to be built before first use",
    install: "Install Dependencies",
    build: "Build Page",
    refresh: "Refresh Page",
    output: "Output Log",
    waiting: "Waiting for action...",
    manual: "Or run manually:",
  },
  {
    language: "ru",
    htmlLanguage: "ru-RU",
    title: "Панель Memora",
    subtitle: "Перед первым использованием",
    install: "Установить зависимости",
    build: "Собрать страницу",
    refresh: "Обновить страницу",
    output: "Лог вывода",
    waiting: "Ожидание действия...",
    manual: "Или выполните вручную:",
  },
] as const;

describe("dashboard bootstrap fallback", () => {
  beforeEach(() => {
    document.documentElement.innerHTML = "";
    document.documentElement.removeAttribute("lang");
    window.localStorage.clear();
    bootstrapTimeout = undefined;
    setBridge(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    setBridge(undefined);
    document.documentElement.innerHTML = "";
    document.documentElement.removeAttribute("lang");
  });

  it.each(staticLanguageCases)(
    "renders the standalone recovery page in $language",
    ({ language, htmlLanguage, title, subtitle, install, build, refresh, output, waiting, manual }) => {
      window.localStorage.setItem("lmem_lang", language);

      loadBootstrapPage();

      expect(document.documentElement.lang).toBe(htmlLanguage);
      expect(compactText(".bs-title")).toBe(title);
      expect(compactText(".bs-subtitle")).toContain(subtitle);
      expect(compactText("#bs-btn-install")).toContain(install);
      expect(compactText("#bs-btn-build")).toContain(build);
      expect(compactText("#bs-refresh button")).toContain(refresh);
      expect(compactText(".bs-log summary")).toBe(output);
      expect(compactText("#bs-log-content")).toBe(waiting);
      expect(compactText(".bs-footer")).toContain(manual);
      expect((document.getElementById("memora-bootstrap") as HTMLElement).hidden).toBe(false);
    },
  );

  it("gives the stored dashboard language priority over the bridge locale", () => {
    window.localStorage.setItem("lmem_lang", "ru");
    setBridge({
      getLocale: vi.fn().mockReturnValue("en-US"),
      getContext: vi.fn().mockReturnValue({ locale: "en-US" }),
    });

    loadBootstrapPage();

    expect(document.documentElement.lang).toBe("ru-RU");
    expect(compactText("#bs-btn-install")).toContain("Установить зависимости");
  });

  it("uses the bridge locale when there is no stored language", () => {
    setBridge({
      getLocale: vi.fn().mockReturnValue("ru-RU"),
      getContext: vi.fn().mockReturnValue({ locale: "en-US" }),
    });

    loadBootstrapPage();

    expect(document.documentElement.lang).toBe("ru-RU");
    expect(compactText(".bs-title")).toBe("Панель Memora");
  });

  it("falls back to the bridge context locale when getLocale fails", () => {
    setBridge({
      getLocale: vi.fn(() => {
        throw new Error("locale unavailable");
      }),
      getContext: vi.fn().mockReturnValue({ locale: "en-US" }),
    });

    loadBootstrapPage();

    expect(document.documentElement.lang).toBe("en-US");
    expect(compactText(".bs-title")).toBe("Memora Dashboard");
  });

  it("defaults to Chinese for unsupported stored and bridge locales", () => {
    window.localStorage.setItem("lmem_lang", "de");
    setBridge({ getLocale: vi.fn().mockReturnValue("ja-JP") });

    loadBootstrapPage();

    expect(document.documentElement.lang).toBe("zh-CN");
    expect(compactText(".bs-title")).toBe("Memora 管理面板");
  });

  it("shows a localized bridge hint when React and the bridge are unavailable", async () => {
    window.localStorage.setItem("lmem_lang", "ru");
    loadBootstrapPage();

    await (window as BootstrapWindow).doBootstrap("install");

    const bridgeError = document.getElementById("bs-error");
    const status = document.getElementById("bs-status");
    expect(bridgeError?.hidden).toBe(false);
    expect(bridgeError?.textContent).toContain("Ошибка моста");
    expect(status?.textContent).toContain("Мост AstrBot недоступен");
    expect((document.getElementById("memora-bootstrap") as HTMLElement).hidden).toBe(false);
  });

  it("does not mislabel localized backend install rejections as bridge errors", async () => {
    window.localStorage.setItem("lmem_lang", "zh");
    loadBootstrapPage();

    setBridge({
      apiPost: vi.fn().mockRejectedValue(
        new Error(
          "dashboard runtime build/install is disabled; set dashboard.allow_runtime_build=true to enable",
        ),
      ),
    });

    await (window as BootstrapWindow).doBootstrap("install");

    const bridgeError = document.getElementById("bs-error");
    const status = document.getElementById("bs-status");
    expect(bridgeError?.hidden).toBe(true);
    expect(status?.textContent).toContain("错误：");
    expect(status?.textContent).toContain(
      "dashboard runtime build/install is disabled; set dashboard.allow_runtime_build=true to enable",
    );
  });

  it("localizes the wrong-plugin explanation while preserving plugin names", async () => {
    window.localStorage.setItem("lmem_lang", "zh");
    setBridge({
      apiPost: vi.fn().mockResolvedValue({
        overview: {
          plugin: {
            name: "astrbot_plugin_self_learning",
            display_name: "Self Learning",
          },
        },
      }),
      getContext: vi.fn().mockReturnValue({
        pluginName: "astrbot_plugin_self_learning",
        displayName: "Self Learning",
        locale: "zh-CN",
        isDark: false,
      }),
    });
    loadBootstrapPage();

    await (window as BootstrapWindow).doBootstrap("install");

    const statusText = document.getElementById("bs-status")?.textContent ?? "";
    expect(statusText).toContain("插件面板错误");
    expect(statusText).toContain("Self Learning");
    expect(statusText).toContain("astrbot_plugin_self_learning");
    expect(statusText).toContain("Memora");
  });

  it.each([
    { language: "zh", action: "install" as const, successText: "依赖安装成功" },
    { language: "ru", action: "build" as const, successText: "Страница собрана" },
  ])("localizes successful $action feedback in $language", async ({ language, action, successText }) => {
    window.localStorage.setItem("lmem_lang", language);
    setBridge({
      apiPost: vi.fn().mockResolvedValue({
        command: "npm ci",
        stdout: "\nadded 568 packages\n",
        stderr: "npm warn deprecated package\n",
        exit_code: 0,
        success: true,
        timed_out: false,
      }),
    });
    loadBootstrapPage();

    await (window as BootstrapWindow).doBootstrap(action);

    const statusText = document.getElementById("bs-status")?.textContent ?? "";
    const logText = document.getElementById("bs-log-content")?.textContent ?? "";
    expect(statusText).toContain(successText);
    expect(document.getElementById("bs-refresh")?.hidden).toBe(false);
    expect(logText).toContain("added 568 packages");
    expect(logText).not.toContain("等待操作");
    expect(logText).not.toContain("Waiting for action");
    expect(logText).not.toContain("Ожидание действия");
  });

  it("escapes backend-provided error text before rendering it as status HTML", async () => {
    window.localStorage.setItem("lmem_lang", "en");
    setBridge({
      apiPost: vi.fn().mockResolvedValue({
        status: "error",
        message: '<img src=x onerror="window.__bootstrapInjected=true">',
      }),
    });
    loadBootstrapPage();

    await (window as BootstrapWindow).doBootstrap("install");

    const status = document.getElementById("bs-status");
    expect(status?.querySelector("img")).toBeNull();
    expect(status?.innerHTML).toContain("&lt;img");
    expect(status?.textContent).toContain('<img src=x onerror="window.__bootstrapInjected=true">');
  });

  it("keeps the recovery page visible until React has mounted real content", () => {
    loadBootstrapPage();
    const fallback = document.getElementById("memora-bootstrap") as HTMLElement;

    bootstrapTimeout?.();
    expect(fallback.hidden).toBe(false);

    const app = document.createElement("div");
    app.appendChild(document.createElement("main"));
    document.getElementById("root")?.appendChild(app);
    bootstrapTimeout?.();
    expect(fallback.hidden).toBe(true);
  });

  it("can execute repeatedly as a classic script", () => {
    const bootstrapScript = loadBootstrapPage();

    expect(() => window.eval(bootstrapScript)).not.toThrow();
  });
});
