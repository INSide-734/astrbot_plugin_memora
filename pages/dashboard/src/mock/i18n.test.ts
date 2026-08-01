import fs from "node:fs";
import path from "node:path";

import {
  isCallExpression,
  isIdentifier,
  isNoSubstitutionTemplateLiteral,
  isStringLiteral,
  type Node,
  type SourceFile,
} from "typescript/unstable/ast";
import { API } from "typescript/unstable/sync";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MOOD_TYPES, RELATION_CATEGORIES } from "../lib/constants";
import { EN_MAP, I18N_MAP, RU_MAP } from "./index";

const SOURCE_ROOT = path.resolve(process.cwd(), "src");

/**
 * 递归收集参与生产构建的 TypeScript 源文件。
 *
 * @param directory 当前扫描目录。
 * @returns 排除测试文件后的绝对路径列表。
 */
function productionSourceFiles(directory: string): string[] {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) return productionSourceFiles(entryPath);
    if (!/\.tsx?$/.test(entry.name) || entry.name.includes(".test.")) return [];
    return [entryPath];
  });
}

/**
 * 从已解析的 TypeScript AST 中提取静态翻译键。
 *
 * @param sourceFile TypeScript 项目快照中的源文件。
 * @returns `t()` 与 `label()` 首个静态字符串参数组成的键列表。
 */
function staticTranslationKeys(sourceFile: SourceFile): string[] {
  const keys: string[] = [];

  /** 遍历当前节点及其所有子节点并收集静态调用参数。 */
  function visit(node: Node): void {
    if (
      isCallExpression(node)
      && isIdentifier(node.expression)
      && (node.expression.text === "t" || node.expression.text === "label")
      && node.arguments.length > 0
    ) {
      const key = node.arguments[0];
      if (isStringLiteral(key) || isNoSubstitutionTemplateLiteral(key)) {
        keys.push(key.text);
      }
    }
    node.forEachChild(visit);
  }

  visit(sourceFile);
  return keys;
}

/**
 * 在单个 TypeScript 项目快照中收集全部生产源码的静态翻译键。
 *
 * @returns 去重并排序后的静态翻译键。
 * @throws 当项目快照为空或生产源文件未进入项目时抛出错误。
 */
function collectStaticTranslationKeys(): string[] {
  const api = new API({ cwd: process.cwd() });
  try {
    const snapshot = api.updateSnapshot({
      openProjects: [path.resolve(process.cwd(), "tsconfig.json")],
    });
    try {
      const project = snapshot.getProjects()[0];
      if (!project) throw new Error("TypeScript 项目快照为空");
      return [...new Set(productionSourceFiles(SOURCE_ROOT).flatMap((filePath) => {
        const sourceFile = project.program.getSourceFile(filePath);
        if (!sourceFile) throw new Error(`TypeScript 项目未包含生产源文件：${filePath}`);
        return staticTranslationKeys(sourceFile);
      }))].sort();
    } finally {
      snapshot.dispose();
    }
  } finally {
    api.close();
  }
}

const TABLE_KEYS = [
  "table.viewOptions",
  "table.columns",
  "table.density",
  "table.densityCompact",
  "table.densityStandard",
  "table.densityComfortable",
  "table.pinLeft",
  "table.pinRight",
  "table.unpin",
  "table.moveLeft",
  "table.moveRight",
  "table.resetView",
  "table.sortAscending",
  "table.sortDescending",
  "table.clearSort",
  "table.resizeColumn",
  "table.rowActions",
  "detail.unsaved",
  "detail.viewMode",
  "detail.editMode",
] as const;

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
  "search.inputPlaceholder",
  "search.groupPages",
  "search.groupConfig",
  "search.results",
  "search.configLoading",
  "search.countLimited",
  "kb.accessCount",
  ...TABLE_KEYS,
] as const;

const INJECTION_REQUIRED_KEYS = [
  "nav.injection",
  "injection.title", "injection.subtitle", "injection.tabs.label",
  "injection.tabs.overview", "injection.tabs.config", "injection.tabs.decisions",
  "injection.actions.edit", "injection.actions.openTrace",
  "injection.actions.traceUnavailable", "injection.actions.restoreDefaults",
  "injection.actions.discard", "injection.actions.save",
  "injection.actions.saving", "injection.actions.clearFilters",
  "injection.decisions.openDetail", "injection.detail.title",
  "injection.detail.description", "injection.state.loading",
  "injection.state.empty", "injection.state.error",
  "injection.window.1h", "injection.window.24h", "injection.window.7d",
  "injection.window.30d", "injection.overview.window",
  "injection.overview.currentMode", "injection.overview.currentPreset",
  "injection.overview.effectiveDelivery", "injection.overview.decisions",
  "injection.overview.payloadP95", "injection.overview.fallbackRate",
  "injection.overview.presetDistribution", "injection.overview.presetChartSummary",
  "injection.overview.costTrend", "injection.overview.costChartSummary",
  "injection.overview.recent", "injection.overview.recentFallbacks",
  "injection.overview.recentErrors", "injection.overview.noEvents",
  "injection.config.routing", "injection.config.presetComparison",
  "injection.config.delivery", "injection.config.advanced",
  "injection.config.retention", "injection.preset.name",
  "injection.preset.autoInject", "injection.preset.budget",
  "injection.preset.maxMemories", "injection.preset.contentLevel",
  "injection.preset.toolFallback", "injection.field.routingMode",
  "injection.field.manualPreset", "injection.field.autoFallbackPreset",
  "injection.field.hybridBasePreset", "injection.field.hybridMinPreset",
  "injection.field.hybridMaxPreset", "injection.field.deliveryOverride",
  "injection.field.overridesEnabled", "injection.field.budgetChars",
  "injection.field.memoryMaxChars", "injection.field.metadataMaxChars",
  "injection.field.includeKeyFacts", "injection.field.includeTopics",
  "injection.field.includeParticipants", "injection.field.compactHeader",
  "injection.field.retentionDays", "injection.field.maxRows",
  "injection.help.preset", "injection.help.overridesEnabled",
  "injection.help.zeroUsesPreset", "injection.help.retention",
  "injection.validation.hybridOrder", "injection.validation.retention",
  "injection.validation.maxRows", "injection.validation.budget",
  "injection.validation.timeRange", "injection.filter.from",
  "injection.filter.to", "injection.filter.routingMode",
  "injection.filter.resolvedPreset", "injection.filter.providerType",
  "injection.filter.primaryReason", "injection.filter.fallbackApplied",
  "injection.filter.outcome", "injection.filter.all", "injection.column.time",
  "injection.column.mode", "injection.column.preset", "injection.column.provider",
  "injection.column.reason", "injection.column.fallback",
  "injection.column.outcome", "injection.column.payloadChars",
  "injection.column.totalMs", "injection.column.actions",
  "injection.pagination.label", "injection.pagination.previous",
  "injection.pagination.next", "injection.pagination.pageSize",
  "injection.pagination.summary", "injection.detail.identity",
  "injection.detail.routing", "injection.detail.delivery",
  "injection.detail.counts", "injection.detail.budgets",
  "injection.detail.timings", "injection.detail.reasons",
  "injection.detail.retry",
] as const;

const INJECTION_EXACT_COPY = [
  ["nav.injection", "注入策略", "Injection Strategy", "Стратегия внедрения"],
  ["injection.title", "注入策略", "Injection Strategy", "Стратегия внедрения"],
  ["injection.subtitle", "配置记忆注入路由，并审查脱敏决策记录。", "Configure memory injection routing and review sanitized decisions.", "Настройте маршрутизацию памяти и просматривайте обезличенные решения."],
  ["injection.tabs.label", "注入策略工作台", "Injection strategy workbench", "Рабочая область стратегии внедрения"],
  ["injection.tabs.overview", "概览", "Overview", "Обзор"],
  ["injection.tabs.config", "策略配置", "Strategy Configuration", "Настройка стратегии"],
  ["injection.tabs.decisions", "决策记录", "Decision History", "История решений"],
  ["injection.actions.edit", "编辑策略", "Edit strategy", "Изменить стратегию"],
  ["injection.actions.openTrace", "打开召回追踪", "Open recall trace", "Открыть трассировку поиска"],
  ["injection.actions.traceUnavailable", "没有关联的召回追踪", "No linked recall trace", "Нет связанной трассировки"],
  ["injection.actions.restoreDefaults", "恢复预设默认值", "Restore preset defaults", "Восстановить значения пресета"],
  ["injection.actions.discard", "放弃草稿", "Discard draft", "Отменить черновик"],
  ["injection.actions.save", "保存策略", "Save strategy", "Сохранить стратегию"],
  ["injection.actions.saving", "正在保存…", "Saving…", "Сохранение…"],
  ["injection.actions.clearFilters", "清除筛选", "Clear filters", "Очистить фильтры"],
  ["injection.decisions.openDetail", "查看决策详情", "View decision details", "Открыть детали решения"],
  ["injection.detail.title", "注入决策详情", "Injection decision details", "Детали решения о внедрении"],
  ["injection.detail.description", "仅显示脱敏后的路由、预算、Provider 与耗时字段。", "Sanitized routing, budget, provider, and timing fields only.", "Только обезличенные поля маршрутизации, бюджета, провайдера и времени."],
  ["injection.state.loading", "正在加载注入策略", "Loading injection strategy", "Загрузка стратегии внедрения"],
  ["injection.state.empty", "当前窗口没有决策记录", "No decisions in this window", "В этом интервале нет решений"],
  ["injection.state.error", "无法加载注入策略数据", "Could not load injection strategy data", "Не удалось загрузить данные стратегии"],
  ["injection.mode.manual", "纯手动", "Manual", "Ручной"],
  ["injection.mode.auto", "自动切换", "Auto", "Авто"],
  ["injection.mode.hybrid", "混合切换", "Hybrid", "Гибрид"],
  ["injection.preset.tool_first", "工具优先", "Tool first", "Сначала инструмент"],
  ["injection.preset.low_cost", "低成本", "Low cost", "Низкая стоимость"],
  ["injection.preset.balanced", "均衡", "Balanced", "Сбалансированный"],
  ["injection.preset.quality", "质量优先", "Quality", "Приоритет качества"],
  ["injection.content.NONE", "不注入", "None", "Без внедрения"],
  ["injection.content.FACTS", "关键事实", "Facts", "Факты"],
  ["injection.content.COMPACT", "紧凑", "Compact", "Компактный"],
  ["injection.content.DETAILED", "详细", "Detailed", "Подробный"],
  ["injection.outcome.injected", "已注入", "Injected", "Внедрено"],
  ["injection.outcome.skipped", "已跳过", "Skipped", "Пропущено"],
  ["injection.outcome.empty", "无有效内容", "Empty", "Пусто"],
  ["injection.outcome.fallback", "已降级", "Fallback", "Резервный режим"],
  ["injection.outcome.error", "错误", "Error", "Ошибка"],
  ["injection.delivery.auto", "自动适配", "Auto", "Автовыбор"],
  ["injection.delivery.extra_user_content", "临时用户附加内容", "Temporary user content", "Временный пользовательский контент"],
  ["injection.delivery.user_message_before", "用户消息前", "Before user message", "Перед сообщением пользователя"],
  ["injection.delivery.user_message_after", "用户消息后", "After user message", "После сообщения пользователя"],
  ["injection.delivery.fake_tool_call", "模拟工具调用", "Synthetic tool call", "Имитация вызова инструмента"],
  ["injection.delivery.fake_tool_call_deepseek_v4", "DeepSeek V4 模拟工具调用", "DeepSeek V4 synthetic tool call", "Имитация инструмента DeepSeek V4"],
  ["injection.reason.MANUAL_SELECTED", "管理员手动选择", "Selected manually", "Выбрано вручную"],
  ["injection.reason.AUTO_HISTORY_INTENT", "历史意图自动升级", "Auto history intent", "Автовыбор по историческому намерению"],
  ["injection.reason.AUTO_LOW_CONTEXT_HEADROOM", "上下文余量不足", "Low context headroom", "Недостаточный запас контекста"],
  ["injection.reason.AUTO_MEMORY_UNCERTAIN", "记忆需求不确定", "Memory need uncertain", "Неопределённая потребность в памяти"],
  ["injection.reason.AUTO_FALLBACK", "自动规则回退", "Auto fallback", "Автоматический резерв"],
  ["injection.reason.HYBRID_CLAMPED_MIN", "已限制到混合最小预设", "Clamped to Hybrid minimum", "Ограничено минимумом гибрида"],
  ["injection.reason.HYBRID_CLAMPED_MAX", "已限制到混合最大预设", "Clamped to Hybrid maximum", "Ограничено максимумом гибрида"],
  ["injection.reason.PROVIDER_TOOL_UNAVAILABLE", "Provider 记忆工具不可用", "Provider memory tool unavailable", "Инструмент памяти провайдера недоступен"],
  ["injection.reason.PROVIDER_DELIVERY_DOWNGRADED", "Provider 传输方式已降级", "Provider delivery downgraded", "Способ доставки провайдера понижен"],
  ["injection.reason.INVALID_CONFIG_FALLBACK", "非法配置安全回退", "Invalid configuration fallback", "Резерв из-за неверной конфигурации"],
  ["injection.reason.NO_USEFUL_CANDIDATES", "没有有效候选", "No useful candidates", "Нет полезных кандидатов"],
  ["injection.reason.BUDGET_EXHAUSTED", "注入预算耗尽", "Injection budget exhausted", "Бюджет внедрения исчерпан"],
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
  ...[
    "baseline",
    "a",
    "b",
    "c",
    "graph_expansion_off",
    "topic_expansion_off",
    "final_reranker_off",
    "final_reranker_mmr",
    "final_reranker_embedding_similarity",
    "graph_neighbors_off",
    "graph_neighbors_1_hop",
    "graph_neighbors_2_hops",
  ]
    .map((variant) => `intelligence.evaluation.variant.${variant}`),
  ...[
    "equivalent_to_baseline",
    "missing_engine",
    "missing_engine_config",
    "missing_dual_route",
    "missing_derived_reader",
    "missing_graph_retriever",
    "missing_document_vector_access",
    "readonly_snapshot_cannot_activate_worker",
    "variant_prepare_failed",
    "variant_execution_failed",
    "variant_not_exercised",
    "embedding_query_failed",
    "unknown_variant",
  ].map((reason) => `intelligence.evaluation.reason.${reason}`),
  ...["private", "group"].map((chatType) => `intelligence.trace.chatType.${chatType}`),
  ...["approve", "mark_safe", "edit", "merge", "archive", "delete", "approved", "edited", "merged", "archived", "deleted", "safe"]
    .map((action) => `intelligence.review.action.${action}`),
  ...["pending", "approved", "rejected", "failed", "rolled_back"]
    .map((status) => `intelligence.reconsolidation.status.${status}`),
  ...["approve", "reject", "rollback", "stage", "apply"]
    .map((action) => `intelligence.reconsolidation.action.${action}`),
  ...["llm_revision"]
    .map((evidence) => `intelligence.reconsolidation.evidence.${evidence}`),
  ...["proposed", "applied", "manual_reject", "rolled_back", "source_revision_mismatch", "candidate_changed"]
    .map((reason) => `intelligence.reconsolidation.reason.${reason}`),
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
  ...["manual", "auto", "hybrid"].map((mode) => `injection.mode.${mode}`),
  ...["tool_first", "low_cost", "balanced", "quality"]
    .map((preset) => `injection.preset.${preset}`),
  ...["NONE", "FACTS", "COMPACT", "DETAILED"]
    .map((level) => `injection.content.${level}`),
  ...["injected", "skipped", "empty", "fallback", "error"]
    .map((outcome) => `injection.outcome.${outcome}`),
  ...[
    "auto", "extra_user_content", "user_message_before", "user_message_after",
    "fake_tool_call", "fake_tool_call_deepseek_v4",
  ].map((delivery) => `injection.delivery.${delivery}`),
  ...[
    "MANUAL_SELECTED", "AUTO_HISTORY_INTENT", "AUTO_LOW_CONTEXT_HEADROOM",
    "AUTO_MEMORY_UNCERTAIN", "AUTO_FALLBACK", "HYBRID_CLAMPED_MIN",
    "HYBRID_CLAMPED_MAX", "PROVIDER_TOOL_UNAVAILABLE",
    "PROVIDER_DELIVERY_DOWNGRADED", "INVALID_CONFIG_FALLBACK",
    "NO_USEFUL_CANDIDATES", "BUDGET_EXHAUSTED",
  ].map((reason) => `injection.reason.${reason}`),
  ...[
    "MANUAL_SELECTED", "AUTO_HISTORY_INTENT", "AUTO_LOW_CONTEXT_HEADROOM",
    "AUTO_MEMORY_UNCERTAIN", "AUTO_FALLBACK", "HYBRID_CLAMPED_MIN",
    "HYBRID_CLAMPED_MAX", "PROVIDER_TOOL_UNAVAILABLE",
    "PROVIDER_DELIVERY_DOWNGRADED", "INVALID_CONFIG_FALLBACK",
    "NO_USEFUL_CANDIDATES", "BUDGET_EXHAUSTED",
  ].map((reason) => `injection.reason.${reason.toLowerCase()}`),
  ...[
    "decision_id", "created_at_ms", "trace_id", "routing_mode",
    "configured_preset", "recommended_preset", "resolved_preset",
    "preferred_delivery", "resolved_delivery", "fallback_applied", "outcome",
    "error_code", "primary_reason", "reason_codes", "provider_type",
    "provider_model", "candidate_count", "selected_count", "dropped_count",
    "truncated_count", "configured_budget_chars", "effective_budget_chars",
    "actual_payload_chars", "context_headroom_chars", "decision_ms", "format_ms",
    "inject_ms",
  ].map((field) => `injection.detail.field.${field}`),
] as const;

const STATIC_TRANSLATION_KEYS = collectStaticTranslationKeys();

const REQUIRED_EDITING_KEYS = [...new Set([
  ...STATIC_TRANSLATION_KEYS,
  ...DYNAMIC_KEYS,
  "social.newRelation",
  "affection.restoreDefaultMood",
])].sort();

/**
 * 查找翻译字典中缺失或只包含空白的键。
 *
 * @param map 待检查的翻译字典。
 * @param keys 必须存在的翻译键。
 * @returns 缺失或空白的键列表。
 */
function missingOrBlankKeys(map: Record<string, string>, keys: readonly string[]): string[] {
  return keys.filter((key) => typeof map[key] !== "string" || map[key].trim().length === 0);
}

/**
 * 提取翻译文本中的数字占位符编号。
 *
 * @param value 待解析的翻译文本。
 * @returns 排序后的占位符编号列表。
 */
function placeholders(value: string): string[] {
  return [...value.matchAll(/\{(\d+)\}/g)].map((match) => match[1]).sort();
}

describe("dashboard i18n dictionaries", () => {
  it("keeps Chinese, English, and Russian key sets identical", () => {
    const zhKeys = Object.keys(I18N_MAP).sort();
    expect(Object.keys(EN_MAP).sort()).toEqual(zhKeys);
    expect(Object.keys(RU_MAP).sort()).toEqual(zhKeys);
  });

  it("defines every required editing key with non-blank copy in all locales", () => {
    const missing = {
      zh: missingOrBlankKeys(I18N_MAP, REQUIRED_EDITING_KEYS),
      en: missingOrBlankKeys(EN_MAP, REQUIRED_EDITING_KEYS),
      ru: missingOrBlankKeys(RU_MAP, REQUIRED_EDITING_KEYS),
    };

    expect(
      missing,
      `missing or blank required editing translations:\n${JSON.stringify(missing, null, 2)}`,
    ).toEqual({ zh: [], en: [], ru: [] });
  });

  it("contains every shared interaction key in all locales", () => {
    for (const key of REQUIRED_KEYS) {
      expect(I18N_MAP[key], `missing zh key: ${key}`).toBeTruthy();
      expect(EN_MAP[key], `missing en key: ${key}`).toBeTruthy();
      expect(RU_MAP[key], `missing ru key: ${key}`).toBeTruthy();
    }
  });

  it("contains the complete injection workbench contract in all locales", () => {
    for (const key of INJECTION_REQUIRED_KEYS) {
      expect(I18N_MAP[key], `missing zh injection key: ${key}`).toBeTruthy();
      expect(EN_MAP[key], `missing en injection key: ${key}`).toBeTruthy();
      expect(RU_MAP[key], `missing ru injection key: ${key}`).toBeTruthy();
      expect(I18N_MAP[key]).not.toBe(key);
      expect(EN_MAP[key]).not.toBe(key);
      expect(RU_MAP[key]).not.toBe(key);
    }
  });

  it("uses the approved injection enum page action and state copy", () => {
    for (const [key, zh, en, ru] of INJECTION_EXACT_COPY) {
      expect(I18N_MAP[key]).toBe(zh);
      expect(EN_MAP[key]).toBe(en);
      expect(RU_MAP[key]).toBe(ru);
    }
  });

  it("uses the documented global search copy in every locale", () => {
    expect({
      inputPlaceholder: I18N_MAP["search.inputPlaceholder"],
      groupPages: I18N_MAP["search.groupPages"],
      groupConfig: I18N_MAP["search.groupConfig"],
      results: I18N_MAP["search.results"],
      configLoading: I18N_MAP["search.configLoading"],
      countLimited: I18N_MAP["search.countLimited"],
    }).toEqual({
      inputPlaceholder: "搜索页面、配置、记忆、知识、笔记...",
      groupPages: "页面",
      groupConfig: "配置",
      results: "搜索结果",
      configLoading: "正在加载配置索引",
      countLimited: "显示 {0}/{1}",
    });
    expect({
      inputPlaceholder: EN_MAP["search.inputPlaceholder"],
      groupPages: EN_MAP["search.groupPages"],
      groupConfig: EN_MAP["search.groupConfig"],
      results: EN_MAP["search.results"],
      configLoading: EN_MAP["search.configLoading"],
      countLimited: EN_MAP["search.countLimited"],
    }).toEqual({
      inputPlaceholder: "Search pages, configuration, memories, knowledge, notes...",
      groupPages: "Pages",
      groupConfig: "Configuration",
      results: "Search results",
      configLoading: "Loading configuration index",
      countLimited: "Showing {0}/{1}",
    });
    expect({
      inputPlaceholder: RU_MAP["search.inputPlaceholder"],
      groupPages: RU_MAP["search.groupPages"],
      groupConfig: RU_MAP["search.groupConfig"],
      results: RU_MAP["search.results"],
      configLoading: RU_MAP["search.configLoading"],
      countLimited: RU_MAP["search.countLimited"],
    }).toEqual({
      inputPlaceholder: "Поиск страниц, настроек, памяти, знаний и заметок...",
      groupPages: "Страницы",
      groupConfig: "Конфигурация",
      results: "Результаты поиска",
      configLoading: "Загрузка индекса конфигурации",
      countLimited: "Показано {0}/{1}",
    });
  });

  it("defines every static production translation key in all locales", () => {
    const keys = STATIC_TRANSLATION_KEYS;

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

  it("never falls back to raw injection keys in any locale", () => {
    const injectionKeys = [
      ...INJECTION_REQUIRED_KEYS,
      ...DYNAMIC_KEYS.filter((key) => key.startsWith("injection.")),
    ];
    for (const key of new Set(injectionKeys)) {
      expect(I18N_MAP[key]).not.toBe(key);
      expect(EN_MAP[key]).not.toBe(key);
      expect(RU_MAP[key]).not.toBe(key);
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
