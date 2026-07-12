import { describe, expect, it } from "vitest";

import type { ConfigObject } from "@/types/config";

import {
  applyConfigChanges,
  buildConfigChanges,
  cloneConfig,
  configValueEquals,
  diffConfigLeafPaths,
  getConfigValue,
  rebaseConfig,
  setConfigValue,
} from "./config";

describe("config dotted-path helpers", () => {
  it("reads nested values without collapsing null into an absent value", () => {
    const config = {
      provider_settings: {
        llm_provider_id: "llm-primary",
        optional: null,
      },
    };

    expect(getConfigValue(config, "provider_settings.llm_provider_id")).toBe(
      "llm-primary"
    );
    expect(getConfigValue(config, "provider_settings.optional")).toBeNull();
    expect(getConfigValue(config, "provider_settings.missing")).toBeUndefined();
    expect(getConfigValue(config, "missing.child")).toBeUndefined();
  });

  it("sets a dotted path immutably while preserving unrelated branches", () => {
    const untouched = { enabled: true };
    const original = {
      recall_engine: { top_k: 8, mode: "hybrid" },
      untouched,
    };

    const updated = setConfigValue(original, "recall_engine.top_k", 12);

    expect(updated).toEqual({
      recall_engine: { top_k: 12, mode: "hybrid" },
      untouched,
    });
    expect(updated).not.toBe(original);
    expect(updated.recall_engine).not.toBe(original.recall_engine);
    expect(updated.untouched).toBe(untouched);
    expect(original.recall_engine.top_k).toBe(8);
  });

  it("creates missing object branches and retains an explicitly undefined leaf", () => {
    const updated = setConfigValue<ConfigObject>(
      {},
      "new_group.optional",
      undefined
    );

    expect(Object.prototype.hasOwnProperty.call(updated, "new_group")).toBe(true);
    const newGroup = updated.new_group as ConfigObject;
    expect(
      Object.prototype.hasOwnProperty.call(newGroup, "optional")
    ).toBe(true);
    expect(newGroup.optional).toBeUndefined();
  });

  it("rejects empty or malformed dotted paths", () => {
    expect(() => setConfigValue({}, "", true)).toThrow(/path/i);
    expect(() => setConfigValue({}, "group..field", true)).toThrow(/path/i);
  });

  it("returns sorted changed leaf paths across nested objects", () => {
    const before = {
      zeta: 1,
      recall: { top_k: 8, mode: "hybrid" },
      removed: { leaf: "old" },
    };
    const after = {
      zeta: 2,
      recall: { top_k: 12, mode: "hybrid", alpha: true },
      added: { leaf: "new" },
    };

    expect(diffConfigLeafPaths(before, after)).toEqual([
      "added.leaf",
      "recall.alpha",
      "recall.top_k",
      "removed.leaf",
      "zeta",
    ]);
  });

  it("distinguishes absent, undefined, and null values", () => {
    expect(configValueEquals({}, { optional: undefined })).toBe(false);
    expect(configValueEquals({ optional: undefined }, { optional: undefined })).toBe(
      true
    );
    expect(configValueEquals({ optional: undefined }, { optional: null })).toBe(
      false
    );
    expect(diffConfigLeafPaths({}, { optional: undefined })).toEqual([
      "optional",
    ]);
    expect(diffConfigLeafPaths({ optional: undefined }, { optional: null })).toEqual([
      "optional",
    ]);
  });

  it("deeply clones objects and arrays without dropping undefined properties", () => {
    const original = {
      nested: { optional: undefined, list: [1, { value: null }] },
    };

    const cloned = cloneConfig(original);

    expect(configValueEquals(cloned, original)).toBe(true);
    expect(cloned).not.toBe(original);
    expect(cloned.nested).not.toBe(original.nested);
    expect(cloned.nested.list).not.toBe(original.nested.list);
    expect(Object.prototype.hasOwnProperty.call(cloned.nested, "optional")).toBe(
      true
    );
  });

  it("builds a deterministic dotted changes payload from the draft", () => {
    const draft = {
      recall_engine: { top_k: 12 },
      provider_settings: { llm_provider_id: "llm-primary" },
    };

    expect(
      buildConfigChanges(draft, [
        "recall_engine.top_k",
        "provider_settings.llm_provider_id",
        "recall_engine.top_k",
      ])
    ).toEqual({
      "provider_settings.llm_provider_id": "llm-primary",
      "recall_engine.top_k": 12,
    });
  });

  it("applies dotted changes and rebases local dirty values onto remote config", () => {
    const remote = {
      recall_engine: { top_k: 20, mode: "vector" },
      remote_only: true,
    };
    const localDraft = {
      recall_engine: { top_k: 12, mode: "hybrid" },
      remote_only: false,
    };

    const applied = applyConfigChanges(remote, {
      "recall_engine.mode": "keyword",
    });
    const rebased = rebaseConfig(remote, localDraft, ["recall_engine.top_k"]);

    expect(applied).toEqual({
      recall_engine: { top_k: 20, mode: "keyword" },
      remote_only: true,
    });
    expect(remote.recall_engine.mode).toBe("vector");
    expect(rebased).toEqual({
      recall_engine: { top_k: 12, mode: "vector" },
      remote_only: true,
    });
  });
});
