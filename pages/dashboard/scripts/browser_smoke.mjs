import { chromium } from "playwright";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import {
  assertEditorViewport,
  assertNoHorizontalOverflow as assertNoHorizontalOverflowMeasurements,
  BROWSER_LAUNCH_CANDIDATES,
  BRIDGE_CALL_SENSITIVE_FIELDS,
  createBrowserLaunchOptions,
  installBundledMockBridgeHarness,
  instrumentBrowserBridge,
  ROUTE_LOADING_TEXT,
} from "./browser_smoke_helpers.mjs";
import {
  assertConfigRuntimeCalls,
  assertDialogActions,
  assertEditorReadiness,
} from "./runtime_smoke_helpers.mjs";
import { createConfigSmokeFixture } from "./config_smoke_fixture.mjs";
import { recallTracePayload } from "./recall_trace_smoke_fixture.mjs";

const dashboardRoot = process.cwd();
const htmlPath = path.join(dashboardRoot, "index.html");
const html = await readFile(htmlPath, "utf8");
const configSmokeFixture = createConfigSmokeFixture();

if (html.includes("/src/main") || html.includes('type="module"')) {
  throw new Error("Dashboard index.html is not a production AstrBot-compatible build");
}

const SCREENSHOT_BASELINES = {
  "config.png": { width: 1366, height: 900, minBytes: 10_000 },
  "config-conflict.png": { width: 1366, height: 900, minBytes: 10_000 },
  "mobile-config.png": { width: 390, height: 844, minBytes: 10_000 },
  "injection-overview.png": { width: 1366, height: 900, minBytes: 10_000 },
  "injection-config-conflict.png": { width: 1366, height: 900, minBytes: 10_000 },
  "injection-decisions.png": { width: 1366, height: 900, minBytes: 10_000 },
  "mobile-injection-detail.png": { width: 390, height: 844, minBytes: 10_000 },
  "wide-injection-overview.png": { width: 2048, height: 1152, minBytes: 10_000 },
  "global-search-scroll.png": { width: 1366, height: 900, minBytes: 10_000 },
  "global-search-memory-target.png": { width: 1366, height: 900, minBytes: 10_000 },
  "graph.png": { width: 1366, height: 900, minBytes: 10_000 },
  "memory.png": { width: 1366, height: 900, minBytes: 10_000 },
  "system.png": { width: 1366, height: 900, minBytes: 10_000 },
  "jargon.png": { width: 1366, height: 900, minBytes: 10_000 },
  "intelligence-evaluation.png": { width: 1366, height: 900, minBytes: 10_000 },
  "intelligence-trace.png": { width: 1366, height: 900, minBytes: 10_000 },
  "intelligence-diagnostics.png": { width: 1366, height: 900, minBytes: 10_000 },
  "intelligence-review.png": { width: 1366, height: 900, minBytes: 10_000 },
  "mobile-system.png": { width: 390, height: 844, minBytes: 10_000 },
  "mobile-jargon.png": { width: 390, height: 844, minBytes: 10_000 },
  "system-confirmation.png": { width: 1366, height: 900, minBytes: 10_000 },
  "dark-learning.png": { width: 1366, height: 900, minBytes: 10_000 },
  "dark-system.png": { width: 1366, height: 900, minBytes: 10_000 },
  "preview.png": { width: 1366, height: 900, minBytes: 10_000 },
  "mobile-preview.png": { width: 390, height: 844, minBytes: 10_000 },
  "dark-preview.png": { width: 1366, height: 900, minBytes: 10_000 },
  "wide-preview.png": { width: 2048, height: 1152, minBytes: 10_000 },
  "wide-learning.png": { width: 2048, height: 1152, minBytes: 10_000 },
  "wide-affection.png": { width: 2048, height: 1152, minBytes: 10_000 },
  "wide-social.png": { width: 2048, height: 1152, minBytes: 10_000 },
  "i18n-en-preview.png": { width: 1366, height: 900, minBytes: 10_000 },
  "i18n-en-memory.png": { width: 1366, height: 900, minBytes: 10_000 },
  "i18n-ru-preview.png": { width: 1366, height: 900, minBytes: 10_000 },
  "i18n-ru-memory.png": { width: 1366, height: 900, minBytes: 10_000 },
  "editing-social-sheet.png": { width: 1366, height: 900, minBytes: 10_000 },
  "editing-social-conflict.png": { width: 1366, height: 900, minBytes: 10_000 },
  "editing-error-summary.png": { width: 1366, height: 900, minBytes: 10_000 },
  "editing-batch-toolbar.png": { width: 1366, height: 900, minBytes: 10_000 },
  "editing-mobile-affection.png": { width: 390, height: 844, minBytes: 10_000 },
  "editing-mobile-mood.png": { width: 390, height: 844, minBytes: 10_000 },
  "knowledge-table-default.png": { width: 1366, height: 900, minBytes: 10_000 },
  "knowledge-table-columns.png": { width: 1366, height: 900, minBytes: 10_000 },
  "knowledge-editor-view.png": { width: 1366, height: 900, minBytes: 10_000 },
  "knowledge-editor-edit.png": { width: 1366, height: 900, minBytes: 10_000 },
  "mobile-knowledge-table.png": { width: 390, height: 844, minBytes: 10_000 },
  "mobile-knowledge-editor.png": { width: 390, height: 844, minBytes: 10_000 },
  "wide-profiles-table.png": { width: 2048, height: 1152, minBytes: 10_000 },
  "dark-social-table.png": { width: 1366, height: 900, minBytes: 10_000 },
  "injection-decisions-compact.png": { width: 1366, height: 900, minBytes: 10_000 },
};

const INJECTION_SMOKE_NOW_MS = Date.UTC(2026, 6, 15, 8, 0, 0);
const INJECTION_SMOKE_PRESETS = ["tool_first", "low_cost", "balanced", "quality"];
const INJECTION_SMOKE_MODES = ["manual", "auto", "hybrid"];
const INJECTION_SMOKE_OUTCOMES = ["injected", "skipped", "empty", "fallback", "error"];
const INJECTION_SMOKE_DECISIONS = Array.from({ length: 72 }, (_, index) => {
  const resolvedPreset = INJECTION_SMOKE_PRESETS[index % INJECTION_SMOKE_PRESETS.length];
  const outcome = INJECTION_SMOKE_OUTCOMES[index % INJECTION_SMOKE_OUTCOMES.length];
  const fallbackApplied = outcome === "fallback" || index % 11 === 0;
  const budget = resolvedPreset === "quality"
    ? 2_400
    : resolvedPreset === "balanced"
      ? 1_200
      : resolvedPreset === "low_cost"
        ? 800
        : 0;
  return {
    decision_id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
    created_at_ms: INJECTION_SMOKE_NOW_MS - index * 30 * 60 * 1_000,
    trace_id: index % 3 === 0 ? `trace-smoke-${String(index + 1).padStart(3, "0")}` : null,
    routing_mode: INJECTION_SMOKE_MODES[index % INJECTION_SMOKE_MODES.length],
    configured_preset: INJECTION_SMOKE_PRESETS[(index + 1) % INJECTION_SMOKE_PRESETS.length],
    recommended_preset: INJECTION_SMOKE_PRESETS[(index + 2) % INJECTION_SMOKE_PRESETS.length],
    resolved_preset: resolvedPreset,
    preferred_delivery: "extra_user_content",
    resolved_delivery: fallbackApplied ? "user_message_before" : "extra_user_content",
    fallback_applied: fallbackApplied,
    outcome,
    error_code: outcome === "error" ? "FORMAT_FAILED" : null,
    primary_reason: fallbackApplied ? "PROVIDER_DELIVERY_DOWNGRADED" : "MANUAL_SELECTED",
    reason_codes: fallbackApplied
      ? ["MANUAL_SELECTED", "PROVIDER_DELIVERY_DOWNGRADED"]
      : ["MANUAL_SELECTED"],
    provider_type: index % 2 === 0 ? "openai" : "gemini",
    provider_model: index % 2 === 0 ? "gpt-smoke" : "gemini-smoke",
    candidate_count: 6,
    selected_count: resolvedPreset === "tool_first" ? 0 : Math.min(4, index % 5),
    dropped_count: index % 3,
    truncated_count: index % 2,
    configured_budget_chars: budget,
    effective_budget_chars: budget,
    actual_payload_chars: budget === 0 ? 0 : Math.min(budget, 320 + index * 13),
    context_headroom_chars: 8_000 - index * 10,
    decision_ms: 0.4 + (index % 5) * 0.1,
    format_ms: 1.2 + (index % 7) * 0.2,
    inject_ms: 0.3 + (index % 3) * 0.1,
  };
});

// Keep the browser fixture dense enough to exercise column visibility, pinning,
// server-side sorting and the shared entity editor without relying on the
// backend runtime. The IDs and timestamps are intentionally deterministic so
// screenshots remain stable across runs.
let KNOWLEDGE_SMOKE_ENTRIES = Array.from({ length: 18 }, (_, index) => {
  const id = `knowledge-smoke-${String(index + 1).padStart(2, "0")}`;
  const category = ["fact", "concept", "rule", "event", "procedure"][index % 5];
  return {
    entry_id: id,
    title: `浏览器 smoke 知识 ${String(index + 1).padStart(2, "0")}`,
    content: `用于浏览器验收的知识条目 ${index + 1}，包含稳定的详情内容。`,
    category,
    confidence: Number((0.55 + (index % 5) * 0.09).toFixed(2)),
    access_count: 3 + index * 2,
    tags: index % 2 === 0 ? ["browser", category] : ["smoke"],
    updated_at: `2026-07-${String(18 - Math.floor(index / 3)).padStart(2, "0")}T${String(8 + (index % 10)).padStart(2, "0")}:00:00Z`,
    created_at: `2026-07-${String(16 - Math.floor(index / 3)).padStart(2, "0")}T08:00:00Z`,
  };
});

function sortKnowledgeSmokeEntries(entries, params = {}) {
  const sortBy = String(params.sort_by ?? "updated_at");
  const descending = String(params.sort_order ?? "desc") === "desc";
  const allowed = new Set(["title", "category", "confidence", "updated_at", "access_count"]);
  const key = allowed.has(sortBy) ? sortBy : "updated_at";
  return [...entries].sort((left, right) => {
    let comparison = 0;
    if (key === "confidence" || key === "access_count") {
      comparison = Number(left[key] ?? 0) - Number(right[key] ?? 0);
    } else {
      comparison = String(left[key] ?? "").localeCompare(String(right[key] ?? ""));
    }
    if (comparison === 0) comparison = String(left.entry_id).localeCompare(String(right.entry_id));
    return descending ? -comparison : comparison;
  });
}

function knowledgeSmokeListPayload(params = {}) {
  const query = String(params.query ?? "").trim().toLocaleLowerCase();
  const category = String(params.category ?? "").trim();
  const filtered = KNOWLEDGE_SMOKE_ENTRIES.filter((entry) => {
    if (category && entry.category !== category) return false;
    if (!query) return true;
    return `${entry.title} ${entry.content}`.toLocaleLowerCase().includes(query);
  });
  const sorted = sortKnowledgeSmokeEntries(filtered, params);
  const offset = Math.max(0, Number(params.offset ?? 0));
  const limit = Math.max(1, Number(params.limit ?? 100));
  return {
    entries: sorted.slice(offset, offset + limit).map((entry) => ({ ...entry, tags: [...entry.tags] })),
    total: sorted.length,
    offset,
    limit,
  };
}

function injectionCatalogPayload() {
  const preset = (
    name,
    rank,
    memoryBudgetChars,
    maxMemories,
    contentLevel,
  ) => ({
    name,
    rank,
    auto_inject: rank > 0,
    memory_budget_chars: memoryBudgetChars,
    max_memories: maxMemories,
    content_level: contentLevel,
    cost_penalty_weight: rank === 0 ? 1 : 0.3 / rank,
    minimum_utility: rank === 0 ? 1 : 0.6 / rank,
    allow_tool_fallback: true,
    preferred_delivery: "extra_user_content",
  });
  return {
    routing_modes: INJECTION_SMOKE_MODES,
    presets: [
      preset("tool_first", 0, 0, 0, "NONE"),
      preset("low_cost", 1, 800, 2, "FACTS"),
      preset("balanced", 2, 1_200, 4, "COMPACT"),
      preset("quality", 3, 2_400, 6, "DETAILED"),
    ],
    deliveries: [
      "auto",
      "extra_user_content",
      "user_message_before",
      "user_message_after",
      "fake_tool_call",
      "fake_tool_call_deepseek_v4",
    ],
    retention_options: [7, 30, 90, 180, 0],
    provider_tools_supported: true,
    memory_tool_available: true,
    recall_trace_available: true,
    effective_default_delivery: "extra_user_content",
  };
}

function injectionListItem(row) {
  return {
    decision_id: row.decision_id,
    created_at_ms: row.created_at_ms,
    trace_id: row.trace_id,
    routing_mode: row.routing_mode,
    configured_preset: row.configured_preset,
    recommended_preset: row.recommended_preset,
    resolved_preset: row.resolved_preset,
    preferred_delivery: row.preferred_delivery,
    resolved_delivery: row.resolved_delivery,
    fallback_applied: row.fallback_applied,
    outcome: row.outcome,
    error_code: row.error_code,
    primary_reason: row.primary_reason,
    provider_type: row.provider_type,
    provider_model: row.provider_model,
    candidate_count: row.candidate_count,
    selected_count: row.selected_count,
    dropped_count: row.dropped_count,
    truncated_count: row.truncated_count,
    configured_budget_chars: row.configured_budget_chars,
    effective_budget_chars: row.effective_budget_chars,
    actual_payload_chars: row.actual_payload_chars,
    context_headroom_chars: row.context_headroom_chars,
    decision_ms: row.decision_ms,
    format_ms: row.format_ms,
    inject_ms: row.inject_ms,
  };
}

function injectionSummaryPayload(params) {
  const windowValue = params.window ?? "24h";
  const windowMs = { "1h": 3_600_000, "24h": 86_400_000, "7d": 604_800_000, "30d": 2_592_000_000 };
  const rows = INJECTION_SMOKE_DECISIONS.filter(
    (row) => row.created_at_ms >= INJECTION_SMOKE_NOW_MS - windowMs[windowValue],
  );
  const payloads = rows.map((row) => row.actual_payload_chars).sort((left, right) => left - right);
  const buckets = new Map();
  for (const row of rows) {
    const bucket = Math.floor(row.created_at_ms / 3_600_000) * 3_600_000;
    buckets.set(bucket, [...(buckets.get(bucket) ?? []), row]);
  }
  const recentEvent = (row) => ({
    decision_id: row.decision_id,
    created_at_ms: row.created_at_ms,
    trace_id: row.trace_id,
    routing_mode: row.routing_mode,
    resolved_preset: row.resolved_preset,
    outcome: row.outcome,
    primary_reason: row.primary_reason,
    fallback_applied: row.fallback_applied,
    actual_payload_chars: row.actual_payload_chars,
  });
  return {
    window: windowValue,
    decision_count: rows.length,
    payload_chars_p95: payloads[Math.max(0, Math.ceil(payloads.length * 0.95) - 1)] ?? 0,
    provider_fallback_rate: rows.length
      ? rows.filter((row) => row.fallback_applied).length / rows.length
      : 0,
    preset_distribution: Object.fromEntries(INJECTION_SMOKE_PRESETS.map(
      (name) => [name, rows.filter((row) => row.resolved_preset === name).length],
    )),
    cost_trend: [...buckets.entries()].sort(([left], [right]) => left - right).map(
      ([bucket_ms, items]) => ({
        bucket_ms,
        decision_count: items.length,
        payload_chars_p95: Math.max(...items.map((item) => item.actual_payload_chars)),
        provider_fallback_rate: items.filter((item) => item.fallback_applied).length / items.length,
      }),
    ),
    recent_events: rows.slice(0, 15).map(recentEvent),
  };
}

function injectionDecisionPagePayload(params) {
  const offset = Number(params.offset ?? 0);
  const limit = Number(params.limit ?? 50);
  const fallback = params.fallback_applied === undefined
    ? undefined
    : params.fallback_applied === "true";
  const rows = INJECTION_SMOKE_DECISIONS
    .filter((row) => params.from_ms === undefined || row.created_at_ms >= Number(params.from_ms))
    .filter((row) => params.to_ms === undefined || row.created_at_ms <= Number(params.to_ms))
    .filter((row) => !params.routing_mode || row.routing_mode === params.routing_mode)
    .filter((row) => !params.resolved_preset || row.resolved_preset === params.resolved_preset)
    .filter((row) => !params.provider_type || row.provider_type === params.provider_type)
    .filter((row) => !params.primary_reason || row.primary_reason === params.primary_reason)
    .filter((row) => fallback === undefined || row.fallback_applied === fallback)
    .filter((row) => !params.outcome || row.outcome === params.outcome)
    .sort((left, right) => right.created_at_ms - left.created_at_ms
      || right.decision_id.localeCompare(left.decision_id));
  return {
    items: rows.slice(offset, offset + limit).map(injectionListItem),
    total: rows.length,
    offset,
    limit,
  };
}

function injectionDecisionDetailPayload(decisionId) {
  const row = INJECTION_SMOKE_DECISIONS.find((item) => item.decision_id === decisionId);
  return row
    ? { ...injectionListItem(row), reason_codes: [...row.reason_codes] }
    : {};
}

function cloneJson(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function createEditingBridgeState() {
  return {
    revision: 1,
    armSocialConflict: false,
    social: [],
    affectionUsers: [
      {
        group_id: "group-smoke",
        user_id: "task18-affection-user",
        affection_score: 24,
        affection_level: "FRIENDLY",
        level_name: "友好",
        interaction_count: 3,
        last_interaction: 1_782_000_000,
        revision: "browser-edit-revision-0001",
      },
    ],
    currentMood: {
      mood_type: "happy",
      intensity: 0.7,
      duration_hours: 4,
      description: "浏览器 smoke 情绪",
      start_time: 1_782_000_000,
      is_active: true,
    },
    moodHistory: [],
  };
}

let editingBridgeState = createEditingBridgeState();
let editingBridgeEnabled = false;

function resetEditingBridgeState() {
  editingBridgeState = createEditingBridgeState();
}

function nextEditingRevision() {
  editingBridgeState.revision += 1;
  return `browser-edit-revision-${String(editingBridgeState.revision).padStart(4, "0")}`;
}

function editingResponse(response) {
  return { __memoraEditingResponse: response };
}

function socialIdentity(value) {
  return {
    from_user: String(value?.from_user ?? ""),
    to_user: String(value?.to_user ?? ""),
    group_id: String(value?.group_id ?? ""),
    relation_type: String(value?.relation_type ?? ""),
  };
}

function sameSocialIdentity(left, right) {
  const a = socialIdentity(left);
  const b = socialIdentity(right);
  return Object.keys(a).every((key) => a[key] === b[key]);
}

function editingBridgePayload(method, pathOnly, payload) {
  if (!editingBridgeEnabled) return undefined;
  if (method === "GET" && pathOnly === "social/relations") {
    const groupId = String(payload?.group_id ?? "");
    return editingResponse({
      status: "ok",
      data: { relations: cloneJson(editingBridgeState.social.filter((item) => !groupId || item.group_id === groupId)) },
    });
  }
  if (method === "POST" && pathOnly === "social/create") {
    if (payload?.from_user === "validation-error") {
      return editingResponse({
        status: "error",
        code: "validation_error",
        message: "请修正关系字段",
        field_errors: { from_user: "该值用于浏览器校验测试" },
      });
    }
    const entity = {
      ...socialIdentity(payload),
      strength: Number(payload?.strength ?? 0.5),
      tags: Array.isArray(payload?.tags) ? [...payload.tags] : [],
      frequency: 0,
      last_interaction: 0,
      category: "other",
    };
    const revision = nextEditingRevision();
    editingBridgeState.social.unshift({ ...entity, revision });
    return editingResponse({ status: "ok", data: { entity, revision } });
  }
  if (method === "POST" && pathOnly === "social/update") {
    const index = editingBridgeState.social.findIndex((item) => sameSocialIdentity(item, payload?.identity));
    const current = editingBridgeState.social[index];
    if (!current) {
      return editingResponse({ status: "error", code: "not_found", message: "关系不存在" });
    }
    if (editingBridgeState.armSocialConflict) {
      editingBridgeState.armSocialConflict = false;
      const currentRevision = nextEditingRevision();
      const remote = { ...current, strength: Math.min(1, current.strength + 0.05), revision: currentRevision };
      editingBridgeState.social[index] = remote;
      const { revision: _revision, ...currentEntity } = remote;
      return editingResponse({
        status: "error",
        code: "edit_conflict",
        message: "关系已更新",
        data: { current_entity: currentEntity, current_revision: currentRevision },
      });
    }
    const changes = payload?.changes && typeof payload.changes === "object" ? payload.changes : {};
    const revision = nextEditingRevision();
    const updated = {
      ...current,
      ...changes,
      tags: Array.isArray(changes.tags) ? [...changes.tags] : [...current.tags],
      revision,
    };
    editingBridgeState.social[index] = updated;
    const { revision: _revision, ...entity } = updated;
    return editingResponse({ status: "ok", data: { entity, revision } });
  }
  if (method === "POST" && pathOnly === "social/batch") {
    const items = Array.isArray(payload?.items) ? payload.items : [];
    const succeededIds = [];
    for (const item of items) {
      const index = editingBridgeState.social.findIndex((entry) => sameSocialIdentity(entry, item.identity));
      if (index < 0) continue;
      const current = editingBridgeState.social[index];
      const tags = Array.isArray(payload?.params?.tags) ? payload.params.tags : [];
      editingBridgeState.social[index] = {
        ...current,
        tags: payload.action === "remove_tags"
          ? current.tags.filter((tag) => !tags.includes(tag))
          : [...new Set([...current.tags, ...tags])],
        revision: nextEditingRevision(),
      };
      succeededIds.push(socialIdentity(current));
    }
    return editingResponse({
      status: "ok",
      data: {
        total: items.length,
        succeeded_count: succeededIds.length,
        failed_count: items.length - succeededIds.length,
        succeeded_ids: succeededIds,
        failures: [],
      },
    });
  }
  if (method === "GET" && pathOnly === "affection/status") {
    const topUsers = editingBridgeState.affectionUsers.slice(0, 5);
    return editingResponse({
      status: "ok",
      data: {
        group_id: "group-smoke",
        total_affection: topUsers.reduce((total, user) => total + user.affection_score, 0),
        max_total_affection: 100,
        user_count: topUsers.length,
        current_mood: cloneJson(editingBridgeState.currentMood),
        top_users: cloneJson(topUsers),
      },
    });
  }
  if (method === "GET" && pathOnly === "affection/users") {
    const offset = Number(payload?.offset ?? 0);
    const limit = Number(payload?.limit ?? 50);
    return editingResponse({
      status: "ok",
      data: {
        group_id: String(payload?.group_id ?? "group-smoke"),
        users: cloneJson(editingBridgeState.affectionUsers.slice(offset, offset + limit)),
        total: editingBridgeState.affectionUsers.length,
        limit,
        offset,
      },
    });
  }
  if (method === "GET" && pathOnly === "affection/moods/history") {
    return editingResponse({
      status: "ok",
      data: {
        group_id: String(payload?.group_id ?? "group-smoke"),
        limit: Number(payload?.limit ?? 50),
        history: cloneJson(editingBridgeState.moodHistory),
      },
    });
  }
  if (method === "POST" && pathOnly === "affection/users/update") {
    const identity = payload?.identity ?? {};
    const index = editingBridgeState.affectionUsers.findIndex(
      (user) => user.group_id === identity.group_id && user.user_id === identity.user_id,
    );
    const current = editingBridgeState.affectionUsers[index];
    if (!current) return editingResponse({ status: "error", code: "not_found", message: "用户不存在" });
    const revision = nextEditingRevision();
    const score = Number(payload?.changes?.affection_score ?? current.affection_score);
    const entity = {
      ...current,
      affection_score: score,
      affection_level: score >= 50 ? "CLOSE" : "FRIENDLY",
      level_name: score >= 50 ? "亲密" : "友好",
      revision,
    };
    editingBridgeState.affectionUsers[index] = entity;
    const { revision: _revision, ...serial } = entity;
    return editingResponse({ status: "ok", data: { entity: serial, revision } });
  }
  if (method === "POST" && pathOnly === "affection/mood/set") {
    editingBridgeState.currentMood = {
      mood_type: String(payload?.mood_type ?? "calm"),
      intensity: Number(payload?.intensity ?? 0.5),
      duration_hours: Number(payload?.duration_hours ?? 4),
      description: String(payload?.description ?? ""),
      start_time: Date.now() / 1000,
      is_active: true,
    };
    editingBridgeState.moodHistory.unshift(cloneJson(editingBridgeState.currentMood));
    return editingResponse({ status: "ok", data: cloneJson(editingBridgeState.currentMood) });
  }
  if (method === "POST" && pathOnly === "affection/mood/reset") {
    editingBridgeState.currentMood = {
      mood_type: "calm",
      intensity: 0.5,
      duration_hours: 4,
      description: "系统默认情绪",
      start_time: Date.now() / 1000,
      is_active: true,
    };
    return editingResponse({ status: "ok", data: cloneJson(editingBridgeState.currentMood) });
  }
  return undefined;
}

async function launchBrowser() {
  const failures = [];
  for (const candidate of BROWSER_LAUNCH_CANDIDATES) {
    try {
      const options = createBrowserLaunchOptions(candidate.channel);
      const browser = await chromium.launch(options);
      return { browser, label: candidate.label };
    } catch (error) {
      failures.push(`${candidate.label}: ${error?.message ?? String(error)}`);
    }
  }
  throw new Error(
    "No Chromium-compatible browser is available for Dashboard browser smoke.\n"
      + failures.join("\n")
  );
}

function bridgePayload(endpoint, params = {}, method = "GET") {
  const pathOnly = String(endpoint || "").replace(/^page\/?/, "");
  const configResponse = configSmokeFixture.handle(method, pathOnly, params);
  if (configResponse !== undefined) return configResponse;
  if (pathOnly === "injection-strategy/catalog") return injectionCatalogPayload();
  if (pathOnly === "injection-strategy/summary") return injectionSummaryPayload(params);
  if (pathOnly === "injection-strategy/decisions") return injectionDecisionPagePayload(params);
  if (pathOnly === "injection-strategy/decisions/detail") {
    return injectionDecisionDetailPayload(params.decision_id);
  }
  if (pathOnly === "recall/trace/detail") return recallTracePayload(params.trace_id);
  const editingPayload = editingBridgePayload(method, pathOnly, params);
  if (editingPayload !== undefined) return editingPayload;
  if (pathOnly === "stats") {
    return {
      total_memories: 12,
      active_count: 8,
      archived_count: 3,
      deleted_count: 1,
      graph_nodes: 7,
      graph_edges: 5,
      atom_count: 10,
      graph_entries: 4,
      avg_importance: 0.64,
      status_breakdown: { active: 8, archived: 3, deleted: 1 },
      atom_breakdown: { fact: 6, preference: 5, event: 3, relation: 2, summary: 1, other_type: 1 },
      recent_sessions: [
        { session_id: "group-smoke-primary", message_count: 9 },
        { session_id: "group-smoke-secondary", message_count: 5 },
      ],
      daily_memory_counts: Array.from({ length: 90 }, (_, index) => ({
        date: new Date(Date.UTC(2026, 3, 14 + index)).toISOString().slice(0, 10),
        count: index < 60 ? 0 : index - 59,
      })),
      importance_distribution: {
        "0-1": 0,
        "1-2": 1,
        "2-3": 1,
        "3-4": 2,
        "4-5": 3,
        "5-6": 4,
        "6-7": 4,
        "7-8": 3,
        "8-9": 1,
        "9-10": 1,
      },
      atom_types: { fact: 4, note: 6 },
      sessions: { "group-smoke": { message_count: 3 } },
    };
  }
  if (pathOnly === "metrics/summary") {
    return {
      recall: { sample_count: 7, p50_total_ms: 42.4, p95_total_ms: 123.4 },
      background_tasks: { tracked: 5, active: 2, completed: 3, failed: 1 },
      provider: { status: "ready", attempts: 3, max_attempts: 60 },
      index: {
        validator_available: true,
        last_rebuild_success: true,
        last_rebuild_errors: 0,
        last_rebuild_total: 12,
        last_rebuild_duration_seconds: 1.2,
      },
      write_coordinator: {
        operations_total: 20,
        lock_retries_total: 4,
        failures_total: 1,
        last_error: null,
      },
      prometheus: { available: true, collector_count: 9 },
    };
  }
  if (pathOnly === "backup/list") {
    return {
      backups: [
        { name: "backup-smoke-a", file_count: 2, backup_timestamp: "2026-07-04T05:30:00Z" },
        { name: "backup-smoke-b", file_count: 3, backup_timestamp: "2026-07-04T05:35:00Z" },
      ],
    };
  }
  if (pathOnly === "graph/overview") {
    return { graph_nodes: 7, graph_edges: 5, graph_entries: 4, enabled: true };
  }
  if (pathOnly === "graph/search" || pathOnly === "graph/query") {
    return {
      nodes: [{ id: 1, label: "Alice", type: "person", weight: 1 }],
      edges: [],
      summary: { visible_node_count: 1, visible_edge_count: 0 },
    };
  }
  if (pathOnly === "memories") {
    if (!params.keyword) {
      return {
        memories: [
          {
            id: 1,
            content: "浏览器 smoke 记忆",
            text: "浏览器 smoke 记忆",
            importance: 0.8,
            metadata: {},
          },
        ],
        total: 1,
      };
    }
    return {
      memories: Array.from({ length: 20 }, (_, index) => ({
        id: `browser-search-memory-${index + 1}`,
        content: `浏览器 smoke 记忆 ${index + 1}`,
        text: `浏览器 smoke 记忆 ${index + 1}`,
        importance: 0.8,
        metadata: {},
      })),
      total: 20,
    };
  }
  if (pathOnly === "memory/detail") {
    return {
      memory: {
        id: params.id,
        content: `浏览器 smoke 精确详情 ${params.id}`,
        importance: 0.8,
        status: "active",
        type: "fact",
      },
    };
  }
  if (pathOnly === "knowledge/search") return knowledgeSmokeListPayload(params);
  if (pathOnly === "notes/search") return { notes: [], total: 0 };
  if (pathOnly === "jargon/stats") return { total_terms: 1, confirmed_terms: 0 };
  if (pathOnly === "jargon/candidates") {
    return { candidates: [{ term: "梗", score: 0.9, occurrences: 3 }] };
  }
  if (pathOnly === "jargon/meanings") return { meanings: [] };
  if (pathOnly === "groups") return { groups: [{ group_id: "group-smoke" }] };
  if (pathOnly === "profiles") {
    const profiles = Array.from({ length: 8 }, (_, index) => ({
      user_id: `profile-smoke-${index + 1}`,
      display_name: `Profile smoke ${index + 1}`,
      message_count: 20 - index,
      last_seen: "2026-07-18T08:00:00Z",
      group_id: "group-smoke",
      revision: `profile-revision-${index + 1}`,
      tags: index % 2 === 0 ? ["browser", "smoke"] : ["smoke"],
      preferences: { tone: index % 2 === 0 ? "concise" : "friendly" },
    }));
    return { profiles, total: profiles.length };
  }
  if (pathOnly === "knowledge") return knowledgeSmokeListPayload(params);
  if (pathOnly === "knowledge/detail") {
    const entry = KNOWLEDGE_SMOKE_ENTRIES.find((item) => item.entry_id === String(params.entry_id));
    return entry ? { entry: { ...entry, tags: [...entry.tags] } } : { entry: {} };
  }
  if (pathOnly === "knowledge/update" && method === "POST") {
    const entryId = String(params.entry_id ?? "");
    const index = KNOWLEDGE_SMOKE_ENTRIES.findIndex((item) => item.entry_id === entryId);
    if (index < 0) return { status: "error", code: "not_found", message: "知识不存在" };
    const changes = params.changes && typeof params.changes === "object" ? params.changes : {};
    KNOWLEDGE_SMOKE_ENTRIES[index] = {
      ...KNOWLEDGE_SMOKE_ENTRIES[index],
      ...changes,
      entry_id: entryId,
      tags: Array.isArray(changes.tags) ? [...changes.tags] : [...KNOWLEDGE_SMOKE_ENTRIES[index].tags],
    };
    return { entry: { ...KNOWLEDGE_SMOKE_ENTRIES[index], tags: [...KNOWLEDGE_SMOKE_ENTRIES[index].tags] }, revision: `knowledge-revision-${entryId}` };
  }
  if (pathOnly === "notes") return { notes: [{ id: 1, title: "Smoke note" }], total: 1, active_count: 1 };
  if (pathOnly === "learning/status") {
    return {
      hit_rate: 0.83,
      avg_quality: 0.812,
      total_trials: 18,
      total_corrections: 4,
      parameters: { retrieval_weight: 0.8, style_bias: 0.35 },
      history: [
        { timestamp: "2026-07-12T08:30:00Z", action: "adjusted", detail: "Raised retrieval weight" },
        { timestamp: "2026-07-12T08:00:00Z", action: "reviewed", detail: "Validated style preference" },
      ],
    };
  }
  if (pathOnly === "expression/patterns") {
    return {
      patterns: [
        {
          pattern_id: 1,
          group_id: "group-smoke",
          situation: "Greeting",
          expression: "Formal greeting",
          weight: 0.8,
          usage_count: 6,
        },
      ],
    };
  }
  if (pathOnly === "affection/status") {
    return {
      group_id: "group-smoke",
      total_affection: 48,
      max_total_affection: 100,
      user_count: 2,
      current_mood: {
        mood_type: "happy",
        intensity: 0.72,
        description: "群聊今天的氛围很积极。",
        is_active: true,
      },
      top_users: [
        { user_id: "alice", affection_score: 42, level_name: "友好", interaction_count: 8 },
        { user_id: "bob", affection_score: 6, level_name: "中立", interaction_count: 3 },
      ],
    };
  }
  if (pathOnly === "social/relations") {
    return {
      relations: [
        {
          from_user: "alice",
          to_user: "bob",
          relation_type: "friend",
          strength: 0.76,
          frequency: 9,
          group_id: "group-smoke",
          tags: ["pair", "project"],
          category: "emotional",
        },
      ],
    };
  }
  if (pathOnly === "evaluation/datasets") {
    return {
      datasets: [
        {
          name: "private_basic",
          case_count: 10,
          path: "tests/fixtures/retrieval/private_basic.jsonl",
          intents: ["preference"],
          chat_types: ["private"],
        },
        {
          name: "group_context",
          case_count: 12,
          path: "tests/fixtures/retrieval/group_context.jsonl",
          intents: ["relation", "fact"],
          chat_types: ["group"],
        },
      ],
    };
  }
  if (pathOnly === "evaluation/reports") {
    return {
      reports: [
        {
          report_id: "eval-smoke-latest",
          created_at: 1_782_000_000,
          baseline: "baseline",
          datasets: ["private_basic"],
          summary: {
            total_cases: 20,
            k: 5,
            recall_at_k: 0.9,
            mrr: 0.74,
            ndcg_at_k: 0.78,
            p95_latency_ms: 42.6,
          },
          variants: {
            baseline: {
              name: "baseline",
              status: "completed",
              summary: {
                total_cases: 20,
                k: 5,
                recall_at_k: 0.9,
                mrr: 0.74,
                ndcg_at_k: 0.78,
                p95_latency_ms: 42.6,
              },
            },
          },
          deltas: {},
          cases: [],
        },
      ],
    };
  }
  if (pathOnly === "evaluation/reports/detail" || pathOnly === "evaluation/report") {
    return {
      report: {
        report_id: "eval-smoke-latest",
        created_at: 1_782_000_000,
        baseline: "baseline",
        datasets: ["private_basic"],
        summary: {
          total_cases: 20,
          k: 5,
          recall_at_k: 0.9,
          mrr: 0.74,
          ndcg_at_k: 0.78,
          p95_latency_ms: 42.6,
        },
        variants: {},
        deltas: {},
        cases: [],
      },
    };
  }
  if (pathOnly === "evaluation/run") {
    return {
      report_id: "eval-smoke-run",
      created_at: 1_782_000_100,
      baseline: "baseline",
      datasets: ["private_basic"],
      summary: {
        total_cases: 20,
        k: 5,
        recall_at_k: 0.91,
        mrr: 0.75,
        ndcg_at_k: 0.79,
        p95_latency_ms: 40.1,
      },
      variants: {},
      deltas: {},
      cases: [],
    };
  }
  if (pathOnly === "recall/trace" || pathOnly === "recall/traces") {
    return recallTracePayload();
  }
  if (pathOnly === "diagnostics/health") {
    return {
      score: 82,
      level: "watch",
      domains: [
        { name: "provider", score: 90, status: "healthy", message: "LLM and embedding providers are ready." },
        { name: "recall", score: 78, status: "watch", message: "Recall p95 is above the preferred band." },
        { name: "write", score: 96, status: "healthy", message: "Write coordinator is accepting operations." },
        { name: "scheduler", score: 74, status: "watch", message: "Backfill scheduler has recent retry history." },
        { name: "index", score: 66, status: "degraded", message: "Index validator recommends a rebuild." },
        { name: "prometheus", score: 100, status: "healthy", message: "Prometheus collectors are registered." },
      ],
      recommended_actions: ["Review index drift before peak traffic."],
    };
  }
  if (pathOnly === "diagnostics/events") {
    return {
      events: [
        {
          event_id: "diag-index-drift",
          created_at: "2026-07-05T00:00:00Z",
          domain: "index",
          severity: "warning",
          title: "Index drift detected",
          message: "Document and vector index counts differ by 2 entries.",
          source: "index_validator",
          payload: { expected: 128, actual: 126 },
          resolved_at: null,
        },
      ],
      total: 1,
    };
  }
  if (pathOnly === "diagnostics/actions/run") return { success: true };
  if (pathOnly === "review/items") {
    return {
      items: [
        {
          item_id: "review-smoke-duplicate",
          memory_id: "mem-smoke-duplicate",
          reasons: ["duplicate"],
          severity: "medium",
          status: "open",
          content_preview: "重复记忆：用户周末喜欢在安静咖啡馆工作，偏好靠窗位置。",
          metadata: {
            provenance: "atom_store",
            session_id: "sess-smoke",
            source: "quality_scorer",
          },
          created_at: 1_782_000_000,
          updated_at: 1_782_000_100,
        },
      ],
      total: 1,
    };
  }
  if (pathOnly === "review/items/detail") {
    return {
      item: {
        item_id: "review-smoke-duplicate",
        memory_id: "mem-smoke-duplicate",
        reasons: ["duplicate"],
        severity: "medium",
        status: "open",
        content_preview: "重复记忆：用户周末喜欢在安静咖啡馆工作，偏好靠窗位置。",
        metadata: {
          provenance: "atom_store",
          session_id: "sess-smoke",
          source: "quality_scorer",
        },
        created_at: 1_782_000_000,
        updated_at: 1_782_000_100,
      },
      actions: [
        {
          action_id: "review-action-smoke",
          item_id: "review-smoke-duplicate",
          action: "flagged",
          actor_id: "system",
          payload: { reason: "duplicate" },
          created_at: 1_782_000_000,
        },
      ],
    };
  }
  if (pathOnly === "review/refresh" || pathOnly === "review/action") return { success: true };
  return {};
}

function ok(data) {
  return { status: "ok", data };
}

function assertText(text, expected, route) {
  const values = Array.isArray(expected) ? expected : [expected];
  for (const value of values) {
    if (!text.includes(value)) {
      throw new Error(`Dashboard route ${route} did not render expected text: ${value}`);
    }
  }
}

async function waitForRootText(page, expected, route) {
  const values = Array.isArray(expected) ? expected : [expected];
  try {
    await page.waitForFunction(
      ({ items, loadingText }) => {
        const text = document.querySelector("#root")?.innerText ?? "";
        return (
          items.every((item) => text.includes(item))
          && loadingText.every((item) => !text.includes(item))
        );
      },
      { items: values, loadingText: ROUTE_LOADING_TEXT },
      { timeout: 5_000 }
    );
  } catch (error) {
    const rootText = await page.locator("#root").innerText();
    const missing = values.filter((item) => !rootText.includes(item));
    const lingeringLoading = ROUTE_LOADING_TEXT.filter((item) => rootText.includes(item));
    throw new Error(
      `Dashboard route ${route} did not become ready. Missing: ${missing.join(", ") || "none"}; `
        + `loading text: ${lingeringLoading.join(", ") || "none"}; root text: ${rootText}`,
      { cause: error }
    );
  }
  assertText(await page.locator("#root").innerText(), values, route);
}

async function switchDashboardLanguage(page, language, expectedDocumentLang) {
  const languageOrder = ["zh", "en", "ru"];
  const languageButtonNames = { zh: "语言", en: "Language", ru: "Язык" };
  let currentLanguage = await page.evaluate(() => {
    const stored = window.localStorage.getItem("lmem_lang");
    if (stored === "zh" || stored === "en" || stored === "ru") return stored;
    const documentLanguage = document.documentElement.lang.slice(0, 2).toLowerCase();
    return documentLanguage === "en" || documentLanguage === "ru" ? documentLanguage : "zh";
  });

  for (let step = 0; currentLanguage !== language && step < languageOrder.length; step += 1) {
    await page.getByRole("button", {
      name: languageButtonNames[currentLanguage],
      exact: true,
    }).click();
    currentLanguage = languageOrder[(languageOrder.indexOf(currentLanguage) + 1) % languageOrder.length];
    await page.waitForFunction(
      (nextLanguage) => window.localStorage.getItem("lmem_lang") === nextLanguage,
      currentLanguage,
      { timeout: 5_000 }
    );
  }

  if (currentLanguage !== language) {
    throw new Error(`Unable to switch Dashboard language to ${language}`);
  }
  await page.waitForFunction(
    (nextDocumentLang) => document.documentElement.lang === nextDocumentLang,
    expectedDocumentLang,
    { timeout: 5_000 }
  );
}

async function assertNoHorizontalOverflow(page, label) {
  const measurements = await page.evaluate(() => {
    const pageContents = document.querySelectorAll('[data-slot="page-content"]');
    const pageContent = pageContents.length > 0 ? pageContents[pageContents.length - 1] : null;
    const targets = [
      ["documentElement", document.documentElement],
      ["body", document.body],
      ["#root", document.querySelector("#root")],
      ['[data-slot="page-content"]', pageContent],
    ];
    return targets.map(([target, element]) => ({
      target,
      present: Boolean(element),
      clientWidth: element?.clientWidth ?? 0,
      scrollWidth: element?.scrollWidth ?? 0,
      overflow: element ? element.scrollWidth - element.clientWidth : Number.NaN,
    }));
  });
  const invalid = measurements.filter((item) => (
    !item.present
    || item.clientWidth <= 0
    || !Number.isFinite(item.overflow)
    || item.overflow > 1
  ));
  if (invalid.length > 0) {
    const offenders = await page.evaluate(() => {
      const viewportRight = document.documentElement.clientWidth;
      return [...document.querySelectorAll('[data-slot="page-content"] *')]
        .map((element) => {
          const rect = element.getBoundingClientRect();
          return {
            tag: element.tagName.toLowerCase(),
            slot: element.getAttribute("data-slot"),
            className: element.getAttribute("class"),
            text: (element.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 120),
            left: Math.round(rect.left),
            right: Math.round(rect.right),
            width: Math.round(rect.width),
            scrollWidth: element.scrollWidth,
            clientWidth: element.clientWidth,
          };
        })
        .filter((item) => item.right > viewportRight + 1 && item.width > 1)
        .sort((left, right) => right.right - left.right)
        .slice(0, 12);
    });
    throw new Error(
      `Dashboard route ${label} has horizontal overflow: ${JSON.stringify(invalid)}; `
        + `offenders=${JSON.stringify(offenders)}`
    );
  }
}

async function assertSocialTableWorkspace(page) {
  const tableRoot = page.locator('[data-table-id="social-relations"]');
  await tableRoot.waitFor({ state: "visible", timeout: 5_000 });
  const workspace = await tableRoot.evaluate((root) => {
    const frame = root.closest('[data-slot="page-frame"]');
    const content = root.closest('[data-slot="page-content"]');
    const panel = root.closest('[role="tabpanel"]');
    const frameRect = frame?.getBoundingClientRect();
    const contentRect = content?.getBoundingClientRect();
    return {
      layout: frame?.getAttribute("data-layout") ?? null,
      hasCard: Boolean(root.closest('[data-slot="card"]')),
      frameWidth: frameRect?.width ?? 0,
      contentWidth: contentRect?.width ?? 0,
      contentOverflowY: content ? getComputedStyle(content).overflowY : null,
      panelOverflowY: panel ? getComputedStyle(panel).overflowY : null,
    };
  });
  if (
    workspace.layout !== "dense"
    || workspace.hasCard
    || workspace.frameWidth <= 0
    || workspace.contentWidth <= 1441
    || workspace.frameWidth - workspace.contentWidth > 1
    || !["auto", "scroll"].includes(workspace.contentOverflowY)
    || workspace.panelOverflowY !== "hidden"
  ) {
    throw new Error(`Social table workspace is not dense and full width: ${JSON.stringify(workspace)}`);
  }
  await assertNoHorizontalOverflow(page, "#/social:wide-table");
}

function assertScreenshotLooksNonEmpty(buffer, label) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 10_000) {
    throw new Error(`Dashboard browser smoke screenshot is unexpectedly small for ${label}`);
  }
}

function readPngDimensions(buffer) {
  const pngSignature = "89504e470d0a1a0a";
  if (!Buffer.isBuffer(buffer) || buffer.subarray(0, 8).toString("hex") !== pngSignature) {
    throw new Error("Dashboard browser smoke screenshot is not a PNG");
  }
  return {
    width: buffer.readUInt32BE(16),
    height: buffer.readUInt32BE(20),
  };
}

function assertScreenshotMatchesBaseline(buffer, filename, label) {
  assertScreenshotLooksNonEmpty(buffer, label);
  const baseline = SCREENSHOT_BASELINES[filename];
  if (!baseline) {
    throw new Error(`Dashboard browser smoke has no screenshot baseline for ${filename}`);
  }
  const dimensions = readPngDimensions(buffer);
  if (dimensions.width !== baseline.width || dimensions.height !== baseline.height) {
    throw new Error(
      `Dashboard browser smoke screenshot ${filename} size changed: `
        + `${dimensions.width}x${dimensions.height}, expected ${baseline.width}x${baseline.height}`
    );
  }
  if (buffer.length < baseline.minBytes) {
    throw new Error(
      `Dashboard browser smoke screenshot ${filename} is below baseline bytes: `
        + `${buffer.length}, expected at least ${baseline.minBytes}`
    );
  }
  return { filename, label, bytes: buffer.length, ...dimensions, minBytes: baseline.minBytes };
}

async function waitForVisualStability(page) {
  await page.waitForFunction(() => {
    const root = document.querySelector("#root");
    let element = document.querySelector('[data-slot="page-frame"]');
    if (!root || !element) return false;
    while (element && element !== root) {
      if (Number.parseFloat(getComputedStyle(element).opacity) < 0.99) return false;
      element = element.parentElement;
    }
    return true;
  }, undefined, { timeout: 3_000 });
  await page.evaluate(async () => {
    const finiteAnimations = document.getAnimations().filter((animation) => {
      const duration = animation.effect?.getTiming().duration;
      return animation.playState === "running"
        && typeof duration === "number"
        && Number.isFinite(duration)
        && duration <= 2_000;
    });
    await Promise.all(finiteAnimations.map((animation) => animation.finished.catch(() => undefined)));
    await new Promise((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    });
  });
}

async function captureBaselineScreenshot(page, screenshotPath, label) {
  await waitForVisualStability(page);
  const pageContent = page.locator('[data-slot="page-content"]').last();
  if (await pageContent.count()) {
    const box = await pageContent.boundingBox();
    const viewport = page.viewportSize();
    if (
      !box
      || !viewport
      || box.width <= 0
      || box.height <= 0
      || box.x >= viewport.width
      || box.x + box.width <= 0
      || box.y >= viewport.height
      || box.y + box.height <= 0
    ) {
      throw new Error(
        `Dashboard page content is outside the viewport for ${label}: `
          + `box=${JSON.stringify(box)}, viewport=${JSON.stringify(viewport)}`
      );
    }
    const firstChild = pageContent.locator(":scope > *").first();
    if (await firstChild.count()) {
      const childBox = await firstChild.boundingBox();
      if (
        !childBox
        || childBox.width <= 0
        || childBox.height <= 0
        || childBox.x >= box.x + box.width
        || childBox.x + childBox.width <= box.x
        || childBox.y >= box.y + box.height
        || childBox.y + childBox.height <= box.y
      ) {
        throw new Error(
          `Dashboard page content has no visible first child for ${label}: `
            + `contentBox=${JSON.stringify(box)}, childBox=${JSON.stringify(childBox)}`
        );
      }
    }
  }
  const screenshot = await page.screenshot({ path: screenshotPath, fullPage: false });
  return assertScreenshotMatchesBaseline(screenshot, path.basename(screenshotPath), label);
}

async function assertEditingDialogReady(page, title) {
  const dialog = page.getByRole("dialog", { name: title, exact: true });
  await dialog.waitFor({ state: "visible", timeout: 5_000 });
  const form = dialog.locator("form").first();
  if (await form.count()) {
    await form.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
  }
  const snapshot = await dialog.evaluate((element, expectedTitle) => {
    const text = element.textContent ?? "";
    const footer = element.querySelector('[data-testid="entity-editor-footer"]')
      ?? element.querySelector("form + div");
    return {
      visibleTitles: [...element.querySelectorAll("h1, h2, h3")]
        .map((titleElement) => titleElement.textContent?.replace(/\s+/g, " ").trim() ?? ""),
      loadingOverlayVisible: ["加载中...", "Loading...", "Загрузка..."]
        .some((loadingText) => text.includes(loadingText)),
      fixedFooterVisible: Boolean(footer && footer.getBoundingClientRect().height > 0),
      expectedTitle,
    };
  }, title);
  assertEditorReadiness(snapshot, { expectedTitle: title });

  if (await form.count() && await form.locator('[data-slot="field"]').count()) {
    const geometry = await form.evaluate((element) => {
      const fields = element.querySelectorAll('[data-slot="field"]');
      const lastField = fields[fields.length - 1];
      const viewportRect = element.getBoundingClientRect();
      const fieldRect = lastField?.getBoundingClientRect();
      return {
        scrollViewport: { top: viewportRect.top, bottom: viewportRect.bottom },
        lastField: { top: fieldRect?.top ?? Number.NaN, bottom: fieldRect?.bottom ?? Number.NaN },
      };
    });
    assertEditorViewport(geometry);
  }
  return dialog;
}

async function assertMobileEditingOverflow(page, dialog, label) {
  const measurements = await page.evaluate((dialogElement) => {
    const targets = [
      ["documentElement", document.documentElement],
      ["body", document.body],
      ["#root", document.querySelector("#root")],
      ["editor", dialogElement],
      ["editor form", dialogElement?.querySelector("form")],
    ];
    return targets.map(([targetLabel, element]) => ({
      label: targetLabel,
      clientWidth: element?.clientWidth ?? 0,
      scrollWidth: element?.scrollWidth ?? 0,
    }));
  }, await dialog.elementHandle());
  try {
    assertNoHorizontalOverflowMeasurements(measurements);
  } catch (error) {
    throw new Error(`${label}: ${error.message}`);
  }
}

async function captureEditingScreenshot(page, screenshotsDir, filename, label) {
  await waitForVisualStability(page);
  const screenshotPath = path.join(screenshotsDir, filename);
  const screenshot = await page.screenshot({ path: screenshotPath, fullPage: false });
  return assertScreenshotMatchesBaseline(screenshot, filename, label);
}

async function assertPinnedTableCellsDoNotOverlap(table, label) {
  const groups = await table.evaluate((element) => {
    const rows = [
      element.querySelector("thead tr"),
      element.querySelector("tbody tr"),
    ].filter(Boolean);
    return rows.flatMap((row) => {
      const left = [];
      const right = [];
      row.querySelectorAll("th,td").forEach((cell) => {
        const style = window.getComputedStyle(cell);
        if (style.position !== "sticky") return;
        const rect = cell.getBoundingClientRect();
        if (style.left !== "auto") left.push({ left: rect.left, right: rect.right });
        if (style.right !== "auto") right.push({ left: rect.left, right: rect.right });
      });
      return [left, right];
    });
  });
  for (const [index, cells] of groups.entries()) {
    const sorted = [...cells].sort((left, right) => left.left - right.left);
    for (let cursor = 1; cursor < sorted.length; cursor += 1) {
      if (sorted[cursor - 1].right > sorted[cursor].left + 1) {
        throw new Error(`${label} has overlapping sticky cells in group ${index}`);
      }
    }
  }
}

async function assertEditorSheetStructure(page, dialog, label) {
  const geometry = await dialog.evaluate((element) => {
    const body = element.querySelector('[data-testid="entity-editor-body"]');
    const footer = element.querySelector('[data-testid="entity-editor-footer"]');
    const bodyRect = body?.getBoundingClientRect();
    const footerRect = footer?.getBoundingClientRect();
    return {
      bodyOverflowY: body ? window.getComputedStyle(body).overflowY : "",
      bodyClientHeight: body?.clientHeight ?? 0,
      bodyScrollHeight: body?.scrollHeight ?? 0,
      bodyBottom: bodyRect?.bottom ?? 0,
      footerTop: footerRect?.top ?? 0,
      footerBottom: footerRect?.bottom ?? 0,
      footerHeight: footerRect?.height ?? 0,
      viewportHeight: window.innerHeight,
    };
  });
  if (geometry.bodyOverflowY !== "auto" && geometry.bodyOverflowY !== "scroll") {
    throw new Error(`${label} editor body is not independently scrollable: ${JSON.stringify(geometry)}`);
  }
  if (geometry.footerHeight <= 0 || geometry.footerBottom > geometry.viewportHeight + 1) {
    throw new Error(`${label} editor footer is not visible: ${JSON.stringify(geometry)}`);
  }
  if (geometry.footerTop < geometry.bodyBottom - 1) {
    throw new Error(`${label} editor body overlaps footer: ${JSON.stringify(geometry)}`);
  }
}

async function runKnowledgeTableSmoke(page, browser, errors, screenshotsDir) {
  const screenshots = [];
  await navigateSidebar(page, "知识库", "#/knowledge", ["知识库", "新建条目"]);
  await page.locator('[data-table-id="knowledge"]').waitFor({ state: "visible", timeout: 10_000 });
  await page.waitForFunction(() => {
    const table = document.querySelector('[data-table-id="knowledge"] table');
    return Boolean(table && table.querySelectorAll("tbody tr").length >= 10)
      && !document.querySelector('[data-table-id="knowledge"] [role="status"]');
  }, undefined, { timeout: 10_000 });
  await assertNoHorizontalOverflow(page, "#/knowledge:table");
  await assertPinnedTableCellsDoNotOverlap(
    page.locator('[data-table-id="knowledge"] table'),
    "knowledge table",
  );
  screenshots.push(await captureBaselineScreenshot(
    page,
    path.join(screenshotsDir, "knowledge-table-default.png"),
    "knowledge-table-default",
  ));

  const table = page.locator('[data-table-id="knowledge"]');
  await page.getByRole("button", { name: "表格视图", exact: true }).click();
  const columnMenu = page.getByRole("menu");
  await columnMenu.getByRole("menuitemcheckbox", { name: "置信度", exact: true }).click();
  await columnMenu.getByRole("menuitemcheckbox", { name: "访问次数", exact: true }).click();
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => {
    const headers = [...document.querySelectorAll('[data-table-id="knowledge"] thead th')]
      .map((header) => header.textContent ?? "");
    return !headers.some((header) => header.includes("置信度"))
      && !headers.some((header) => header.includes("访问次数"));
  }, undefined, { timeout: 5_000 });
  await assertPinnedTableCellsDoNotOverlap(table.locator("table"), "knowledge columns view");
  screenshots.push(await captureBaselineScreenshot(
    page,
    path.join(screenshotsDir, "knowledge-table-columns.png"),
    "knowledge-table-columns",
  ));

  const firstRow = table.locator("tbody tr").first();
  const title = (await firstRow.locator("td").nth(1).innerText()).trim();
  await firstRow.click();
  const editor = await assertEditingDialogReady(page, title);
  await assertEditorSheetStructure(page, editor, "knowledge view");
  screenshots.push(await captureEditingScreenshot(
    page,
    screenshotsDir,
    "knowledge-editor-view.png",
    "knowledge-editor-view",
  ));
  await editor.getByRole("button", { name: "编辑", exact: true }).click();
  const editingEditor = await assertEditingDialogReady(page, title);
  await editingEditor.getByLabel("内容", { exact: true }).fill("浏览器 smoke 编辑后的知识内容");
  await assertEditorSheetStructure(page, editingEditor, "knowledge edit");
  if (!(await editingEditor.textContent()).includes("未保存")) {
    throw new Error("Knowledge editor did not expose its dirty status");
  }
  screenshots.push(await captureEditingScreenshot(
    page,
    screenshotsDir,
    "knowledge-editor-edit.png",
    "knowledge-editor-edit",
  ));
  await editingEditor.getByRole("button", { name: "取消", exact: true }).click();
  await editingEditor.getByRole("button", { name: "关闭", exact: true }).click();
  await editingEditor.waitFor({ state: "hidden", timeout: 5_000 });

  const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
  try {
    const mobilePage = await mobileContext.newPage();
    collectPageErrors(mobilePage, errors);
    await installBridge(mobilePage);
    await mobilePage.goto(`${pathToFileURL(htmlPath).href}#/knowledge`, { waitUntil: "load" });
    await mobilePage.bringToFront();
    await mobilePage.waitForSelector("#root > *", { timeout: 10_000 });
    await waitForRootText(mobilePage, ["知识库", "新建条目"], "#/knowledge:mobile");
    const mobileTable = mobilePage.locator('[data-table-id="knowledge"]');
    await mobileTable.waitFor({ state: "visible", timeout: 10_000 });
    await mobilePage.waitForFunction(() => document.querySelectorAll('[data-table-id="knowledge"] tbody tr').length >= 10);
    await assertNoHorizontalOverflow(mobilePage, "#/knowledge:mobile-table");
    await assertPinnedTableCellsDoNotOverlap(mobileTable.locator("table"), "mobile knowledge table");
    screenshots.push(await captureBaselineScreenshot(
      mobilePage,
      path.join(screenshotsDir, "mobile-knowledge-table.png"),
      "mobile-knowledge-table",
    ));
    const mobileRow = mobileTable.locator("tbody tr").first();
    const mobileTitle = (await mobileRow.locator("td").nth(1).innerText()).trim();
    await mobileRow.click();
    let mobileEditor = await assertEditingDialogReady(mobilePage, mobileTitle);
    await mobileEditor.getByRole("button", { name: "编辑", exact: true }).click();
    mobileEditor = await assertEditingDialogReady(mobilePage, mobileTitle);
    await assertMobileEditingOverflow(mobilePage, mobileEditor, "mobile knowledge editor");
    await assertEditorSheetStructure(mobilePage, mobileEditor, "mobile knowledge editor");
    screenshots.push(await captureEditingScreenshot(
      mobilePage,
      screenshotsDir,
      "mobile-knowledge-editor.png",
      "mobile-knowledge-editor",
    ));
  } finally {
    await mobileContext.close();
  }
  return screenshots;
}

async function runUnifiedEditingSmoke(page, browser, errors, screenshotsDir) {
  resetEditingBridgeState();
  editingBridgeEnabled = true;
  const screenshots = [];
  const socialTitle = "关系：task18-alice → task18-bob";

  try {
    await page.bringToFront();
    await page.evaluate(() => {
      window.location.hash = "#/social";
    });
    await waitForRootText(page, ["社交关系", "新建关系"], "#/social:editing");

    await page.getByRole("button", { name: "新建关系", exact: true }).click();
    const createDialog = await assertEditingDialogReady(page, "新建关系");
    await createDialog.getByLabel("发起用户", { exact: true }).fill("validation-error");
    await createDialog.getByLabel("目标用户", { exact: true }).fill("task18-bob");
    await createDialog.getByLabel("群组 ID", { exact: true }).fill("group-smoke");
    await createDialog.getByRole("button", { name: "创建", exact: true }).click();

    const validationSummary = createDialog.getByRole("alert");
    await validationSummary.waitFor({ state: "visible", timeout: 5_000 });
    if (await createDialog.getByRole("alert").count() !== 1) {
      throw new Error("Social create must expose exactly one validation summary");
    }
    const validationText = (await validationSummary.textContent()) ?? "";
    assertText(validationText, ["请修正以下字段", "该值用于浏览器校验测试"], "social-create-validation");
    screenshots.push(
      await captureEditingScreenshot(
        page,
        screenshotsDir,
        "editing-error-summary.png",
        "editing-error-summary",
      ),
    );

    await createDialog.getByLabel("发起用户", { exact: true }).fill("task18-alice");
    await createDialog.getByRole("button", { name: "创建", exact: true }).click();
    await createDialog.waitFor({ state: "hidden", timeout: 5_000 });

    let socialSheet = await assertEditingDialogReady(page, socialTitle);
    screenshots.push(
      await captureEditingScreenshot(
        page,
        screenshotsDir,
        "editing-social-sheet.png",
        "editing-social-sheet",
      ),
    );

    await socialSheet.getByRole("button", { name: "编辑", exact: true }).click();
    socialSheet = await assertEditingDialogReady(page, socialTitle);
    await socialSheet.getByLabel("关系强度", { exact: true }).fill("0.75");
    const tagInput = socialSheet.getByRole("textbox", { name: "标签", exact: true });
    await tagInput.fill("task18-browser");
    await tagInput.press("Enter");
    await socialSheet.getByRole("button", { name: "保存", exact: true }).click();
    await socialSheet.getByRole("button", { name: "编辑", exact: true }).waitFor({ state: "visible", timeout: 5_000 });
    const savedRelation = editingBridgeState.social.find(
      (relation) => relation.from_user === "task18-alice" && relation.to_user === "task18-bob",
    );
    if (savedRelation?.strength !== 0.75 || !savedRelation.tags.includes("task18-browser")) {
      throw new Error("Social save did not persist the edited strength and tag");
    }

    await socialSheet.getByRole("button", { name: "编辑", exact: true }).click();
    socialSheet = await assertEditingDialogReady(page, socialTitle);
    await socialSheet.getByLabel("关系强度", { exact: true }).fill("0.76");
    await socialSheet.getByRole("button", { name: "关闭", exact: true }).click();

    let unsavedDialog = page.getByRole("dialog", { name: "要离开配置页吗？", exact: true });
    await unsavedDialog.waitFor({ state: "visible", timeout: 5_000 });
    assertDialogActions(
      await unsavedDialog.getByRole("button").allTextContents(),
      ["继续编辑", "放弃更改并离开"],
      "social unsaved dialog",
    );
    await unsavedDialog.getByRole("button", { name: "继续编辑", exact: true }).click();
    await unsavedDialog.waitFor({ state: "hidden", timeout: 5_000 });
    await socialSheet.getByRole("button", { name: "关闭", exact: true }).click();
    unsavedDialog = page.getByRole("dialog", { name: "要离开配置页吗？", exact: true });
    await unsavedDialog.waitFor({ state: "visible", timeout: 5_000 });
    await unsavedDialog.getByRole("button", { name: "放弃更改并离开", exact: true }).click();
    await page.getByRole("dialog", { name: socialTitle, exact: true }).waitFor({ state: "hidden", timeout: 5_000 });

    await page.getByRole("row")
      .filter({ hasText: "task18-alice" })
      .filter({ hasText: "task18-bob" })
      .first()
      .click();
    socialSheet = await assertEditingDialogReady(page, socialTitle);
    await socialSheet.getByRole("button", { name: "编辑", exact: true }).click();
    socialSheet = await assertEditingDialogReady(page, socialTitle);
    await socialSheet.getByLabel("关系强度", { exact: true }).fill("0.8");
    editingBridgeState.armSocialConflict = true;
    await socialSheet.getByRole("button", { name: "保存", exact: true }).click();

    const conflictDialog = page.getByRole("dialog", { name: "关系编辑冲突", exact: true });
    await conflictDialog.waitFor({ state: "visible", timeout: 5_000 });
    assertDialogActions(
      await conflictDialog.getByRole("button").allTextContents(),
      ["载入 AstrBot 版本", "重新应用本地值"],
      "social conflict dialog",
    );
    screenshots.push(
      await captureEditingScreenshot(
        page,
        screenshotsDir,
        "editing-social-conflict.png",
        "editing-social-conflict",
      ),
    );
    await conflictDialog.getByRole("button", { name: "载入 AstrBot 版本", exact: true }).click();
    await conflictDialog.waitFor({ state: "hidden", timeout: 5_000 });
    await socialSheet.getByRole("button", { name: "关闭", exact: true }).click();
    await socialSheet.waitFor({ state: "hidden", timeout: 5_000 });

    await page.getByRole("checkbox", {
      name: "选择关系 task18-alice task18-bob",
      exact: true,
    }).click();
    await page.getByText("已选择 1 项", { exact: true }).waitFor({ state: "visible", timeout: 5_000 });
    await page.getByRole("button", { name: "编辑标签", exact: true }).waitFor({ state: "visible", timeout: 5_000 });
    screenshots.push(
      await captureEditingScreenshot(
        page,
        screenshotsDir,
        "editing-batch-toolbar.png",
        "editing-batch-toolbar",
      ),
    );

    const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    try {
      const mobilePage = await mobileContext.newPage();
      collectPageErrors(mobilePage, errors);
      await installBridge(mobilePage);
      await mobilePage.goto(pathToFileURL(htmlPath).href, { waitUntil: "load" });
      await mobilePage.bringToFront();
      await mobilePage.waitForSelector("#root > *", { timeout: 10_000 });
      await mobilePage.evaluate(() => {
        window.location.hash = "#/affection";
      });
      await waitForRootText(
        mobilePage,
        ["好感度与情绪", "所有好感用户", "task18-affection-user"],
        "#/affection:editing-mobile",
      );

      const affectionRow = mobilePage.getByRole("row")
        .filter({ hasText: "task18-affection-user" })
        .first();
      await affectionRow.focus();
      await affectionRow.press("Enter");
      const affectionTitle = "好感：task18-affection-user";
      let affectionSheet = await assertEditingDialogReady(mobilePage, affectionTitle);
      await affectionSheet.getByRole("button", { name: "编辑", exact: true }).click();
      affectionSheet = await assertEditingDialogReady(mobilePage, affectionTitle);
      await assertMobileEditingOverflow(mobilePage, affectionSheet, "mobile affection editor");
      screenshots.push(
        await captureEditingScreenshot(
          mobilePage,
          screenshotsDir,
          "editing-mobile-affection.png",
          "editing-mobile-affection",
        ),
      );
      await affectionSheet.getByLabel("好感度", { exact: true }).fill("55");
      await affectionSheet.getByRole("button", { name: "保存", exact: true }).click();
      await affectionSheet.getByRole("button", { name: "编辑", exact: true }).waitFor({ state: "visible", timeout: 5_000 });
      await affectionSheet.getByRole("button", { name: "关闭", exact: true }).click();
      await affectionSheet.waitFor({ state: "hidden", timeout: 5_000 });

      await mobilePage.getByRole("button", { name: "编辑情绪", exact: true }).click();
      const moodDialog = await assertEditingDialogReady(mobilePage, "编辑情绪");
      await assertMobileEditingOverflow(mobilePage, moodDialog, "mobile mood editor");
      screenshots.push(
        await captureEditingScreenshot(
          mobilePage,
          screenshotsDir,
          "editing-mobile-mood.png",
          "editing-mobile-mood",
        ),
      );
      await moodDialog.getByLabel("情绪描述", { exact: true }).fill("Task 18 mobile mood");
      await moodDialog.getByRole("button", { name: "设置情绪", exact: true }).click();
      await moodDialog.waitFor({ state: "hidden", timeout: 5_000 });

      await mobilePage.getByRole("button", { name: "恢复默认情绪", exact: true }).click();
      const resetDialog = mobilePage.getByRole("dialog", { name: "恢复默认情绪", exact: true });
      await resetDialog.waitFor({ state: "visible", timeout: 5_000 });
      await resetDialog.getByRole("button", { name: "恢复默认情绪", exact: true }).click();
      await resetDialog.waitFor({ state: "hidden", timeout: 5_000 });
    } finally {
      await mobileContext.close();
    }

    return screenshots;
  } finally {
    editingBridgeEnabled = false;
  }
}

async function runGlobalSearchScrollAndTargetSmoke(page, screenshotsDir) {
  await page.keyboard.press("Control+K");
  const search = page.getByRole("combobox", { name: "全局搜索" });
  await search.fill("浏览器");
  await page.waitForFunction(
    () => document.querySelectorAll("#global-search-results [role='option']").length >= 20,
    undefined,
    { timeout: 5_000 },
  );

  const listbox = page.locator("#global-search-results");
  const viewport = listbox.locator("..");
  const before = await viewport.evaluate((element) => ({
    scrollTop: element.scrollTop,
    scrollHeight: element.scrollHeight,
    clientHeight: element.clientHeight,
  }));
  if (before.scrollHeight <= before.clientHeight) {
    throw new Error(`Global search results did not overflow: ${JSON.stringify(before)}`);
  }

  const viewportBox = await viewport.boundingBox();
  if (!viewportBox) throw new Error("Global search result viewport has no layout box");
  await page.mouse.move(
    viewportBox.x + viewportBox.width / 2,
    viewportBox.y + viewportBox.height / 2,
  );
  await page.mouse.wheel(0, 700);
  await page.waitForFunction(
    () => (document.querySelector("#global-search-results")?.parentElement?.scrollTop ?? 0) > 0,
    undefined,
    { timeout: 3_000 },
  );

  const after = await viewport.evaluate((element) => element.scrollTop);
  if (after <= before.scrollTop) {
    throw new Error(`Mouse wheel did not scroll global search results: ${before.scrollTop} -> ${after}`);
  }

  const screenshots = [
    await captureBaselineScreenshot(
      page,
      path.join(screenshotsDir, "global-search-scroll.png"),
      "global-search-scroll",
    ),
  ];

  await page.getByRole("option", { name: /浏览器 smoke 记忆 20/ }).click();
  await page.waitForFunction(
    () => window.location.hash === "#/memory",
    undefined,
    { timeout: 5_000 },
  );
  await page.getByText(
    "浏览器 smoke 精确详情 browser-search-memory-20",
    { exact: true },
  ).waitFor({ state: "visible", timeout: 5_000 });
  if (await page.getByRole("dialog", { name: "全局搜索" }).count()) {
    throw new Error("Global search dialog remained open after exact memory navigation");
  }
  await assertNoHorizontalOverflow(page, "#/memory:global-search-target");
  screenshots.push(
    await captureBaselineScreenshot(
      page,
      path.join(screenshotsDir, "global-search-memory-target.png"),
      "global-search-memory-target",
    ),
  );
  const memoryDetail = page.getByRole("dialog", { name: "记忆详情", exact: true });
  await memoryDetail.getByRole("button", { name: "关闭", exact: true }).click();
  await memoryDetail.waitFor({ state: "hidden", timeout: 5_000 });
  return screenshots;
}

async function navigateSidebar(page, label, expectedHash, expectedText) {
  await page.getByRole("button", { name: label }).click();
  await page.waitForFunction(
    (nextHash) => window.location.hash === nextHash,
    expectedHash,
    { timeout: 5_000 }
  );
  await waitForRootText(page, expectedText, expectedHash);
}

async function clickSidebarNav(page, label, expectedHash, expectedText, screenshotPath) {
  await navigateSidebar(page, label, expectedHash, expectedText);
  return await captureBaselineScreenshot(page, screenshotPath, label);
}

async function captureRoute(page, hash, expectedText, screenshotPath, label) {
  await page.evaluate((nextHash) => {
    window.location.hash = nextHash;
  }, hash);
  await page.waitForFunction(
    (nextHash) => window.location.hash === nextHash,
    hash,
    { timeout: 5_000 }
  );
  await waitForRootText(page, expectedText, hash);
  return await captureBaselineScreenshot(page, screenshotPath, label);
}

async function captureLocalizedRoute(
  page,
  hash,
  expectedText,
  expectedDocumentLang,
  screenshotPath,
  label,
) {
  await page.evaluate((nextHash) => {
    window.location.hash = nextHash;
  }, hash);
  await page.waitForFunction(
    (nextHash) => window.location.hash === nextHash,
    hash,
    { timeout: 5_000 }
  );
  await waitForRootText(page, expectedText, `${hash}:${expectedDocumentLang}`);
  const documentLang = await page.evaluate(() => document.documentElement.lang);
  if (documentLang !== expectedDocumentLang) {
    throw new Error(
      `Dashboard route ${hash} has document language ${documentLang}, expected ${expectedDocumentLang}`
    );
  }
  await assertNoHorizontalOverflow(page, `${hash}:${expectedDocumentLang}`);
  return await captureBaselineScreenshot(page, screenshotPath, label);
}

async function selectIntelligenceTab(page, label, tabId, expectedText) {
  await page.getByRole("tab", { name: label }).click();
  await page.waitForFunction(
    (id) => document.getElementById(`intelligence-tab-${id}`)?.getAttribute("aria-selected") === "true",
    tabId,
    { timeout: 5_000 }
  );
  await page.waitForFunction(
    (nextHash) => window.location.hash === nextHash,
    "#/intelligence",
    { timeout: 5_000 }
  );
  await waitForRootText(page, expectedText, `#/intelligence:${tabId}`);
}

async function clickIntelligenceTab(page, label, tabId, expectedText, screenshotPath) {
  await selectIntelligenceTab(page, label, tabId, expectedText);
  return await captureBaselineScreenshot(page, screenshotPath, `Intelligence ${label}`);
}

async function runRecallTraceSmoke(page, screenshotPath) {
  await selectIntelligenceTab(
    page,
    "召回链路",
    "recallTrace",
    ["召回链路", "查询", "运行一次链路追踪，查看阶段耗时、排序证据、贡献项与过滤候选。"]
  );
  await page.getByPlaceholder("输入要追踪的查询...").fill("用户喜欢喝什么咖啡");
  await page.getByRole("button", { name: "追踪", exact: true }).click();
  await waitForRootText(
    page,
    ["trace-smoke-coffee", "记忆检索", "BM25", "#1", "0.930"],
    "#/intelligence:recallTrace"
  );
  return await captureBaselineScreenshot(page, screenshotPath, "Intelligence 召回链路");
}

async function clickMobileNav(page, label, expectedHash, expectedText, screenshotPath) {
  await page.getByRole("button", { name: "打开菜单" })
    .or(page.getByRole("button", { name: "Open menu" }))
    .or(page.getByRole("button", { name: "Открыть меню" }))
    .click();
  await page.getByRole("button", { name: label }).click();
  await page.waitForFunction(
    (nextHash) => window.location.hash === nextHash,
    expectedHash,
    { timeout: 5_000 }
  );
  await waitForRootText(page, expectedText, expectedHash);
  if (expectedHash === "#/preview") {
    await assertMobilePreviewLayout(page);
  }
  return await captureBaselineScreenshot(page, screenshotPath, `mobile-${label}`);
}

async function assertMobilePreviewLayout(page) {
  const slots = [
    "preview-metrics",
    "growth-panel",
    "composition-panel",
    "module-assets-panel",
    "active-sessions-panel",
  ];
  await page.locator('[data-slot="active-sessions-panel"]').waitFor({ state: "visible" });
  const positions = await Promise.all(slots.map(async (slot) => {
    const box = await page.locator(`[data-slot="${slot}"]`).boundingBox();
    return { slot, top: box?.y ?? Number.NaN };
  }));
  const overflow = await page.locator('[data-slot="page-content"]').last().evaluate(
    (content) => content.scrollWidth - content.clientWidth
  );
  const result = { positions, overflow };
  const validOrder = result.positions.every((item, index) => (
    Number.isFinite(item.top)
    && (index === 0 || item.top > result.positions[index - 1].top)
  ));
  if (!validOrder || !Number.isFinite(result.overflow) || result.overflow > 1) {
    throw new Error(`Mobile preview layout is invalid: ${JSON.stringify(result)}`);
  }
}

function assertNoPostCall(postCalls, endpoint) {
  if (postCalls.includes(endpoint)) {
    throw new Error(`High-impact action ${endpoint} was called before confirmation`);
  }
}

async function clickButtonByAnyName(page, labels) {
  const attempts = [];
  for (const label of labels) {
    try {
      await page.getByRole("button", { name: label }).first().click({ timeout: 1_000 });
      return label;
    } catch (error) {
      attempts.push(`${label}: ${error?.message ?? String(error)}`);
    }
  }
  throw new Error(`Could not click any button label:\n${attempts.join("\n")}`);
}

async function waitForTextByAny(page, texts, options = {}) {
  const attempts = [];
  for (const text of texts) {
    try {
      await page.getByText(text).first().waitFor({ timeout: 1_000, ...options });
      return text;
    } catch (error) {
      attempts.push(`${text}: ${error?.message ?? String(error)}`);
    }
  }
  throw new Error(`Could not find any expected text:\n${attempts.join("\n")}`);
}

function backupRow(page, backupName) {
  return page
    .getByText(backupName)
    .locator("xpath=ancestor::div[contains(@class, 'justify-between')][1]");
}

function confirmationBar(page, confirmText) {
  return page
    .getByText(confirmText)
    .first()
    .locator("xpath=ancestor::*[@role='dialog'][1]");
}

async function assertConfirmationCancelsWithoutPost(
  page,
  trigger,
  confirmTexts,
  endpoint,
  screenshotPath = null,
) {
  await trigger();
  const confirmText = await waitForTextByAny(page, confirmTexts, { timeout: 5_000 });
  const bar = confirmationBar(page, confirmText);
  let screenshotResult = null;

  if (screenshotPath) {
    await bar.scrollIntoViewIfNeeded();
    const confirmationIsInViewport = await page.getByText(confirmText).first().evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return (
        rect.width > 0
        && rect.height > 0
        && rect.top < window.innerHeight
        && rect.bottom > 0
        && rect.left < window.innerWidth
        && rect.right > 0
      );
    });
    if (!confirmationIsInViewport) {
      throw new Error(`Confirmation is outside the viewport: ${confirmText}`);
    }
    await waitForVisualStability(page);
    const screenshot = await page.screenshot({ path: screenshotPath, fullPage: false });
    screenshotResult = assertScreenshotMatchesBaseline(
      screenshot,
      path.basename(screenshotPath),
      "system-confirmation",
    );
  }

  assertNoPostCall(await getPostCalls(page), endpoint);
  await clickButtonByAnyName(bar, ["Cancel", "取消", "Отмена"]);
  await page.getByText(confirmText).first().waitFor({
    state: "detached",
    timeout: 5_000,
  });
  assertNoPostCall(await getPostCalls(page), endpoint);
  return screenshotResult;
}

async function assertBackupDestructiveConfirmations(page, screenshotPath) {
  await page.getByText("backup-smoke-a").waitFor({ timeout: 5_000 });
  await page.getByText("backup-smoke-b").waitFor({ timeout: 5_000 });

  const screenshotResult = await assertConfirmationCancelsWithoutPost(
    page,
    async () => {
      await clickButtonByAnyName(backupRow(page, "backup-smoke-a"), [
        "Restore Backup",
        "恢复备份",
        "Восстановить",
      ]);
    },
    [
      "Restore data from backup-smoke-a? This will overwrite current data.",
      "确定要从 backup-smoke-a 恢复数据吗？这将覆盖当前数据。",
      "Восстановить данные из backup-smoke-a? Текущие данные будут перезаписаны.",
    ],
    "backup/restore",
    screenshotPath,
  );

  await assertConfirmationCancelsWithoutPost(
    page,
    async () => {
      await backupRow(page, "backup-smoke-a").getByRole("button").nth(1).click();
    },
    [
      "Delete backup backup-smoke-a? This cannot be undone.",
      "确定要删除备份 backup-smoke-a 吗？此操作不可撤销。",
      "Удалить резервную копию backup-smoke-a? Это действие необратимо.",
    ],
    "backup/delete"
  );

  await clickButtonByAnyName(page, ["Select all", "全选", "Все"]);
  await assertConfirmationCancelsWithoutPost(
    page,
    async () => {
      await clickButtonByAnyName(page, ["Delete Selected (2)", "删除选中 (2)", "Удалить (2)"]);
    },
    [
      "Delete 2 backups? This cannot be undone.",
      "确定要删除 2 个备份吗？此操作不可撤销。",
      "Удалить 2 резервных копий? Это действие необратимо.",
    ],
    "backup/batch-delete"
  );

  if (!screenshotResult) {
    throw new Error("System confirmation screenshot was not captured");
  }
  return screenshotResult;
}

async function assertHighImpactConfirmation(page) {
  const actions = [
    {
      endpoint: "dashboard/install",
      buttonLabels: ["Install Dependencies", "安装依赖", "Установить зависимости"],
      confirmTexts: [
        "Install Dashboard dependencies now?",
        "现在安装 Dashboard 依赖吗？",
        "Установить зависимости Dashboard сейчас?",
      ],
    },
    {
      endpoint: "dashboard/build",
      buttonLabels: ["Build Page", "构建页面", "Собрать страницу"],
      confirmTexts: [
        "Build Dashboard production assets now?",
        "现在构建 Dashboard 生产产物吗？",
        "Собрать production-ресурсы Dashboard сейчас?",
      ],
    },
  ];

  for (const action of actions) {
    await assertConfirmationCancelsWithoutPost(
      page,
      async () => clickButtonByAnyName(page, action.buttonLabels),
      action.confirmTexts,
      action.endpoint
    );
  }
}

async function installBundledMockBridge(page) {
  const content = `{
    const BRIDGE_CALL_SENSITIVE_FIELDS = ${JSON.stringify(BRIDGE_CALL_SENSITIVE_FIELDS)};
    const instrumentBrowserBridge = ${instrumentBrowserBridge.toString()};
    const installBundledMockBridgeHarness = ${installBundledMockBridgeHarness.toString()};
    window.__memoraLoseNextStaleApplyResponse = true;
    installBundledMockBridgeHarness(window, (sourceBridge, options) =>
      instrumentBrowserBridge(sourceBridge, {
        ...options,
        afterPost({ endpoint, response }) {
          if (
            !window.__memoraLoseNextStaleApplyResponse
            || endpoint !== "page/config/apply"
            || response?.code !== "config_conflict"
          ) return;
          window.__memoraLoseNextStaleApplyResponse = false;
          throw new Error("Browser smoke lost the stale apply response");
        },
      })
    );
  }`;
  await page.addInitScript({ content });
}

async function openBundledConfigPage(
  browser,
  viewport,
  errors,
  initialHash = "#/config",
  expectedText = null,
) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  collectPageErrors(page, errors);
  await installBundledMockBridge(page);
  await page.goto(`${pathToFileURL(htmlPath).href}${initialHash}`, { waitUntil: "load" });
  await page.bringToFront();
  await page.waitForSelector("#root > *", { timeout: 10_000 });
  if (expectedText) await waitForRootText(page, expectedText, initialHash);
  return { context, page };
}

async function waitForConfigReady(page, label) {
  await waitForRootText(
    page,
    ["配置", "单次召回数量", "recall_engine.top_k", "已同步"],
    label,
  );
  await page.waitForFunction(
    ({ sections, fields }) =>
      document.querySelectorAll("[data-config-section]").length === sections
      && document.querySelectorAll('[data-slot="page-frame"] [data-slot="field"]').length === fields,
    { sections: 43, fields: 234 },
    { timeout: 10_000 },
  );
  const counts = await page.evaluate(() => ({
    sections: document.querySelectorAll("[data-config-section]").length,
    fields: document.querySelectorAll('[data-slot="page-frame"] [data-slot="field"]').length,
    text: document.querySelector("#root")?.innerText ?? "",
  }));
  const lingeringLoading = [
    "正在加载配置",
    "加载中...",
    "Loading configuration",
    "Загрузка конфигурации",
  ].filter((text) => counts.text.includes(text));
  if (counts.sections !== 43 || counts.fields !== 234 || lingeringLoading.length > 0) {
    throw new Error(
      `${label} did not render the complete settled schema: ${JSON.stringify({
        sections: counts.sections,
        fields: counts.fields,
        lingeringLoading,
      })}`,
    );
  }
  return counts;
}

function configNumberInput(page, pathName) {
  return page
    .locator("code")
    .filter({ hasText: pathName })
    .first()
    .locator("xpath=ancestor::*[@data-slot='field'][1]")
    .getByRole("spinbutton");
}

async function getBrowserBridgeCalls(page) {
  return await page.evaluate(() =>
    JSON.parse(JSON.stringify(window.__memoraBridgeCalls ?? []))
  );
}

async function waitForInjectionSettled(page, label) {
  try {
    await page.waitForFunction(
      (loadingTexts) => {
        const root = document.querySelector("#root");
        const text = root?.innerText ?? "";
        return loadingTexts.every((item) => !text.includes(item))
          && !root?.querySelector('[data-slot="skeleton"]');
      },
      ROUTE_LOADING_TEXT,
      { timeout: 10_000 },
    );
  } catch (error) {
    const state = await page.evaluate((loadingTexts) => {
      const root = document.querySelector("#root");
      const text = root?.innerText ?? "";
      return {
        loadingText: loadingTexts.filter((item) => text.includes(item)),
        skeletons: [...(root?.querySelectorAll('[data-slot="skeleton"]') ?? [])]
          .map((element) => element.getAttribute("class")),
        calls: (window.__memoraBridgeCalls ?? []).map((call) => ({
          endpoint: call.endpoint,
          method: call.method,
        })),
      };
    }, ROUTE_LOADING_TEXT);
    throw new Error(`${label} did not settle: ${JSON.stringify(state)}`, { cause: error });
  }
  const rootText = await page.locator("#root").innerText();
  const lingering = ROUTE_LOADING_TEXT.filter((item) => rootText.includes(item));
  if (lingering.length > 0) {
    throw new Error(`${label} retained a localized loading overlay`);
  }
}

async function runInjectionStrategySmoke(page, screenshotsDir) {
  const screenshots = [];
  await navigateSidebar(
    page,
    "注入策略",
    "#/injection",
    ["注入策略", "概览", "策略配置", "决策记录"],
  );
  await waitForInjectionSettled(page, "#/injection:overview");
  await assertNoHorizontalOverflow(page, "#/injection:overview");
  screenshots.push(await captureBaselineScreenshot(
    page,
    path.join(screenshotsDir, "injection-overview.png"),
    "injection-overview",
  ));

  await page.getByRole("tab", { name: "策略配置", exact: true }).click();
  await page.getByRole("tab", { name: "混合切换", exact: true }).click();
  await page.getByRole("tab", { name: "决策记录", exact: true }).click();
  const unsaved = page.getByRole("dialog", { name: "要离开配置页吗？", exact: true });
  await unsaved.waitFor({ state: "visible", timeout: 5_000 });
  await unsaved.getByRole("button", { name: "继续编辑", exact: true }).click();

  await page.getByRole("button", { name: "系统概览", exact: true }).click();
  await unsaved.waitFor({ state: "visible", timeout: 5_000 });
  await unsaved.getByRole("button", { name: "继续编辑", exact: true }).click();
  if (await page.evaluate(() => window.location.hash) !== "#/injection") {
    throw new Error("Cross-route dirty confirmation did not keep the injection workbench open");
  }

  await page.getByRole("tab", { name: "决策记录", exact: true }).click();
  await unsaved.waitFor({ state: "visible", timeout: 5_000 });
  await unsaved.getByRole("button", { name: "放弃更改并离开", exact: true }).click();

  await page.getByRole("textbox", { name: "Provider 类型", exact: true }).fill("openai");
  await page.waitForFunction(
    () => (window.__memoraBridgeCalls ?? []).some((call) =>
      call.endpoint === "page/injection-strategy/decisions"
      && call.params?.provider_type === "openai"
      && call.params?.offset === "0"),
    undefined,
    { timeout: 5_000 },
  );
  const pagination = page.getByRole("navigation", { name: "决策记录分页", exact: true });
  try {
    await pagination.waitFor({ state: "visible", timeout: 5_000 });
  } catch (error) {
    const diagnostics = await page.evaluate(() => ({
      rootText: document.querySelector("#root")?.innerText?.slice(-1600) ?? "",
      calls: (window.__memoraBridgeCalls ?? []).filter((call) => call.endpoint === "page/injection-strategy/decisions").map((call) => ({ params: call.params, total: call.response?.data?.total, itemCount: call.response?.data?.items?.length })),
      navs: [...document.querySelectorAll("nav")].map((element) => ({ aria: element.getAttribute("aria-label"), text: element.textContent })),
    }));
    throw new Error(`Decision pagination did not render: ${JSON.stringify(diagnostics)}`, { cause: error });
  }
  await pagination.getByRole("button", { name: "下一页", exact: true }).click();
  await page.waitForFunction(
    () => (window.__memoraBridgeCalls ?? []).some((call) =>
      call.endpoint === "page/injection-strategy/decisions"
      && call.params?.provider_type === "openai"
      && Number(call.params?.offset) > 0),
    undefined,
    { timeout: 5_000 },
  );

  const detailTrigger = page.getByRole("button", { name: "查看决策详情", exact: true }).nth(2);
  await detailTrigger.click();
  await page.getByRole("menuitem", { name: "查看决策详情", exact: true }).click();
  const detailSheet = page.getByRole("dialog", { name: "注入决策详情", exact: true });
  await detailSheet.waitFor({ state: "visible", timeout: 5_000 });
  await detailSheet.getByRole("heading", { name: "阶段耗时", exact: true })
    .waitFor({ state: "visible", timeout: 5_000 });
  screenshots.push(await captureBaselineScreenshot(
    page,
    path.join(screenshotsDir, "injection-decisions.png"),
    "injection-decisions",
  ));
  await assertNoHorizontalOverflow(page, "#/injection:decisions");

  await detailSheet.getByRole("button", { name: "关闭", exact: true }).click();
  await detailSheet.waitFor({ state: "hidden", timeout: 5_000 });
  const decisionsTable = page.locator('[data-table-id="injection-decisions"]');
  const readDecisionDensityMetrics = () => decisionsTable.evaluate((root) => {
    const header = root.querySelector("thead th");
    const cell = root.querySelector("tbody td");
    if (!(header instanceof HTMLElement) || !(cell instanceof HTMLElement)) {
      throw new Error("Decision table density cells are unavailable");
    }
    return {
      headerHeight: header.getBoundingClientRect().height,
      cellPaddingTop: Number.parseFloat(getComputedStyle(cell).paddingTop),
    };
  });
  const standardDensityMetrics = await readDecisionDensityMetrics();
  await decisionsTable.getByRole("button", { name: "表格视图", exact: true }).click();
  const decisionsMenu = page.getByRole("menu");
  await decisionsMenu.getByRole("menuitemradio", { name: "紧凑", exact: true }).click();
  await page.keyboard.press("Escape");
  await page.waitForFunction(
    () => document.querySelector('[data-table-id="injection-decisions"] table')?.getAttribute("data-density") === "compact",
    undefined,
    { timeout: 5_000 },
  );
  const compactDensityMetrics = await readDecisionDensityMetrics();
  if (compactDensityMetrics.headerHeight >= standardDensityMetrics.headerHeight
    || compactDensityMetrics.cellPaddingTop >= standardDensityMetrics.cellPaddingTop) {
    throw new Error(`Compact density did not reduce spacing: ${JSON.stringify({ standardDensityMetrics, compactDensityMetrics })}`);
  }
  await decisionsTable.getByRole("button", { name: "表格视图", exact: true }).click();
  await page.getByRole("menu").getByRole("menuitemradio", { name: "宽松", exact: true }).click();
  await page.keyboard.press("Escape");
  await page.waitForFunction(
    () => document.querySelector('[data-table-id="injection-decisions"] table')?.getAttribute("data-density") === "comfortable",
    undefined,
    { timeout: 5_000 },
  );
  const comfortableDensityMetrics = await readDecisionDensityMetrics();
  if (comfortableDensityMetrics.headerHeight <= standardDensityMetrics.headerHeight
    || comfortableDensityMetrics.cellPaddingTop <= standardDensityMetrics.cellPaddingTop) {
    throw new Error(`Comfortable density did not increase spacing: ${JSON.stringify({ standardDensityMetrics, comfortableDensityMetrics })}`);
  }
  await decisionsTable.getByRole("button", { name: "表格视图", exact: true }).click();
  await page.getByRole("menu").getByRole("menuitemradio", { name: "紧凑", exact: true }).click();
  await page.keyboard.press("Escape");
  await page.waitForFunction(
    () => document.querySelector('[data-table-id="injection-decisions"] table')?.getAttribute("data-density") === "compact",
    undefined,
    { timeout: 5_000 },
  );
  screenshots.push(await captureBaselineScreenshot(
    page,
    path.join(screenshotsDir, "injection-decisions-compact.png"),
    "injection-decisions-compact",
  ));

  await page.getByRole("button", { name: "查看决策详情", exact: true }).nth(2).click();
  await page.getByRole("menuitem", { name: "查看决策详情", exact: true }).click();
  await detailSheet.waitFor({ state: "visible", timeout: 5_000 });

  await detailSheet.getByRole("button", { name: "打开召回追踪", exact: true }).click();
  await page.waitForFunction(
    () => window.location.hash === "#/intelligence",
    undefined,
    { timeout: 5_000 },
  );
  try {
    await page.waitForFunction(
      () => {
        const traceCalls = (window.__memoraBridgeCalls ?? []).filter((call) =>
          call.endpoint === "page/recall/trace/detail");
        return document.getElementById("intelligence-tab-recallTrace")
          ?.getAttribute("aria-selected") === "true"
          && traceCalls.length >= 1;
      },
      undefined,
      { timeout: 5_000 },
    );
  } catch (error) {
    const traceState = await page.evaluate(() => ({
      hash: window.location.hash,
      selectedTab: document.querySelector('[role="tab"][aria-selected="true"]')?.id ?? null,
      traceCallCount: (window.__memoraBridgeCalls ?? []).filter((call) =>
        call.endpoint === "page/recall/trace/detail").length,
    }));
    throw new Error(`Injection Trace navigation did not settle: ${JSON.stringify(traceState)}`, {
      cause: error,
    });
  }
  const traceCalls = await page.evaluate(() => (
    (window.__memoraBridgeCalls ?? []).filter((call) =>
      call.endpoint === "page/recall/trace/detail")
  ));
  if (traceCalls.length !== 1) {
    throw new Error(
      `Injection Trace navigation issued ${traceCalls.length} detail calls; expected 1`,
    );
  }
  await waitForRootText(
    page,
    ["召回链路", "trace-mock-coffee"],
    "#/intelligence:injection-trace",
  );

  await navigateSidebar(
    page,
    "注入策略",
    "#/injection",
    ["注入策略", "概览", "策略配置", "决策记录"],
  );
  await page.getByRole("tab", { name: "策略配置", exact: true }).click();
  await page.getByRole("tab", { name: "混合切换", exact: true }).click();

  const initialCalls = await getBrowserBridgeCalls(page);
  const initialState = [...initialCalls].reverse().find(
    (call) => call.method === "GET"
      && call.endpoint === "page/config/state"
      && call.response?.data?.revision,
  );
  const initialRevision = initialState?.response?.data?.revision;
  if (!initialRevision) {
    throw new Error("Injection strategy conflict smoke did not capture its initial revision");
  }
  const seeded = await page.evaluate(
    async ({ revision }) => window.__memoraRawBridge.apiPost("page/config/apply", {
      base_revision: revision,
      changes: { "recall_engine.injection_routing_mode": "auto" },
    }),
    { revision: initialRevision },
  );
  if (seeded?.status !== "ok") {
    throw new Error("Injection strategy conflict smoke could not seed a remote revision");
  }
  await new Promise((resolve) => setTimeout(resolve, 850));

  await page.getByRole("button", { name: "保存策略", exact: true }).click();
  const conflictDialog = page.getByRole("dialog", {
    name: "AstrBot 中的配置已更改",
    exact: true,
  });
  await conflictDialog.waitFor({ state: "visible", timeout: 5_000 });
  const conflictPaths = await conflictDialog.locator("code").evaluateAll(
    (nodes) => nodes.map((node) => node.textContent?.trim()),
  );
  if (
    conflictPaths.length !== 3
    || conflictPaths.some((value) => value !== "recall_engine.injection_routing_mode")
  ) {
    throw new Error("ConfigConflictDialog did not report the injection revision conflict");
  }
  screenshots.push(await captureBaselineScreenshot(
    page,
    path.join(screenshotsDir, "injection-config-conflict.png"),
    "injection-config-conflict",
  ));

  await conflictDialog
    .getByRole("button", { name: "在最新版本上重新应用我的更改", exact: true })
    .click();
  await conflictDialog.waitFor({ state: "detached", timeout: 5_000 });
  const rebasedHybridTab = page.getByRole("tab", { name: "混合切换", exact: true });
  if (await rebasedHybridTab.getAttribute("aria-selected") !== "true") {
    throw new Error("Injection strategy conflict rebase lost the local hybrid draft");
  }
  if (await page.getByRole("button", { name: "保存策略", exact: true }).isDisabled()) {
    throw new Error("Injection strategy conflict rebase did not preserve a saveable draft");
  }
  const callsAfterRebase = await getBrowserBridgeCalls(page);
  if (callsAfterRebase.some((call) =>
    call.method === "POST"
    && call.endpoint === "page/config/apply"
    && call.response?.status === "ok")) {
    throw new Error("Injection strategy conflict rebase posted automatically");
  }

  await page.getByRole("button", { name: "保存策略", exact: true }).click();
  await page.waitForFunction(
    () => (window.__memoraBridgeCalls ?? []).some((call) =>
      call.method === "POST"
      && call.endpoint === "page/config/apply"
      && call.response?.status === "ok"),
    undefined,
    { timeout: 5_000 },
  );
  const finalCalls = await getBrowserBridgeCalls(page);
  const successfulApply = [...finalCalls].reverse().find((call) =>
    call.method === "POST"
    && call.endpoint === "page/config/apply"
    && call.response?.status === "ok");
  const bodyKeys = Object.keys(successfulApply?.body ?? {}).sort();
  const changedPaths = Object.keys(successfulApply?.body?.changes ?? {});
  if (
    bodyKeys.join(",") !== "base_revision,changes"
    || changedPaths.length === 0
    || changedPaths.some((value) => !value.startsWith("recall_engine.injection_"))
    || changedPaths.some((value) => value.includes("injection_method"))
  ) {
    throw new Error("Injection strategy apply payload crossed its configuration boundary");
  }
  return screenshots;
}

async function runMobileInjectionStrategySmoke(page, screenshotsDir) {
  await page.getByRole("button", { name: "打开菜单" }).click();
  await page.getByRole("button", { name: "注入策略", exact: true }).click();
  await page.waitForFunction(
    () => window.location.hash === "#/injection",
    undefined,
    { timeout: 5_000 },
  );
  await waitForRootText(page, ["注入策略", "概览", "策略配置", "决策记录"], "#/injection:mobile");
  await page.getByRole("tab", { name: "决策记录", exact: true }).click();
  const detailTrigger = page.getByRole("button", { name: "查看决策详情", exact: true }).first();
  await detailTrigger.waitFor({ state: "visible", timeout: 5_000 });
  await detailTrigger.click();
  await page.getByRole("menuitem", { name: "查看决策详情", exact: true }).click();
  const sheet = page.getByRole("dialog", { name: "注入决策详情", exact: true });
  await sheet.waitFor({ state: "visible", timeout: 5_000 });
  const box = await sheet.boundingBox();
  const viewportWidth = await page.evaluate(
    () => window.visualViewport?.width ?? window.innerWidth,
  );
  const pixelTolerance = 0.5;
  if (
    !box
    || box.x < -pixelTolerance
    || box.x + box.width > viewportWidth + pixelTolerance
    || box.width < 360
  ) {
    throw new Error(
      `Mobile injection detail is not full width: ${JSON.stringify({ box, viewportWidth })}`,
    );
  }
  const header = sheet.locator('[data-slot="sheet-header"]');
  const body = sheet.locator('[data-slot="injection-decision-body"]');
  const footer = sheet.locator('[data-slot="sheet-footer"]');
  await Promise.all([
    header.waitFor({ state: "visible", timeout: 5_000 }),
    body.waitFor({ state: "visible", timeout: 5_000 }),
    footer.waitFor({ state: "visible", timeout: 5_000 }),
  ]);
  await page.waitForFunction(
    () => {
      const element = document.querySelector('[data-slot="injection-decision-body"]');
      return element && element.scrollHeight > element.clientHeight;
    },
    undefined,
    { timeout: 5_000 },
  );
  const before = await body.evaluate((element) => ({
    scrollTop: element.scrollTop,
    scrollHeight: element.scrollHeight,
    clientHeight: element.clientHeight,
  }));
  await body.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await page.getByText("注入耗时", { exact: true }).last().waitFor({
    state: "visible",
    timeout: 5_000,
  });
  const after = await body.evaluate((element) => element.scrollTop);
  if (before.scrollHeight <= before.clientHeight || after <= before.scrollTop) {
    const scrollState = await body.evaluate((element) => ({
      overflowY: getComputedStyle(element).overflowY,
      scrollTop: element.scrollTop,
      scrollHeight: element.scrollHeight,
      clientHeight: element.clientHeight,
    }));
    throw new Error(`Mobile injection detail Sheet did not scroll to its final timing section: ${JSON.stringify({
      before,
      after,
      scrollState,
    })}`);
  }
  const viewport = page.viewportSize();
  const [headerBox, bodyBox, footerBox] = await Promise.all([
    header.boundingBox(),
    body.boundingBox(),
    footer.boundingBox(),
  ]);
  if (
    !viewport
    || !headerBox
    || !bodyBox
    || !footerBox
    || headerBox.y < 0
    || footerBox.y + footerBox.height > viewport.height
    || bodyBox.y < headerBox.y + headerBox.height - 1
    || bodyBox.y + bodyBox.height > footerBox.y + 1
  ) {
    throw new Error(`Mobile injection Sheet regions are clipped: ${JSON.stringify({
      viewport,
      headerBox,
      bodyBox,
      footerBox,
    })}`);
  }
  await assertNoHorizontalOverflow(page, "#/injection:mobile-detail");
  const result = await captureBaselineScreenshot(
    page,
    path.join(screenshotsDir, "mobile-injection-detail.png"),
    "mobile-injection-detail",
  );
  await sheet.getByRole("button", { name: "关闭", exact: true }).click();
  await sheet.waitFor({ state: "detached", timeout: 5_000 });
  await page.waitForFunction(
    (element) => document.activeElement === element,
    await detailTrigger.elementHandle(),
    { timeout: 5_000 },
  );
  return result;
}

async function runWideInjectionStrategySmoke(page, screenshotsDir) {
  await page.evaluate(() => {
    window.location.hash = "#/injection";
  });
  await waitForRootText(
    page,
    ["注入策略", "概览", "策略配置", "决策记录"],
    "#/injection:wide-overview",
  );
  await waitForInjectionSettled(page, "#/injection:wide-overview");

  const overviewContent = page.locator(
    '#injection-panel-overview [data-slot="page-content"]',
  );
  const overviewBox = await overviewContent.boundingBox();
  const viewport = page.viewportSize();
  if (
    !viewport
    || !overviewBox
    || overviewBox.width > 1441
    || Math.abs(
      (overviewBox.x + overviewBox.width / 2) - viewport.width / 2,
    ) > 256
  ) {
    throw new Error(`Wide Injection Overview is not constrained: ${JSON.stringify({
      viewport,
      overviewBox,
    })}`);
  }

  const screenshot = await captureBaselineScreenshot(
    page,
    path.join(screenshotsDir, "wide-injection-overview.png"),
    "wide-injection-overview",
  );

  await page.getByRole("tab", { name: "决策记录", exact: true }).click();
  const decisionsContent = page.locator(
    '#injection-panel-decisions [data-slot="page-content"]',
  );
  const decisionsBox = await decisionsContent.boundingBox();
  if (!decisionsBox || decisionsBox.width <= overviewBox.width) {
    throw new Error(`Wide Decision History did not use full width: ${JSON.stringify({
      overviewBox,
      decisionsBox,
    })}`);
  }
  await assertNoHorizontalOverflow(page, "#/injection:wide-decisions");

  await page.getByRole("tab", { name: "概览", exact: true }).click();
  return screenshot;
}

async function runDesktopConfigSmoke(browser, errors, screenshotsDir) {
  const { context, page } = await openBundledConfigPage(
    browser,
    { width: 1366, height: 900 },
    errors,
  );
  try {
    await waitForConfigReady(page, "#/config:desktop");
    await assertNoHorizontalOverflow(page, "#/config:desktop");
    const screenshots = [
      await captureBaselineScreenshot(
        page,
        path.join(screenshotsDir, "config.png"),
        "config",
      ),
    ];

    const topKInput = configNumberInput(page, "recall_engine.top_k");
    await topKInput.fill("9");
    await page.waitForFunction(
      () => document.querySelector("#root")?.innerText.includes("有未保存更改"),
      undefined,
      { timeout: 5_000 },
    );

    const initialCalls = await getBrowserBridgeCalls(page);
    const initialState = initialCalls.find(
      (call) =>
        call.method === "GET"
        && call.endpoint === "page/config/state"
        && call.response?.data?.changed === true,
    );
    const initialRevision = initialState?.response?.data?.revision;
    if (!initialRevision) {
      throw new Error("Browser config smoke did not capture its initial revision");
    }
    const seeded = await page.evaluate(
      async ({ revision }) =>
        window.__memoraRawBridge.apiPost("page/config/apply", {
          base_revision: revision,
          changes: { "recall_engine.top_k": 8 },
        }),
      { revision: initialRevision },
    );
    if (seeded?.status !== "ok") {
      throw new Error(`Browser config smoke could not seed the remote revision: ${JSON.stringify(seeded)}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 850));

    await page.getByRole("button", { name: "应用配置", exact: true }).click();
    const conflictDialog = page.getByRole("dialog", {
      name: "AstrBot 中的配置已更改",
      exact: true,
    });
    await conflictDialog.waitFor({ state: "visible", timeout: 5_000 });
    await conflictDialog
      .getByRole("button", { name: "在最新版本上重新应用我的更改", exact: true })
      .waitFor({ state: "visible", timeout: 5_000 });
    for (const label of ["我的本地更改", "AstrBot 远端更改", "重叠更改"]) {
      await conflictDialog.getByText(label, { exact: true }).waitFor({ timeout: 5_000 });
    }
    const conflictPaths = await conflictDialog.locator("code").evaluateAll(
      (nodes) => nodes.map((node) => node.textContent?.trim()),
    );
    if (
      conflictPaths.length !== 3
      || conflictPaths.some((path) => path !== "recall_engine.top_k")
    ) {
      throw new Error(`Browser config conflict paths are incomplete: ${JSON.stringify(conflictPaths)}`);
    }
    screenshots.push(
      await captureBaselineScreenshot(
        page,
        path.join(screenshotsDir, "config-conflict.png"),
        "config-conflict",
      ),
    );

    await conflictDialog
      .getByRole("button", { name: "在最新版本上重新应用我的更改", exact: true })
      .click();
    await conflictDialog.waitFor({ state: "detached", timeout: 5_000 });
    await page.waitForFunction(
      () => document.querySelector("#root")?.innerText.includes("有未保存更改"),
      undefined,
      { timeout: 5_000 },
    );
    if ((await topKInput.inputValue()) !== "9") {
      throw new Error(`Browser config rebase lost the local top_k draft: ${await topKInput.inputValue()}`);
    }
    const callsAfterRebase = await getBrowserBridgeCalls(page);
    const successfulAppliesAfterRebase = callsAfterRebase.filter(
      (call) =>
        call.method === "POST"
        && call.endpoint === "page/config/apply"
        && call.response?.status === "ok",
    );
    if (successfulAppliesAfterRebase.length !== 0) {
      throw new Error("Browser config rebase saved automatically instead of preserving a draft");
    }
    await page
      .getByRole("searchbox", { name: "搜索配置", exact: true })
      .fill("recall_engine.top_k");
    await page.waitForFunction(
      () =>
        document.querySelectorAll('[data-slot="page-frame"] [data-slot="field"]').length === 1
        && [...document.querySelectorAll("code")].some(
          (code) => code.textContent?.trim() === "recall_engine.top_k",
        ),
      undefined,
      { timeout: 5_000 },
    );

    await page.getByRole("button", { name: "应用配置", exact: true }).click();
    const successfulApplyHandle = await page.waitForFunction(
      () => {
        const text = document.querySelector("#root")?.innerText ?? "";
        const successfulApply = [...(window.__memoraBridgeCalls ?? [])].reverse().find(
          (call) =>
            call.method === "POST"
            && call.endpoint === "page/config/apply"
            && call.response?.status === "ok",
        );
        return text.includes("正在重载")
          ? successfulApply?.response?.data?.revision ?? false
          : false;
      },
      undefined,
      { timeout: 2_000 },
    );
    const appliedRevision = await successfulApplyHandle.jsonValue();
    await page.evaluate(async () => {
      window.dispatchEvent(new Event("focus"));
      await new Promise((resolve) => setTimeout(resolve, 100));
      window.dispatchEvent(new Event("focus"));
    });
    await page.waitForFunction(
      (revision) => (window.__memoraBridgeCalls ?? []).some(
        (call) =>
          call.method === "GET"
          && call.endpoint === "page/config/state"
          && call.params?.revision === revision
          && /Mock plugin is reloading/i.test(String(call.error ?? "")),
      ),
      appliedRevision,
      { timeout: 2_000 },
    );
    await page.waitForFunction(
      () => {
        const text = document.querySelector("#root")?.innerText ?? "";
        return text.includes("已同步")
          && (window.__memoraBridgeCalls ?? []).some(
            (call) =>
              call.method === "GET"
              && call.endpoint === "page/config/state"
              && call.response?.data?.changed === false,
          );
      },
      undefined,
      { timeout: 10_000 },
    );
    const trace = assertConfigRuntimeCalls(await getBrowserBridgeCalls(page), {
      changedPath: "recall_engine.top_k",
      changedValue: 9,
    });
    await waitForRootText(page, ["已同步", trace.finalInstanceId], "#/config:reloaded");
    await assertNoHorizontalOverflow(page, "#/config:reloaded");
    return { screenshots, trace };
  } finally {
    await context.close();
  }
}

async function runMobileConfigSmoke(browser, errors, screenshotsDir) {
  const { context, page } = await openBundledConfigPage(
    browser,
    { width: 390, height: 844 },
    errors,
  );
  try {
    await waitForConfigReady(page, "#/config:mobile");
    const groupSelect = page.getByRole("combobox", {
      name: "选择配置分组",
      exact: true,
    });
    await groupSelect.waitFor({ state: "visible", timeout: 5_000 });
    const triggerBox = await groupSelect.boundingBox();
    if (
      !triggerBox
      || triggerBox.x < 0
      || triggerBox.y < 0
      || triggerBox.x + triggerBox.width > 390
      || triggerBox.y + triggerBox.height > 844
    ) {
      throw new Error(`Mobile config group trigger is outside the viewport: ${JSON.stringify(triggerBox)}`);
    }
    const desktopGroupNav = page.locator('[data-slot="page-frame"] nav').first();
    if ((await desktopGroupNav.count()) !== 1 || await desktopGroupNav.isVisible()) {
      throw new Error("Mobile config rendered the desktop group navigation");
    }

    await groupSelect.click();
    await page.getByRole("option", { name: "记忆召回", exact: true }).click();
    await page.waitForFunction(
      () => {
        const focusedSection = document.activeElement
          ?.closest("[data-config-section]")
          ?.getAttribute("data-config-section");
        const section = document.querySelector('[data-config-section="recall_engine"]');
        const pageContents = document.querySelectorAll('[data-slot="page-content"]');
        const pageContent = pageContents[pageContents.length - 1];
        const rect = section?.getBoundingClientRect();
        const contentRect = pageContent?.getBoundingClientRect();
        return focusedSection === "recall_engine"
          && Boolean(rect)
          && Boolean(contentRect)
          && rect.top >= contentRect.top - 1
          && rect.top <= contentRect.top + 24
          && rect.bottom > contentRect.top;
      },
      undefined,
      { timeout: 5_000 },
    );
    const recallSection = page.locator('[data-config-section="recall_engine"]');
    const recallBox = await recallSection.boundingBox();
    if (!recallBox || recallBox.y >= 844 || recallBox.y + recallBox.height <= 0) {
      throw new Error(`Mobile config did not move the recall section into view: ${JSON.stringify(recallBox)}`);
    }
    await assertNoHorizontalOverflow(page, "#/config:mobile");
    return await captureBaselineScreenshot(
      page,
      path.join(screenshotsDir, "mobile-config.png"),
      "mobile-config",
    );
  } finally {
    await context.close();
  }
}

async function installBridge(page) {
  await page.addInitScript((sensitiveFields) => {
    let nextSubscriptionId = 1;
    const timers = new Map();
    const sensitiveFieldSet = new Set(sensitiveFields);
    const cloneJson = (value) => (
      value === undefined ? undefined : JSON.parse(JSON.stringify(value))
    );
    const sanitizeBridgeCallValue = (value) => {
      const scrub = (sanitized) => {
        if (Array.isArray(sanitized)) {
          sanitized.forEach(scrub);
          return sanitized;
        }
        if (!sanitized || typeof sanitized !== "object") return sanitized;
        sensitiveFieldSet.forEach((field) => delete sanitized[field]);
        Object.values(sanitized).forEach(scrub);
        return sanitized;
      };
      return scrub(cloneJson(value));
    };
    const sanitizeBridgeCallParams = (params) => (
      sanitizeBridgeCallValue(params ?? {})
    );
    window.__memoraBridgeCalls = [];
    window.__memoraPostCalls = [];
    window.AstrBotPluginPage = {
      async apiGet(endpoint, params) {
        const call = {
          method: "GET",
          endpoint: String(endpoint),
          params: sanitizeBridgeCallParams(params),
        };
        window.__memoraBridgeCalls.push(call);
        try {
          const payload = await window.__memoraBridgePayload(endpoint, params, "GET");
          const response = payload?.__memoraEditingResponse
            ?? window.__memoraBridgeOk(payload);
          call.response = sanitizeBridgeCallValue(response);
          return response;
        } catch (error) {
          call.error = error instanceof Error ? error.message : String(error);
          throw error;
        }
      },
      async apiPost(endpoint, body) {
        const call = {
          method: "POST",
          endpoint: String(endpoint),
          body: sanitizeBridgeCallValue(body ?? {}),
        };
        window.__memoraBridgeCalls.push(call);
        window.__memoraPostCalls.push(String(endpoint || "").replace(/^page\/?/, ""));
        try {
          const snapshot = body === undefined ? {} : JSON.parse(JSON.stringify(body));
          const payload = await window.__memoraBridgePayload(endpoint, snapshot, "POST");
          const response = payload?.__memoraEditingResponse
            ?? window.__memoraBridgeOk(payload);
          call.response = sanitizeBridgeCallValue(response);
          return response;
        } catch (error) {
          call.error = error instanceof Error ? error.message : String(error);
          throw error;
        }
      },
      subscribeSSE(_endpoint, handlers) {
        const id = `browser-smoke-${nextSubscriptionId++}`;
        const eventTimer = window.setTimeout(() => {
          handlers?.onMessage?.(JSON.stringify({
            event: "memory_created",
            data: { text: "browser smoke event" },
            ts: Date.now() / 1000,
          }));
        }, 100);
        const errorTimer = window.setTimeout(() => {
          handlers?.onError?.(new Error("browser smoke forced reconnect"));
        }, 250);
        timers.set(id, [eventTimer, errorTimer]);
        return id;
      },
      unsubscribeSSE(id) {
        for (const timer of timers.get(id) || []) window.clearTimeout(timer);
        timers.delete(id);
      },
    };
  }, BRIDGE_CALL_SENSITIVE_FIELDS);
  await page.exposeFunction("__memoraBridgePayload", bridgePayload);
  await page.exposeFunction("__memoraBridgeOk", ok);
}

async function getPostCalls(page) {
  return await page.evaluate(() => [...(window.__memoraPostCalls || [])]);
}

function collectPageErrors(page, errors) {
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    errors.push(`console.error: ${text}`);
  });
  page.on("pageerror", (error) => {
    errors.push(`pageerror: ${error.message}`);
  });
}

const { browser, label } = await launchBrowser();
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
const errors = [];

collectPageErrors(page, errors);
await installBridge(page);

try {
  await page.goto(pathToFileURL(htmlPath).href, { waitUntil: "load" });
  await page.bringToFront();
  await page.waitForSelector("#root > *", { timeout: 10_000 });
  const screenshotsDir = path.join(os.tmpdir(), "memora-dashboard-browser-smoke-screenshots");
  await mkdir(screenshotsDir, { recursive: true });
  const baselineResults = [];

  const desktopConfigResult = await runDesktopConfigSmoke(
    browser,
    errors,
    screenshotsDir,
  );
  baselineResults.push(...desktopConfigResult.screenshots);
  baselineResults.push(
    await runMobileConfigSmoke(browser, errors, screenshotsDir),
  );
  const desktopInjection = await openBundledConfigPage(
    browser,
    { width: 1366, height: 900 },
    errors,
    "#/injection",
    ["注入策略", "概览", "策略配置", "决策记录"],
  );
  try {
    baselineResults.push(
      ...await runInjectionStrategySmoke(desktopInjection.page, screenshotsDir),
    );
  } finally {
    await desktopInjection.context.close();
  }
  baselineResults.push(
    ...await runGlobalSearchScrollAndTargetSmoke(page, screenshotsDir),
  );
  baselineResults.push(
    ...await runKnowledgeTableSmoke(page, browser, errors, screenshotsDir),
  );

  const routes = [
    ["数据预览", "#/preview", ["数据预览", "记忆增长", "模块资产", "活跃会话"], "preview.png"],
    ["知识图谱", "#/graph", "知识图谱", "graph.png"],
    ["记忆管理", "#/memory", "记忆管理", "memory.png"],
    ["系统概览", "#/system", ["系统概览", "运行观测", "Provider 状态"], "system.png"],
    ["黑话发现", "#/jargon", "黑话", "jargon.png"],
  ];

  for (const [navLabel, hash, expectedText, filename] of routes) {
    baselineResults.push(
      await clickSidebarNav(
        page,
        navLabel,
        hash,
        expectedText,
        path.join(screenshotsDir, filename)
      )
    );
  }

  baselineResults.push(
    await clickSidebarNav(
      page,
      "智能控制",
      "#/intelligence",
      ["智能控制台", "页面壳已就绪", "评测工作台", "private_basic", "group_context", "eval-smoke-latest"],
      path.join(screenshotsDir, "intelligence-evaluation.png")
    )
  );

  baselineResults.push(
    await runRecallTraceSmoke(
      page,
      path.join(screenshotsDir, "intelligence-trace.png")
    )
  );

  const intelligenceTabs = [
    [
      "诊断",
      "diagnostics",
      ["诊断", "82", "Index drift detected", "Index validator recommends a rebuild."],
      "intelligence-diagnostics.png",
    ],
    [
      "复核队列",
      "reviewQueue",
      ["复核队列", "mem-smoke-duplicate", "duplicate", "重复记忆"],
      "intelligence-review.png",
    ],
  ];

  for (const [tabLabel, tabId, expectedText, filename] of intelligenceTabs) {
    baselineResults.push(
      await clickIntelligenceTab(
        page,
        tabLabel,
        tabId,
        expectedText,
        path.join(screenshotsDir, filename)
      )
    );
  }

  const mobilePage = await browser.newPage({ viewport: { width: 390, height: 844 } });
  collectPageErrors(mobilePage, errors);
  await installBridge(mobilePage);
  await mobilePage.goto(pathToFileURL(htmlPath).href, { waitUntil: "load" });
  await mobilePage.bringToFront();
  await mobilePage.waitForSelector("#root > *", { timeout: 10_000 });

  const mobileRoutes = [
    ["数据预览", "#/preview", ["数据预览", "记忆增长", "模块资产"], "mobile-preview.png"],
    ["系统概览", "#/system", ["系统概览", "运行观测", "Provider 状态"], "mobile-system.png"],
    ["黑话发现", "#/jargon", "黑话", "mobile-jargon.png"],
  ];

  for (const [navLabel, hash, expectedText, filename] of mobileRoutes) {
    baselineResults.push(
      await clickMobileNav(
        mobilePage,
        navLabel,
        hash,
        expectedText,
        path.join(screenshotsDir, filename)
      )
    );
  }

  await mobilePage.close();

  const mobileInjection = await openBundledConfigPage(
    browser,
    { width: 390, height: 844 },
    errors,
    "#/injection",
    ["注入策略", "概览", "策略配置", "决策记录"],
  );
  try {
    baselineResults.push(
      await runMobileInjectionStrategySmoke(mobileInjection.page, screenshotsDir),
    );
  } finally {
    await mobileInjection.context.close();
  }

  const widePage = await browser.newPage({ viewport: { width: 2048, height: 1152 } });
  collectPageErrors(widePage, errors);
  await installBridge(widePage);
  await widePage.goto(pathToFileURL(htmlPath).href, { waitUntil: "load" });
  await widePage.bringToFront();
  await widePage.waitForSelector("#root > *", { timeout: 10_000 });

  const wideRoutes = [
    ["#/preview", ["数据预览", "记忆增长", "记忆构成", "模块资产", "group-smoke-primary"], "wide-preview.png", "wide-preview"],
    ["#/learning", ["自主学习", "83.0%", "retrieval_weight", "Formal greeting"], "wide-learning.png", "wide-learning"],
    ["#/affection", ["好感度与情绪", "开心", "群聊今天的氛围很积极。", "所有好感用户"], "wide-affection.png", "wide-affection"],
    ["#/social", ["社交关系", "alice", "bob", "pair", "project"], "wide-social.png", "wide-social"],
    ["#/profiles", ["用户画像", "Profile smoke 1", "Profile smoke 8"], "wide-profiles-table.png", "wide-profiles-table"],
  ];

  for (const [hash, expectedText, filename, routeLabel] of wideRoutes) {
    baselineResults.push(
      await captureRoute(
        widePage,
        hash,
        expectedText,
        path.join(screenshotsDir, filename),
        routeLabel
      )
    );
    if (hash === "#/social") {
      await assertSocialTableWorkspace(widePage);
    }
  }

  baselineResults.push(
    await runWideInjectionStrategySmoke(widePage, screenshotsDir),
  );

  await widePage.close();
  await page.bringToFront();

  baselineResults.push(
    ...await runUnifiedEditingSmoke(page, browser, errors, screenshotsDir),
  );

  await navigateSidebar(
    page,
    "系统概览",
    "#/system",
    ["系统概览", "运行观测", "Provider 状态"],
  );
  baselineResults.push(
    await assertBackupDestructiveConfirmations(
      page,
      path.join(screenshotsDir, "system-confirmation.png"),
    ),
  );
  await assertHighImpactConfirmation(page);

  await navigateSidebar(
    page,
    "自主学习",
    "#/learning",
    ["自主学习", "命中率", "学习参数", "表达模式"],
  );

  await page.evaluate(() => {
    window.__memoraThemeTransitionSeen = document.documentElement.classList.contains(
      "theme-transitioning",
    );
    window.__memoraThemeTransitionObserver?.disconnect();
    window.__memoraThemeTransitionObserver = new MutationObserver(() => {
      if (document.documentElement.classList.contains("theme-transitioning")) {
        window.__memoraThemeTransitionSeen = true;
      }
    });
    window.__memoraThemeTransitionObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
  });
  await page.getByRole("button", { name: "切换主题" }).click();
  await page.waitForFunction(
    () => (
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
      || window.__memoraThemeTransitionSeen === true
    ),
    undefined,
    { timeout: 5_000 }
  );
  await page.waitForFunction(
    () => (
      document.documentElement.getAttribute("data-theme") === "dark"
      && !document.documentElement.classList.contains("theme-transitioning")
    ),
    undefined,
    { timeout: 5_000 }
  );
  await page.waitForFunction(
    () => {
      const themeSurfaces = [
        document.documentElement,
        document.querySelector("aside"),
        document.querySelector('aside [aria-current="page"]'),
        document.querySelector("main"),
        document.querySelector('main > [data-slot="app-header"]'),
        document.querySelector('[data-slot="page-frame"]'),
        document.querySelector('[data-slot="page-header"]'),
        ...Array.from(document.querySelectorAll('[data-slot="card"]')).slice(0, 2),
      ];
      const hasActiveThemeTransition = (element) => (
        element.getAnimations().some((animation) => (
          animation instanceof CSSTransition
          && (animation.pending || animation.playState === "running")
        ))
      );
      return themeSurfaces.length >= 9
        && themeSurfaces.every((element) => element instanceof HTMLElement)
        && themeSurfaces.every((element) => !hasActiveThemeTransition(element));
    },
    undefined,
    { timeout: 5_000 }
  );
  await page.evaluate(() => {
    window.__memoraThemeTransitionObserver?.disconnect();
    delete window.__memoraThemeTransitionObserver;
    delete window.__memoraThemeTransitionSeen;
  });
  await page.locator('[data-slot="page-content"]').last().evaluate((element) => {
    element.scrollTo({ top: 0, left: 0 });
  });
  baselineResults.push(
    await captureBaselineScreenshot(
      page,
      path.join(screenshotsDir, "dark-learning.png"),
      "dark-learning"
    )
  );

  baselineResults.push(
    await captureRoute(
      page,
      "#/system",
      ["系统概览", "运行观测", "Provider 状态"],
      path.join(screenshotsDir, "dark-system.png"),
      "dark-system"
    )
  );

  baselineResults.push(
    await captureRoute(
      page,
      "#/preview",
      ["数据预览", "记忆增长", "记忆构成", "模块资产"],
      path.join(screenshotsDir, "dark-preview.png"),
      "dark-preview"
    )
  );

  baselineResults.push(
    await captureRoute(
      page,
      "#/social",
      ["社交关系", "alice", "bob", "pair", "project"],
      path.join(screenshotsDir, "dark-social-table.png"),
      "dark-social-table",
    )
  );

  const i18nContext = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  try {
    const i18nPage = await i18nContext.newPage();
    collectPageErrors(i18nPage, errors);
    await installBridge(i18nPage);
    await i18nPage.goto(pathToFileURL(htmlPath).href, { waitUntil: "load" });
    await i18nPage.bringToFront();
    try {
      await i18nPage.waitForSelector("#root > *", { timeout: 10_000 });
    } catch (error) {
      const diagnostics = await i18nPage.evaluate(() => ({
        url: window.location.href,
        readyState: document.readyState,
        rootChildren: document.querySelector("#root")?.childElementCount ?? -1,
        rootText: document.querySelector("#root")?.textContent?.slice(0, 240) ?? null,
        scripts: [...document.scripts].map((script) => script.src || "inline"),
        bodyText: document.body?.innerText?.slice(0, 240) ?? null,
      }));
      throw new Error(
        `Dashboard i18n page did not render: ${JSON.stringify({ diagnostics, errors })}`,
        { cause: error },
      );
    }

    const i18nRoutes = [
      {
        language: "en",
        documentLang: "en-US",
        routes: [
          ["#/preview", ["Preview", "Memory growth", "Memory composition", "Module assets", "Active sessions"], "i18n-en-preview.png"],
          ["#/memory", ["Memories", "All Status", "SUMMARY", "IMPORTANCE", "Active", "Page 1/1 · 1 total"], "i18n-en-memory.png"],
        ],
      },
      {
        language: "ru",
        documentLang: "ru-RU",
        routes: [
          ["#/preview", ["Обзор", "Рост памяти", "Состав памяти", "Активы модулей", "Активные сессии"], "i18n-ru-preview.png"],
          ["#/memory", ["Память", "Все", "СВОДКА", "ВАЖНОСТЬ", "Активные", "Стр. 1/1 · 1 всего"], "i18n-ru-memory.png"],
        ],
      },
    ];

    for (const { language, documentLang, routes: localizedRoutes } of i18nRoutes) {
      await switchDashboardLanguage(i18nPage, language, documentLang);
      for (const [hash, expectedText, filename] of localizedRoutes) {
        baselineResults.push(
          await captureLocalizedRoute(
            i18nPage,
            hash,
            expectedText,
            documentLang,
            path.join(screenshotsDir, filename),
            `${language}-${hash.slice(2)}`,
          )
        );
      }
    }
  } finally {
    await i18nContext.close();
  }

  const manifestPath = path.join(
    screenshotsDir,
    "screenshot-baseline-manifest.json",
  );
  await writeFile(
    manifestPath,
    JSON.stringify(
      {
        generatedAt: new Date().toISOString(),
        browser: label,
        baselines: SCREENSHOT_BASELINES,
        screenshots: baselineResults,
      },
      null,
      2
    ),
    "utf8"
  );

  console.log(`screenshotsDir=${screenshotsDir}`);
  console.log(`manifest=${manifestPath}`);

  if (errors.length > 0) {
    throw new Error(`Dashboard browser smoke reported errors:\n${errors.join("\n")}`);
  }

  console.log(`Dashboard browser smoke passed with ${label}.`);
} finally {
  await browser.close();
}
