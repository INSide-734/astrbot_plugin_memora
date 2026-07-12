import fs from "node:fs";
import path from "node:path";

import ts from "typescript";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MOOD_TYPES, RELATION_CATEGORIES } from "../lib/constants";
import { EN_MAP, I18N_MAP, RU_MAP } from "./index";

const SOURCE_ROOT = path.resolve(process.cwd(), "src");

function productionSourceFiles(directory: string): string[] {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) return productionSourceFiles(entryPath);
    if (!/\.tsx?$/.test(entry.name) || entry.name.includes(".test.")) return [];
    return [entryPath];
  });
}

function staticTranslationKeys(filePath: string): string[] {
  const source = fs.readFileSync(filePath, "utf8");
  const sourceFile = ts.createSourceFile(
    filePath,
    source,
    ts.ScriptTarget.Latest,
    true,
    filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const keys: string[] = [];

  function visit(node: ts.Node): void {
    if (
      ts.isCallExpression(node)
      && ts.isIdentifier(node.expression)
      && node.expression.text === "t"
      && node.arguments.length > 0
    ) {
      const key = node.arguments[0];
      if (ts.isStringLiteral(key) || ts.isNoSubstitutionTemplateLiteral(key)) {
        keys.push(key.text);
      }
    }
    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
  return keys;
}

const REQUIRED_KEYS = [
  "common.close",
  "header.openMenu",
  "nav.config",
  "config.unsaved.title",
  "config.unsaved.description",
  "config.unsaved.keepEditing",
  "config.unsaved.discard",
  "common.loading",
  "graph.exitFullscreen",
  "graph.temporalEdges",
  "graph.timeRange",
  "pagination.label",
  "pagination.pageOf",
  "kb.pagination",
  "profiles.pagination",
  "memory.selectItem",
  "notes.selectNote",
  "profiles.selectProfile",
  "kb.selectEntry",
  "profiles.details",
  "system.export",
  "system.exporting",
] as const;

const KNOWN_RELATION_TYPES = [
  "parent_child", "siblings", "relatives", "neighbor", "fellow_town",
  "fellow_passenger", "colleague", "mentor_mentee", "classmate", "lover",
  "best_friend", "ambiguous", "rival", "board_game_friend", "gaming_teammate",
  "core_intimate", "daily_normal", "stranger", "acquaintance", "friend",
  "close_friend", "confidant",
] as const;

const DYNAMIC_KEYS = [
  ...MOOD_TYPES.map((mood) => `mood.${mood.type}`),
  ...Object.keys(RELATION_CATEGORIES).map((category) => `social.category.${category}`),
  ...KNOWN_RELATION_TYPES.map((relation) => `relation.${relation}`),
  ...["fact", "concept", "rule", "event", "procedure"]
    .map((category) => `category.${category}`),
  ...["general", "episodic", "factual", "preference", "relational", "planned", "unknown", "other", "fact", "note", "summary", "event", "relation"]
    .map((type) => `memory.type.${type}`),
  ...["active", "archived", "deleted"].map((status) => `status.${status}`),
  ...["low", "medium", "high", "critical", "info", "warning", "error"]
    .map((severity) => `severity.${severity}`),
  ...["open", "approved", "edited", "merged", "archived", "deleted", "safe"]
    .map((status) => `intelligence.review.status.${status}`),
  ...["low_confidence", "duplicate", "conflict", "sensitive", "stale", "noisy", "provenance_missing"]
    .map((reason) => `intelligence.review.reason.${reason}`),
  ...[
    "healthy", "watch", "degraded", "critical", "info", "open", "resolved", "idle",
    "running", "stopping", "completed", "completed_with_errors", "cancelled", "failed",
    "waiting", "ready", "unavailable", "skipped", "error", "active", "inactive", "paused",
    "pending", "unknown",
  ].map((status) => `runtime.status.${status}`),
  ...["overall", "consistency", "coherence", "relevance", "freshness", "accuracy"]
    .map((dimension) => `quality.dim.${dimension}`),
  ...["baseline", "graph_expansion_off", "topic_expansion_off"]
    .map((variant) => `intelligence.evaluation.variant.${variant}`),
  ...["private", "group"].map((chatType) => `intelligence.trace.chatType.${chatType}`),
  ...["approve", "mark_safe", "edit", "merge", "archive", "delete", "approved", "edited", "merged", "archived", "deleted", "safe"]
    .map((action) => `intelligence.review.action.${action}`),
  ...["healthy", "watch", "degraded", "critical"]
    .map((level) => `intelligence.diagnostics.level.${level}`),
  ...["healthy", "watch", "degraded", "critical", "info", "unknown", "ok", "resolved", "failed", "error", "warning"]
    .map((status) => `intelligence.diagnostics.status.${status}`),
  ...["info", "warning", "critical", "error", "degraded"]
    .map((severity) => `intelligence.diagnostics.severity.${severity}`),
  ...["open", "resolved"].map((state) => `intelligence.diagnostics.state.${state}`),
  ...["hostile", "disliked", "cold", "neutral", "warm", "friendly", "close", "intimate"]
    .map((level) => `affection.levelValue.${level}`),
  ...["weight_adjust", "threshold_tune", "correction", "param_init"]
    .map((action) => `learning.historyAction.${action}`),
  ...["low_score", "topic_mismatch", "missing_fields"]
    .map((reason) => `intelligence.trace.filterReason.${reason}`),
  ...["search_memories", "query_parse", "bm25", "vector", "graph", "merge", "boost", "rerank"]
    .map((stage) => `intelligence.trace.stage.${stage}`),
  ...["bm25", "vector", "emotion_boost", "graph", "optimizer"]
    .map((source) => `intelligence.trace.source.${source}`),
  ...["provider", "recall", "write", "scheduler", "index", "prometheus"]
    .map((domain) => `intelligence.diagnostics.domain.${domain}`),
] as const;

function placeholders(value: string): string[] {
  return [...value.matchAll(/\{(\d+)\}/g)].map((match) => match[1]).sort();
}

describe("dashboard i18n dictionaries", () => {
  it("keeps Chinese, English, and Russian key sets identical", () => {
    const zhKeys = Object.keys(I18N_MAP).sort();
    expect(Object.keys(EN_MAP).sort()).toEqual(zhKeys);
    expect(Object.keys(RU_MAP).sort()).toEqual(zhKeys);
  });

  it("contains every shared interaction key in all locales", () => {
    for (const key of REQUIRED_KEYS) {
      expect(I18N_MAP[key], `missing zh key: ${key}`).toBeTruthy();
      expect(EN_MAP[key], `missing en key: ${key}`).toBeTruthy();
      expect(RU_MAP[key], `missing ru key: ${key}`).toBeTruthy();
    }
  });

  it("defines every static production translation key in all locales", () => {
    const keys = [...new Set(productionSourceFiles(SOURCE_ROOT).flatMap(staticTranslationKeys))].sort();

    expect(keys.length).toBeGreaterThan(400);
    for (const key of keys) {
      expect(I18N_MAP[key], `missing zh key used by production code: ${key}`).toBeTruthy();
      expect(EN_MAP[key], `missing en key used by production code: ${key}`).toBeTruthy();
      expect(RU_MAP[key], `missing ru key used by production code: ${key}`).toBeTruthy();
    }
  });

  it("defines every known dynamic enum key in all locales", () => {
    for (const key of DYNAMIC_KEYS) {
      expect(I18N_MAP[key], `missing dynamic zh key: ${key}`).toBeTruthy();
      expect(EN_MAP[key], `missing dynamic en key: ${key}`).toBeTruthy();
      expect(RU_MAP[key], `missing dynamic ru key: ${key}`).toBeTruthy();
    }
  });

  it("keeps interpolation placeholders aligned across locales", () => {
    for (const key of Object.keys(I18N_MAP)) {
      const expected = placeholders(I18N_MAP[key]);
      expect(placeholders(EN_MAP[key]), `en placeholders differ for ${key}`).toEqual(expected);
      expect(placeholders(RU_MAP[key]), `ru placeholders differ for ${key}`).toEqual(expected);
    }
  });
});

describe("mock bridge locale persistence", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      writable: true,
      value: undefined,
    });
  });

  afterEach(() => {
    localStorage.clear();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      writable: true,
      value: undefined,
    });
  });

  it("restores Russian and reports it consistently from every context API", async () => {
    localStorage.setItem("memora_lang", "ru");
    const { ensureI18n, initMockBridge } = await import("./index");

    ensureI18n();
    expect(initMockBridge()).toBe(true);

    expect(window.AstrBotPluginPage.getLocale()).toBe("ru-RU");
    await expect(window.AstrBotPluginPage.ready()).resolves.toMatchObject({ locale: "ru-RU" });
    expect(window.AstrBotPluginPage.getContext()).toMatchObject({ locale: "ru-RU" });
    expect(document.documentElement.lang).toBe("ru-RU");
    expect(window.t("timeline.count", "$& $$")).toBe(
      RU_MAP["timeline.count"].replace("{0}", () => "$& $$"),
    );
  });
});
