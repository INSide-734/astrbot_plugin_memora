import { describe, expect, it } from "vitest";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { normalizeHtmlLineEndings } from "../buildUtils";

describe("vite config", () => {
  it("keeps filesystem-mutating plugins scoped to production builds", () => {
    const configSource = readFileSync(resolve(__dirname, "../vite.config.ts"), "utf-8");
    const mutatingPluginNames = [
      "clean-old-assets",
      "preserve-index-source",
      "sync-dashboard-build-output",
    ];

    for (const name of mutatingPluginNames) {
      const pluginPattern = new RegExp(`name:\\s*["']${name}["'][\\s\\S]*?apply:\\s*["']build["']`);
      expect(configSource, `${name} must set apply: "build"`).toMatch(pluginPattern);
    }
  });

  it("normalizes mixed Windows line endings in the synced index", () => {
    expect(normalizeHtmlLineEndings("first\r\nsecond\nthird\rfourth")).toBe(
      "first\nsecond\nthird\nfourth",
    );
  });
});
