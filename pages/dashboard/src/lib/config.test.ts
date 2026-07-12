import { describe, expect, it } from "vitest";

import type {
  ConfigApplyRequest,
  ConfigObject,
} from "@/types/config";

import {
  applyConfigChanges,
  buildConfigChanges,
  cloneConfig,
  configValueEquals,
  diffConfigLeafPaths,
  getConfigValue,
  rebaseConfig,
  setConfigValue,
  toJsonConfigChanges,
} from "./config";

const DANGEROUS_SEGMENTS = ["__proto__", "prototype", "constructor"] as const;
const NON_JSON_VALUES: ReadonlyArray<readonly [string, unknown]> = [
  ["undefined", undefined],
  ["NaN", Number.NaN],
  ["positive infinity", Number.POSITIVE_INFINITY],
  ["negative infinity", Number.NEGATIVE_INFINITY],
  ["a function", () => true],
  ["a symbol", Symbol("invalid-config-value")],
];

class CustomConfigValue {
  readonly value = 1;
}

const INVALID_JSON_CONTAINERS: ReadonlyArray<
  readonly [string, () => unknown]
> = [
  ["a Date", () => new Date("2026-07-13T00:00:00Z")],
  ["a Map", () => new Map([["value", 1]])],
  ["a Set", () => new Set([1])],
  ["a RegExp", () => /memora/],
  ["a boxed primitive", () => new Number(1)],
  ["a custom class instance", () => new CustomConfigValue()],
  [
    "a sparse array",
    () => {
      const value = new Array<unknown>(2);
      value[1] = true;
      return value;
    },
  ],
  [
    "an array with an enumerable string key",
    () => {
      const value: unknown[] = [true];
      Object.defineProperty(value, "extra", {
        enumerable: true,
        value: "unexpected",
      });
      return value;
    },
  ],
  [
    "an array with an enumerable symbol key",
    () => {
      const value: unknown[] = [true];
      Object.defineProperty(value, Symbol("extra"), {
        enumerable: true,
        value: "unexpected",
      });
      return value;
    },
  ],
  [
    "an array with a dangerous own key",
    () => {
      const value: unknown[] = [true];
      Object.defineProperty(value, "__proto__", {
        configurable: true,
        enumerable: true,
        value: { polluted: true },
      });
      return value;
    },
  ],
];

function configWithOwnKey(key: string, value: ConfigObject): ConfigObject {
  const config: ConfigObject = {};
  Object.defineProperty(config, key, {
    configurable: true,
    enumerable: true,
    value,
    writable: true,
  });
  return config;
}

function expectObjectPrototypeClean(): void {
  expect(Object.getPrototypeOf(Object.prototype)).toBeNull();
  expect(Object.prototype).not.toHaveProperty("polluted");
}

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

  it.each(DANGEROUS_SEGMENTS)(
    "rejects the dangerous %s segment when setting a dotted path",
    (segment) => {
      const original: ConfigObject = { stable: { value: true } };
      const originalPrototype = Object.getPrototypeOf(original);
      let produced: ConfigObject | undefined;

      expect(() => {
        produced = setConfigValue(
          original,
          `target.${segment}.polluted`,
          true
        );
      }).toThrow(/config|key|path|segment/i);

      expect(Object.getPrototypeOf(original)).toBe(originalPrototype);
      expect(original).toEqual({ stable: { value: true } });
      if (produced) {
        expect(Object.getPrototypeOf(produced)).toBe(Object.prototype);
        expect(Object.getPrototypeOf(produced.target as ConfigObject)).toBe(
          Object.prototype
        );
      }
      expectObjectPrototypeClean();
    }
  );

  it("does not read an inherited value while creating a missing path branch", () => {
    const inherited = Object.defineProperty({}, "branch", {
      get: () => {
        throw new Error("inherited getter must not run");
      },
    });
    const original = Object.create(inherited) as ConfigObject;

    const updated = setConfigValue(original, "branch.leaf", true);

    expect(updated.branch).toEqual({ leaf: true });
    expect(Object.prototype.hasOwnProperty.call(original, "branch")).toBe(false);
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

  it.each(DANGEROUS_SEGMENTS)(
    "rejects the dangerous own key %s while cloning",
    (segment) => {
      const original = configWithOwnKey(segment, { polluted: true });
      const originalPrototype = Object.getPrototypeOf(original);
      let produced: ConfigObject | undefined;

      expect(() => {
        produced = cloneConfig(original);
      }).toThrow(/config|key|path|segment/i);

      expect(Object.getPrototypeOf(original)).toBe(originalPrototype);
      expect(Object.prototype.hasOwnProperty.call(original, segment)).toBe(true);
      if (produced) {
        expect(Object.getPrototypeOf(produced)).toBe(Object.prototype);
      }
      expectObjectPrototypeClean();
    }
  );

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

  it("constructs an exact JSON-only apply request", () => {
    const request: ConfigApplyRequest = {
      base_revision: "rev-1",
      changes: toJsonConfigChanges({
        "provider_settings.ids": ["primary", null],
        "recall_engine.top_k": 12,
      }),
    };

    expect(request).toEqual({
      base_revision: "rev-1",
      changes: {
        "provider_settings.ids": ["primary", null],
        "recall_engine.top_k": 12,
      },
    });
  });

  it.each(NON_JSON_VALUES)(
    "rejects %s recursively at the JSON changes boundary",
    (_label, value) => {
      expect(() =>
        toJsonConfigChanges({
          "group.value": { nested: [value] },
        })
      ).toThrow(/json/i);
    }
  );

  it.each(INVALID_JSON_CONTAINERS)(
    "rejects %s instead of silently coercing it at the JSON boundary",
    (_label, createValue) => {
      expect(() =>
        toJsonConfigChanges({
          "group.value": { nested: createValue() },
        })
      ).toThrow(/json/i);
    }
  );

  it("accepts a JSON record with a null prototype", () => {
    const record = Object.create(null) as Record<string, unknown>;
    record.enabled = true;
    record.values = [1, null, "ok"];

    expect(toJsonConfigChanges({ "group.value": record })).toEqual({
      "group.value": {
        enabled: true,
        values: [1, null, "ok"],
      },
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

  it.each(DANGEROUS_SEGMENTS)(
    "rejects the dangerous %s segment while applying changes",
    (segment) => {
      const remote: ConfigObject = { stable: true };
      const remotePrototype = Object.getPrototypeOf(remote);
      let produced: ConfigObject | undefined;

      expect(() => {
        produced = applyConfigChanges(remote, {
          [`target.${segment}.polluted`]: true,
        });
      }).toThrow(/config|key|path|segment/i);

      expect(remote).toEqual({ stable: true });
      expect(Object.getPrototypeOf(remote)).toBe(remotePrototype);
      if (produced) {
        expect(Object.getPrototypeOf(produced)).toBe(Object.prototype);
        expect(Object.getPrototypeOf(produced.target as ConfigObject)).toBe(
          Object.prototype
        );
      }
      expectObjectPrototypeClean();
    }
  );

  it.each(DANGEROUS_SEGMENTS)(
    "rejects the dangerous %s segment while rebasing changes",
    (segment) => {
      const remote: ConfigObject = { stable: true };
      const localDraft: ConfigObject = {
        stable: true,
        target: configWithOwnKey(segment, { polluted: true }),
      };
      const remotePrototype = Object.getPrototypeOf(remote);
      let produced: ConfigObject | undefined;

      expect(() => {
        produced = rebaseConfig(remote, localDraft, [
          `target.${segment}.polluted`,
        ]);
      }).toThrow(/config|key|path|segment/i);

      expect(remote).toEqual({ stable: true });
      expect(Object.getPrototypeOf(remote)).toBe(remotePrototype);
      expect(Object.prototype.hasOwnProperty.call(localDraft.target, segment)).toBe(
        true
      );
      if (produced) {
        expect(Object.getPrototypeOf(produced)).toBe(Object.prototype);
        expect(Object.getPrototypeOf(produced.target as ConfigObject)).toBe(
          Object.prototype
        );
      }
      expectObjectPrototypeClean();
    }
  );
});
