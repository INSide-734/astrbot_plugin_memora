// @vitest-environment node

import { describe, expect, it } from "vitest";

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createServer } from "vite";

import { normalizeHtmlLineEndings } from "../buildUtils";

interface ConfigSchemaPlugin {
  resolveId(id: string): string | null;
  load(id: string): string | null;
}

function countSchemaLeaves(schema: Record<string, unknown>): number {
  return Object.values(schema).reduce<number>((count, rawNode) => {
    const node = rawNode as {
      type?: string;
      items?: Record<string, unknown>;
    };
    return (
      count +
      (node.type === "object" && node.items
        ? countSchemaLeaves(node.items)
        : 1)
    );
  }, 0);
}

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

  it("loads only the root config schema through an exact virtual module", async () => {
    const dashboardRoot = resolve(__dirname, "..");
    const configSource = readFileSync(
      resolve(dashboardRoot, "vite.config.ts"),
      "utf-8"
    );
    expect(configSource).not.toContain(
      'allow: [path.resolve(ROOT_DIR, "../..")]'
    );
    const mockConfigSource = readFileSync(
      resolve(dashboardRoot, "src/mock/configServer.ts"),
      "utf-8"
    );
    expect(mockConfigSource).toContain("virtual:memora-config-schema");
    expect(mockConfigSource).not.toContain("_conf_schema.json?raw");

    const configModule = (await import("../vite.config")) as unknown as {
      memoraConfigSchemaPlugin?: () => ConfigSchemaPlugin;
    };
    expect(configModule.memoraConfigSchemaPlugin).toBeTypeOf("function");
    if (!configModule.memoraConfigSchemaPlugin) {
      throw new Error("Config schema plugin factory is unavailable");
    }

    const plugin = configModule.memoraConfigSchemaPlugin();
    const virtualId = "virtual:memora-config-schema";
    const resolvedId = plugin.resolveId(virtualId);
    expect(resolvedId).toBe("\0virtual:memora-config-schema");
    expect(plugin.resolveId(`${virtualId}/other`)).toBeNull();
    expect(plugin.resolveId("../../_conf_schema.json")).toBeNull();
    expect(plugin.load("\0virtual:other")).toBeNull();

    const loaded = plugin.load(resolvedId!);
    expect(loaded).toMatch(/^export default .+;$/s);
    const schemaText = JSON.parse(
      loaded!.slice("export default ".length, -1)
    ) as string;
    const schema = JSON.parse(schemaText) as Record<string, unknown>;
    expect(Object.keys(schema)).toHaveLength(42);
    expect(countSchemaLeaves(schema)).toBe(229);
    expect(schema).not.toHaveProperty("index_management");
    expect(schema).toMatchObject({
      jargon: {
        type: "object",
        items: {
          enabled: { type: "bool", default: false },
        },
      },
    });

    const server = await createServer({
      root: dashboardRoot,
      logLevel: "silent",
      server: { middlewareMode: true },
    });
    try {
      const transformed = await server.transformRequest(
        "/src/mock/configServer.ts"
      );
      expect(transformed?.code).toContain("virtual:memora-config-schema");
    } finally {
      await server.close();
    }
  });
});
