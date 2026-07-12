import { describe, expect, it } from "vitest";

import type { ConfigSchemaNode } from "@/types/config";

import { filterConfigSections } from "./configSections";

const schema: Record<string, ConfigSchemaNode> = {
  identity: {
    type: "object",
    description: "Identity",
    hint: "How Memora presents itself",
    items: {
      bot_name: {
        type: "string",
        description: "Bot name",
        hint: "Shown in generated memories",
      },
      hidden_token: {
        type: "string",
        description: "Hidden token",
        invisible: true,
      },
    },
  },
  recall: {
    type: "object",
    description: "Recall engine",
    items: {
      mode: {
        type: "string",
        description: "Recall mode",
        options: ["hybrid", "vector"],
      },
      limits: {
        type: "object",
        description: "Retrieval limits",
        items: {
          top_k: {
            type: "int",
            description: "Maximum memories",
          },
          threshold: {
            type: "float",
            description: "Score threshold",
          },
        },
      },
    },
  },
  diagnostics_enabled: {
    type: "bool",
    description: "Diagnostics enabled",
  },
};

describe("filterConfigSections", () => {
  it("preserves top-level schema order and creates stable unique section ids", () => {
    const first = filterConfigSections(schema, {
      query: "",
      modifiedOnly: false,
      dirtyPaths: [],
    });
    const second = filterConfigSections(schema, {
      query: "",
      modifiedOnly: false,
      dirtyPaths: [],
    });

    expect(first.map((section) => section.path)).toEqual([
      "identity",
      "recall",
      "diagnostics_enabled",
    ]);
    expect(first.map((section) => section.id)).toEqual(
      second.map((section) => section.id),
    );
    expect(new Set(first.map((section) => section.id)).size).toBe(3);
    expect(first[0].node).toMatchObject({
      type: "object",
      items: { bot_name: expect.any(Object) },
    });
    expect(
      first[0].node.type === "object"
        ? first[0].node.items.hidden_token
        : undefined,
    ).toBeUndefined();
  });

  it.each([
    ["recall.limits.top_k", "Maximum memories"],
    ["generated memories", "Bot name"],
    ["vector", "Recall mode"],
  ])("searches dotted keys, hints, descriptions, and option labels for %s", (query, expectedDescription) => {
    const sections = filterConfigSections(schema, {
      query,
      modifiedOnly: false,
      dirtyPaths: [],
    });

    const serialized = JSON.stringify(sections);
    expect(serialized).toContain(expectedDescription);
  });

  it("retains every visible descendant when an object group itself matches", () => {
    const sections = filterConfigSections(schema, {
      query: "Recall engine",
      modifiedOnly: false,
      dirtyPaths: [],
    });

    expect(sections).toHaveLength(1);
    expect(sections[0].path).toBe("recall");
    expect(sections[0].node).toMatchObject({
      type: "object",
      items: {
        mode: expect.any(Object),
        limits: {
          items: {
            top_k: expect.any(Object),
            threshold: expect.any(Object),
          },
        },
      },
    });
  });

  it("keeps only dirty leaves and their containing object hierarchy", () => {
    const sections = filterConfigSections(schema, {
      query: "",
      modifiedOnly: true,
      dirtyPaths: ["recall.limits.top_k"],
    });

    expect(sections).toHaveLength(1);
    expect(sections[0].path).toBe("recall");
    expect(sections[0].dirtyCount).toBe(1);
    expect(sections[0].node).toMatchObject({
      type: "object",
      items: {
        limits: {
          type: "object",
          items: { top_k: expect.any(Object) },
        },
      },
    });
    const recallItems =
      sections[0].node.type === "object" ? sections[0].node.items : {};
    expect(recallItems.mode).toBeUndefined();
    expect(
      recallItems.limits?.type === "object"
        ? recallItems.limits.items.threshold
        : undefined,
    ).toBeUndefined();
  });

  it("composes search with modified-only and returns no sections when the dirty leaf does not match", () => {
    expect(
      filterConfigSections(schema, {
        query: "threshold",
        modifiedOnly: true,
        dirtyPaths: ["recall.limits.top_k"],
      }),
    ).toEqual([]);

    const matching = filterConfigSections(schema, {
      query: "maximum memories",
      modifiedOnly: true,
      dirtyPaths: ["recall.limits.top_k"],
    });
    expect(matching).toHaveLength(1);
    expect(JSON.stringify(matching[0].node)).toContain("Maximum memories");
  });
});
