import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const currentDir = dirname(fileURLToPath(import.meta.url));
const dashboardHtml = readFileSync(resolve(currentDir, "../index.html"), "utf-8");

function loadBootstrapPage(): void {
  document.documentElement.innerHTML = dashboardHtml;

  const scripts = Array.from(document.querySelectorAll("script"));
  const bootstrapScript = scripts[scripts.length - 1]?.textContent;
  if (!bootstrapScript) {
    throw new Error("Bootstrap script not found in dashboard index.html");
  }

  vi.spyOn(window, "setTimeout").mockImplementation(() => 0 as unknown as ReturnType<typeof setTimeout>);
  window.eval(bootstrapScript);
}

describe("dashboard bootstrap fallback", () => {
  beforeEach(() => {
    document.documentElement.innerHTML = "";
  });

  afterEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
    document.documentElement.innerHTML = "";
  });

  it("shows the bridge hint when the AstrBot bridge is unavailable", async () => {
    loadBootstrapPage();

    await (window as typeof window & { doBootstrap: (action: "install" | "build") => Promise<void> }).doBootstrap("install");

    const bridgeError = document.getElementById("bs-error");
    const status = document.getElementById("bs-status");

    expect(bridgeError?.hidden).toBe(false);
    expect(bridgeError?.textContent).toContain("Bridge error");
    expect(status?.innerHTML).toContain("AstrBot bridge not available");
  });

  it("does not mislabel backend install rejections as bridge errors", async () => {
    loadBootstrapPage();

    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: {
        apiPost: vi.fn().mockRejectedValue(
          new Error(
            "dashboard runtime build/install is disabled; set dashboard.allow_runtime_build=true to enable"
          )
        ),
      },
    });

    await (window as typeof window & { doBootstrap: (action: "install" | "build") => Promise<void> }).doBootstrap("install");

    const bridgeError = document.getElementById("bs-error");
    const status = document.getElementById("bs-status");

    expect(bridgeError?.hidden).toBe(true);
    expect(status?.innerHTML).toContain(
      "dashboard runtime build/install is disabled; set dashboard.allow_runtime_build=true to enable"
    );
  });

  it("explains when the page is running inside a different plugin panel", async () => {
    loadBootstrapPage();

    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: {
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
      },
    });

    await (window as typeof window & { doBootstrap: (action: "install" | "build") => Promise<void> }).doBootstrap("install");

    const bridgeError = document.getElementById("bs-error");
    const status = document.getElementById("bs-status");
    const statusText = status?.textContent ?? "";

    expect(bridgeError?.hidden).toBe(true);
    expect(statusText).toContain("Self Learning");
    expect(statusText).toContain("Memora");
    expect(statusText.toLowerCase()).toContain("wrong plugin panel");
  });

  it("accepts direct command payloads without an ok envelope", async () => {
    loadBootstrapPage();

    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: {
        apiPost: vi.fn().mockResolvedValue({
          command: "npm ci",
          stdout: "\nadded 568 packages\n",
          stderr: "npm warn deprecated package\n",
          exit_code: 0,
          success: true,
          timed_out: false,
        }),
      },
    });

    await (window as typeof window & { doBootstrap: (action: "install" | "build") => Promise<void> }).doBootstrap("install");

    const status = document.getElementById("bs-status");
    const refresh = document.getElementById("bs-refresh");
    const log = document.getElementById("bs-log-content");
    const statusText = status?.textContent ?? "";

    expect(statusText).toContain("Success");
    expect(statusText).toContain("Dependencies installed");
    expect(refresh?.hidden).toBe(false);
    expect(log?.textContent ?? "").toContain("added 568 packages");
  });
});
