import { describe, expect, it } from "vitest";

import type { Translate } from "@/lib/i18n";
import { DASHBOARD_NAVIGATION } from "@/lib/navigation";
import type { ConfigSchemaNode } from "@/types/config";

import {
  buildConfigSearchEntries,
  buildPageSearchEntries,
  highlightSegments,
  searchLocalEntries,
  type LocalSearchEntry,
} from "./globalSearch";

const providerSchema = {
  provider_settings: {
    type: "object",
    description: "Provider settings",
    items: {
      llm_provider_id: {
        type: "string",
        description: "LLM provider",
        hint: "Choose the provider used for chat completions",
        options: ["openai", "local"],
      },
      secret_token: {
        type: "string",
        description: "Secret token",
        invisible: true,
      },
    },
  },
} satisfies Record<string, ConfigSchemaNode>;

function searchEntry(
  id: string,
  order: number,
  fields: Partial<LocalSearchEntry> = {},
): LocalSearchEntry {
  const title = fields.title ?? id;
  const path = fields.path ?? id;
  const description = fields.normalizedDescription ?? "";
  const hint = fields.normalizedHint ?? "";
  const options = fields.normalizedOptions ?? "";

  return {
    id,
    kind: fields.kind ?? "page",
    title,
    subtitle: fields.subtitle ?? path,
    path,
    order,
    searchable: fields.searchable ?? [title, path, description, hint, options].join(" ").toLocaleLowerCase(),
    normalizedTitle: fields.normalizedTitle ?? title.trim().toLocaleLowerCase(),
    normalizedPath: fields.normalizedPath ?? path.trim().toLocaleLowerCase(),
    normalizedDescription: description,
    normalizedHint: hint,
    normalizedOptions: options,
    page: fields.page,
    parentPath: fields.parentPath,
  };
}

describe("global dashboard search helpers", () => {
  it("flattens visible configuration groups and fields in traversal order", () => {
    const entries = buildConfigSearchEntries(providerSchema);

    expect(entries.map(({ kind, path }) => ({ kind, path }))).toEqual([
      { kind: "config-group", path: "provider_settings" },
      { kind: "config-field", path: "provider_settings.llm_provider_id" },
    ]);
    expect(entries[0]).toMatchObject({ parentPath: null });
    expect(entries[1]).toMatchObject({
      id: "config:provider_settings.llm_provider_id",
      title: "LLM provider",
      subtitle: "Choose the provider used for chat completions",
      parentPath: "provider_settings",
    });
  });

  it("skips malformed runtime schema nodes without dropping valid siblings", () => {
    const entries = buildConfigSearchEntries({
      missing: null,
      malformed_group: { type: "object", items: [] },
      valid: { type: "bool", description: "Valid setting" },
    });

    expect(entries.map((entry) => entry.path)).toEqual(["valid"]);
  });

  it("matches every query token across searchable fields and favors exact titles", () => {
    const entries = buildConfigSearchEntries(providerSchema);

    expect(searchLocalEntries(entries, "LLM local", 10).items.map((entry) => entry.path)).toEqual([
      "provider_settings.llm_provider_id",
    ]);
    expect(searchLocalEntries(entries, "Provider settings", 10).items[0]?.path).toBe(
      "provider_settings",
    );

    const entriesWithFallback = buildConfigSearchEntries({
      alpha_fallback: { type: "string", hint: "provider settings" },
      ...providerSchema,
    });
    expect(
      searchLocalEntries(
        entriesWithFallback,
        "Provider   provider settings",
        10,
      ).items[0]?.path,
    ).toBe("provider_settings");
  });

  it("reports total matches independently from the display limit", () => {
    const entries = Array.from({ length: 10 }, (_, index) =>
      searchEntry(`page:${index}`, index, {
        title: `Page ${index}`,
        path: `page-${index}`,
      }),
    );

    const result = searchLocalEntries(entries, "page", 5);

    expect(result.total).toBe(10);
    expect(result.items).toHaveLength(5);
  });

  it("ranks description matches above hint matches and option-only matches", () => {
    const entries = [
      searchEntry("option", 0, {
        normalizedOptions: "needle",
        searchable: "option needle",
      }),
      searchEntry("hint", 1, {
        normalizedHint: "needle",
        searchable: "hint needle",
      }),
      searchEntry("description", 2, {
        normalizedDescription: "needle",
        searchable: "description needle",
      }),
    ];

    expect(searchLocalEntries(entries, "needle", 10).items.map((entry) => entry.id)).toEqual([
      "description",
      "hint",
      "option",
    ]);
  });

  it("orders tied pages before configurations with deterministic kind-specific rules", () => {
    const configEntries = buildConfigSearchEntries({
      zeta: { type: "string", hint: "shared match" },
      alpha: { type: "string", hint: "shared match" },
    });
    const entries = [
      ...configEntries,
      searchEntry("page-first", 0, {
        path: "zzz-page",
        normalizedHint: "shared",
        searchable: "shared",
      }),
      searchEntry("page-second", 1, {
        path: "aaa-page",
        normalizedHint: "shared",
        searchable: "shared",
      }),
    ];

    const expectedIds = [
      "page-first",
      "page-second",
      "config:alpha",
      "config:zeta",
    ];

    for (const input of [entries, [...entries].reverse()]) {
      expect(searchLocalEntries(input, "shared", 10).items.map((entry) => entry.id)).toEqual(
        expectedIds,
      );
    }
  });

  it("returns safe text segments for each highlighted query token", () => {
    expect(highlightSegments("LLM provider local", "provider local")).toEqual([
      { text: "LLM ", matched: false },
      { text: "provider", matched: true },
      { text: " ", matched: false },
      { text: "local", matched: true },
    ]);
    expect(highlightSegments("A+B then a+b", "a+b")).toEqual([
      { text: "A+B", matched: true },
      { text: " then ", matched: false },
      { text: "a+b", matched: true },
    ]);
    expect(highlightSegments("Provider pro", "pro provider")).toEqual([
      { text: "Provider", matched: true },
      { text: " ", matched: false },
      { text: "pro", matched: true },
    ]);
    expect(highlightSegments("I i", "i")).toEqual([
      { text: "I", matched: true },
      { text: " ", matched: false },
      { text: "i", matched: true },
    ]);
  });

  it("builds localized page entries from the shared navigation catalog", () => {
    const t: Translate = (key) => {
      if (key === "nav.config") return "Configuration";
      if (key.startsWith("nav.group")) return `Page group ${key}`;
      return `Page ${key}`;
    };
    const entries = buildPageSearchEntries(t);

    expect(searchLocalEntries(entries, "Configuration", 10).items.map((entry) => entry.page)).toEqual([
      "config",
    ]);
    expect(searchLocalEntries(entries, "Page", 100).items.map((entry) => entry.page)).toEqual(
      DASHBOARD_NAVIGATION.flatMap((group) => group.items.map((item) => item.id)),
    );

    const injectionEntries = buildPageSearchEntries((key) => {
      if (key === "nav.injection") return "Injection Strategy";
      if (key.startsWith("nav.group")) return `Group ${key}`;
      return `Localized ${key}`;
    });
    expect(
      searchLocalEntries(injectionEntries, "Injection Strategy", 10).items
        .map((entry) => entry.page),
    ).toEqual(["injection"]);

    const injection = entries.find((entry) => entry.id === "page:injection");
    expect(injection).toMatchObject({
      id: "page:injection",
      page: "injection",
    });
    expect(entries.map((entry) => entry.page)).toEqual(
      DASHBOARD_NAVIGATION.flatMap((group) => group.items.map((item) => item.id)),
    );
  });
});
