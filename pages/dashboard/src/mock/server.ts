// ================================================================
// Mock API server — simulates AstrBot Plugin Page bridge
// ================================================================
import type {
  InjectionCostPoint,
  InjectionDecisionDetail,
  InjectionDecisionListItem,
  InjectionOutcome,
  InjectionPresetName,
  InjectionRecentEvent,
  InjectionRoutingMode,
  InjectionSummaryWindow,
} from "@/types/injection";

import { MEMORIES, GRAPH_NODES, GRAPH_EDGES, PROFILES, KNOWLEDGE_ENTRIES, NOTES, JARGON_CANDIDATES, JARGON_MEANINGS, AFFECTION_DATA, MOOD_TYPES, SOCIAL_RELATIONS, QUALITY_SCORES, QUALITY_ALERTS, DELEGATION_STATUS, EXPRESSION_PATTERNS, EVALUATION_DATASETS, EVALUATION_REPORTS, RECALL_TRACE_SAMPLE, DIAGNOSTIC_HEALTH, DIAGNOSTIC_EVENTS, REVIEW_ITEMS, REVIEW_ACTIONS, INJECTION_DECISIONS, INJECTION_MOCK_NOW_MS } from "./data";
import { createMockConfigServer } from "./configServer";

type ApiResponse = { status: string; data?: unknown; message?: string };

const configServer = createMockConfigServer({
  disconnectDuringReload: true,
  autoCompleteReloadMs: 750,
});

function ok(data: unknown): ApiResponse {
  return { status: "ok", data };
}

function err(message: string): ApiResponse {
  return { status: "error", message };
}

// Simulate network latency (80-250ms)
function delay(): Promise<void> {
  const ms = 80 + Math.random() * 170;
  return new Promise((r) => setTimeout(r, ms));
}

// ---- Route handlers ----

function handleStats(): ApiResponse {
  const active = MEMORIES.filter((m) => m.status === "active").length;
  const archived = MEMORIES.filter((m) => m.status === "archived").length;
  const deleted = MEMORIES.filter((m) => m.status === "deleted").length;

  const importanceDist: Record<string, number> = {};
  for (let i = 0; i < 10; i++) importanceDist[`${i}-${i + 1}`] = 0;
  MEMORIES.forEach((m) => {
    const normalized = m.importance <= 1 ? m.importance * 10 : m.importance;
    const index = Math.min(9, Math.max(0, Math.floor(normalized)));
    const bucket = `${index}-${index + 1}`;
    importanceDist[bucket] = (importanceDist[bucket] ?? 0) + 1;
  });

  const atomTypes: Record<string, number> = {};
  MEMORIES.forEach((m) => {
    atomTypes[m.type] = (atomTypes[m.type] ?? 0) + 1;
  });

  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);
  const dailyMemoryCounts = Array.from({ length: 90 }, (_, index) => {
    const day = new Date(today);
    day.setUTCDate(today.getUTCDate() - (89 - index));
    return { date: day.toISOString().slice(0, 10), count: 0 };
  });
  const dailyByDate = new Map(dailyMemoryCounts.map((item) => [item.date, item]));
  MEMORIES.forEach((memory) => {
    const bucket = dailyByDate.get(String(memory.created_at).slice(0, 10));
    if (bucket) bucket.count += 1;
  });

  return ok({
    total_memories: MEMORIES.length,
    active_count: active,
    archived_count: archived,
    deleted_count: deleted,
    graph_nodes: GRAPH_NODES.length,
    graph_edges: GRAPH_EDGES.length,
    graph_entries: GRAPH_NODES.length,
    atom_count: MEMORIES.length,
    avg_importance: MEMORIES.length > 0
      ? MEMORIES.reduce((sum, memory) => sum + Math.min(1, memory.importance > 1 ? memory.importance / 10 : memory.importance), 0) / MEMORIES.length
      : 0,
    status_breakdown: { active, archived, deleted },
    importance_distribution: importanceDist,
    atom_breakdown: atomTypes,
    recent_sessions: [
      { session_id: "sess_5", message_count: 55 },
      { session_id: "sess_1", message_count: 45 },
      { session_id: "sess_2", message_count: 32 },
      { session_id: "sess_3", message_count: 28 },
      { session_id: "sess_4", message_count: 19 },
    ],
    daily_memory_counts: dailyMemoryCounts,
    backups: [
      { name: "pre_v2.4.0_backup", size: 2_560_000, created: "2026-06-01T00:00:00Z" },
      { name: "pre_v2.3.0_backup", size: 2_100_000, created: "2026-05-15T00:00:00Z" },
    ],
  });
}

function handleMetricsSummary(): ApiResponse {
  return ok({
    recall: {
      sample_count: 42,
      avg_total_ms: 83.6,
      avg_bm25_ms: 12.4,
      avg_vector_ms: 24.8,
      avg_graph_ms: 18.3,
      avg_rerank_ms: 28.1,
      p50_total_ms: 76.5,
      p95_total_ms: 148.2,
      recent: [
        { total_ms: 72.1, bm25_ms: 10.2, vector_ms: 22.4, graph_ms: 16.8, rerank_ms: 22.7 },
        { total_ms: 148.2, bm25_ms: 19.1, vector_ms: 41.3, graph_ms: 27.6, rerank_ms: 60.2 },
      ],
    },
    quality: {
      status: "ok",
      total_scored: 1042,
      avg_overall: 0.78,
      paused: false,
      alert_counts: { critical: 1, high: 1, medium: 1, info: 1 },
    },
    background_tasks: {
      tracked: 5,
      active: 2,
      completed: 3,
      failed: 1,
      cancelled: 0,
      failed_tasks: [
        {
          name: "provider-retry",
          error: "TimeoutError",
          message: "provider retry timed out",
          suggestion: "检查 LLM/Embedding provider 配置与网络状态，然后等待重试或重启插件初始化。",
        },
      ],
      schedulers: {
        backfill: {
          status: "completed_with_errors",
          running: false,
          errors: 1,
          processed: 120,
          total: 128,
          last_error: "topic split failed",
          suggestion: "检查话题分割配置和最近的错误详情；修复后可重新启动存量回填。",
        },
      },
    },
    provider: {
      status: "ready",
      providers_ready: true,
      attempts: 3,
      max_attempts: 60,
      retry_active: false,
      missing_provider: [],
    },
    index: {
      validator_available: true,
      last_rebuild_success: true,
      last_rebuild_duration_seconds: 2.4,
      last_rebuild_errors: 0,
      last_rebuild_total: 128,
    },
    write_coordinator: {
      operations_total: 318,
      lock_retries_total: 7,
      failures_total: 1,
      retry_exhausted_total: 0,
      fatal_failures_total: 0,
      non_retryable_failures_total: 1,
      last_error: null,
    },
    prometheus: {
      available: true,
      collector_count: 9,
      metric_names: ["memora_write_operations", "memora_write_lock_retries", "memora_write_failures"],
    },
  });
}

function handleMemories(params: Record<string, string>): ApiResponse {
  let filtered = [...MEMORIES];

  const keyword = params.keyword?.toLowerCase();
  if (keyword) {
    filtered = filtered.filter(
      (m) =>
        m.id.toLowerCase().includes(keyword) ||
        (m.content ?? "").toLowerCase().includes(keyword) ||
        (m.summary ?? "").toLowerCase().includes(keyword)
    );
  }

  if (params.session_id) {
    filtered = filtered.filter((m) => m.session_id === params.session_id);
  }

  if (params.status && params.status !== "all") {
    filtered = filtered.filter((m) => m.status === params.status);
  }

  const page = parseInt(params.page ?? "1", 10);
  const pageSize = parseInt(params.page_size ?? "20", 10);
  const total = filtered.length;
  const start = (page - 1) * pageSize;
  const items = filtered.slice(start, start + pageSize);

  return ok({ items, total, page, page_size: pageSize });
}

function handleMemoryDetail(id: string): ApiResponse {
  const m = MEMORIES.find((m) => m.id === id);
  return m ? ok({ memory: m }) : err("Memory not found");
}

function handleMemoryUpdate(body: Record<string, unknown>): ApiResponse {
  const idx = MEMORIES.findIndex((m) => m.id === (body.memory_id as string));
  if (idx === -1) return err("Memory not found");
  const field = body.field as string;
  const value = body.value;
  if (field && value !== undefined) {
    (MEMORIES[idx] as Record<string, unknown>)[field] = value;
  }
  return ok({ updated: true });
}

function handleMemoryBatch(body: Record<string, unknown>): ApiResponse {
  const ids = body.memory_ids as string[];
  const action = body.action as string;
  if (!ids || !action) return err("Missing memory_ids or action");
  return ok({ action, affected: ids.length });
}

function handleGraphSearch(params: Record<string, string>): ApiResponse {
  const q = params.query?.toLowerCase();
  let nodes = GRAPH_NODES;
  let edges = GRAPH_EDGES;

  if (q) {
    nodes = nodes.filter(
      (n) =>
        (n.label ?? "").toLowerCase().includes(q) ||
        (n.type ?? "").toLowerCase().includes(q)
    );
    const nodeIds = new Set(nodes.map((n) => n.id));
        edges = edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));
  }

  return ok({ nodes, edges });
}

function handleProfiles(params: Record<string, string>): ApiResponse {
  const limit = parseInt(params.limit ?? "100", 10);
  const items = PROFILES.slice(0, limit);
  return ok({ profiles: items, total: PROFILES.length, count: PROFILES.length });
}

function handleProfileDetail(userId: string): ApiResponse {
  const p = PROFILES.find((p) => p.user_id === userId);
  return p ? ok({ profile: p }) : err("Profile not found");
}

function handleKnowledgeList(params: Record<string, string>): ApiResponse {
  let items = [...KNOWLEDGE_ENTRIES];
  if (params.category) {
    items = items.filter((e) => e.category === params.category);
  }
  return ok({ entries: items, items, total: KNOWLEDGE_ENTRIES.length, count: KNOWLEDGE_ENTRIES.length });
}

function handleKnowledgeSearch(query: string): ApiResponse {
  const q = query.toLowerCase();
  const items = KNOWLEDGE_ENTRIES.filter(
    (e) =>
      e.title.toLowerCase().includes(q) ||
      (e.content ?? "").toLowerCase().includes(q)
  );
  return ok({ entries: items, items });
}

function handleKnowledgeDetail(entryId: string): ApiResponse {
  const e = KNOWLEDGE_ENTRIES.find((e) => e.entry_id === entryId);
  return e ? ok({ entry: e }) : err("Entry not found");
}

function handleKnowledgeCreate(body: Record<string, unknown>): ApiResponse {
  const newEntry = {
    entry_id: `k${KNOWLEDGE_ENTRIES.length + 1}`,
    title: (body.title as string) ?? "Untitled",
    content: (body.content as string) ?? "",
    category: (body.category as string) ?? "fact",
    confidence: 0.5,
    access_count: 0,
    updated_at: new Date().toISOString(),
  };
  KNOWLEDGE_ENTRIES.push(newEntry);
  return ok({ entry: newEntry });
}

function handleKnowledgeDelete(body: Record<string, unknown>): ApiResponse {
  const id = body.entry_id as string;
  const idx = KNOWLEDGE_ENTRIES.findIndex((e) => e.entry_id === id);
  if (idx === -1) return err("Entry not found");
  KNOWLEDGE_ENTRIES.splice(idx, 1);
  return ok({ deleted: true });
}

function handleKnowledgeUpdate(body: Record<string, unknown>): ApiResponse {
  const id = body.entry_id as string;
  const entry = KNOWLEDGE_ENTRIES.find((e) => e.entry_id === id);
  if (!entry) return err("Entry not found");
  const field = body.field as string;
  const value = body.value;
  if (field && value !== undefined) {
    (entry as Record<string, unknown>)[field] = field === "confidence" ? Number(value) : value;
  }
  entry.updated_at = new Date().toISOString();
  return ok({ entry });
}

function handleKnowledgeBatch(body: Record<string, unknown>): ApiResponse {
  const ids = body.entry_ids as string[];
  if (!ids) return err("Missing entry_ids");
  for (const id of ids) {
    const idx = KNOWLEDGE_ENTRIES.findIndex((e) => e.entry_id === id);
    if (idx !== -1) KNOWLEDGE_ENTRIES.splice(idx, 1);
  }
  return ok({ action: body.action ?? "delete", affected: ids.length });
}

function handleNotesList(params: Record<string, string>): ApiResponse {
  let items = [...NOTES];
  if (params.status) {
    items = items.filter((n) => n.status === params.status);
  }
  return ok({ notes: items, items, total: NOTES.length, count: NOTES.length });
}

function handleNoteSearch(query: string): ApiResponse {
  const q = query.toLowerCase();
  const items = NOTES.filter(
    (n) =>
      n.title.toLowerCase().includes(q) ||
      (n.content ?? "").toLowerCase().includes(q) ||
      (n.tags ?? []).some((t) => t.toLowerCase().includes(q))
  );
  return ok({ notes: items, items });
}

function handleNoteDetail(noteId: string): ApiResponse {
  const n = NOTES.find((n) => n.note_id === noteId);
  return n ? ok({ note: n }) : err("Note not found");
}

function handleNoteCreate(body: Record<string, unknown>): ApiResponse {
  const newNote = {
    note_id: `note_${String(NOTES.length + 1).padStart(3, "0")}`,
    title: (body.title as string) ?? "Untitled",
    content: (body.content as string) ?? "",
    tags: (body.tags as string[]) ?? [],
    status: "active",
    version: 1,
    updated_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
  };
  NOTES.push(newNote);
  return ok({ note: newNote });
}

function handleNoteDelete(body: Record<string, unknown>): ApiResponse {
  const id = body.note_id as string;
  const idx = NOTES.findIndex((n) => n.note_id === id);
  if (idx === -1) return err("Note not found");
  NOTES.splice(idx, 1);
  return ok({ deleted: true });
}

function handleNoteArchive(body: Record<string, unknown>): ApiResponse {
  const id = body.note_id as string;
  const note = NOTES.find((n) => n.note_id === id);
  if (!note) return err("Note not found");
  note.status = "archived";
  return ok({ note });
}

function handleNoteUpdate(body: Record<string, unknown>): ApiResponse {
  const id = body.note_id as string;
  const note = NOTES.find((n) => n.note_id === id);
  if (!note) return err("Note not found");
  const field = body.field as string;
  const value = body.value;
  if (field && value !== undefined) {
    if (field === "tags") {
      note.tags = String(value).split(",").map((t) => t.trim()).filter(Boolean);
    } else {
      (note as Record<string, unknown>)[field] = value;
    }
  }
  note.updated_at = new Date().toISOString();
  note.version = (note.version ?? 1) + 1;
  return ok({ note });
}

function handleNoteBatch(body: Record<string, unknown>): ApiResponse {
  const ids = body.note_ids as string[];
  const action = body.action as string;
  if (!ids) return err("Missing note_ids");
  for (const id of ids) {
    const note = NOTES.find((n) => n.note_id === id);
    if (!note) continue;
    if (action === "delete") {
      const idx = NOTES.findIndex((n) => n.note_id === id);
      if (idx !== -1) NOTES.splice(idx, 1);
    } else if (action === "archive") {
      note.status = "archived";
    }
  }
  return ok({ action, affected: ids.length });
}

function handleRecallTest(body: Record<string, unknown>): ApiResponse {
  const k = (body.k as number) ?? 5;
  const results = MEMORIES.slice(0, Math.min(k, MEMORIES.length)).map((m, i) => ({
    ...m,
    score: parseFloat((0.95 - i * 0.07).toFixed(3)),
    doc_kw_score: parseFloat((0.85 - i * 0.1).toFixed(3)),
    doc_vec_score: parseFloat((0.88 - i * 0.08).toFixed(3)),
    graph_kw_score: parseFloat((0.72 - i * 0.1).toFixed(3)),
    graph_vec_score: parseFloat((0.68 - i * 0.12).toFixed(3)),
  }));
  return ok({ results, memories: results });
}

function clampInt(value: unknown, min: number, max: number, fallback: number): number {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, Math.round(n)));
}

function handleRecallTrace(body: Record<string, unknown>): ApiResponse {
  const k = clampInt(body.k, 1, 20, 5);
  const chainDepth = clampInt(body.chain_depth, 0, 5, 2);
  return ok({
    ...RECALL_TRACE_SAMPLE,
    trace_id: `trace-mock-${Date.now()}`,
    query: String(body.query ?? RECALL_TRACE_SAMPLE.query),
    results: RECALL_TRACE_SAMPLE.results.slice(0, k),
    created_at: Date.now() / 1000,
    metadata: {
      ...RECALL_TRACE_SAMPLE.metadata,
      session_id: String(body.session_id ?? ""),
      user_id: String(body.user_id ?? ""),
      chat_type: String(body.chat_type ?? "private"),
      chain_depth: chainDepth,
      requested_k: k,
    },
  });
}

function handleRecallTraceDetail(): ApiResponse {
  return ok(RECALL_TRACE_SAMPLE);
}

function handleEvaluationDatasets(): ApiResponse {
  return ok({ datasets: EVALUATION_DATASETS });
}

function handleEvaluationReports(params: Record<string, string>): ApiResponse {
  const limit = Math.min(50, Math.max(1, parseInt(params.limit ?? "10", 10)));
  return ok({ reports: EVALUATION_REPORTS.slice(0, limit) });
}

function handleEvaluationReportDetail(params: Record<string, string>): ApiResponse {
  const reportId = params.report_id ?? params.id;
  const report = EVALUATION_REPORTS.find((item) => item.report_id === reportId);
  return report ? ok({ report }) : err("Evaluation report not found");
}

function handleEvaluationRun(body: Record<string, unknown>): ApiResponse {
  const template = EVALUATION_REPORTS[0];
  const datasets = Array.isArray(body.datasets) ? body.datasets.map(String) : ["private_basic"];
  const variants = Array.isArray(body.variants) ? body.variants.map(String) : ["baseline"];
  const k = Math.min(20, Math.max(1, Number(body.k ?? template.summary.k)));
  const report = {
    ...template,
    report_id: `eval-${Date.now()}`,
    created_at: Date.now() / 1000,
    baseline: String(body.baseline ?? "baseline"),
    datasets,
    summary: { ...template.summary, k },
    variants: Object.fromEntries(
      Object.entries(template.variants).filter(([name]) => variants.includes(name))
    ),
    deltas: Object.fromEntries(
      Object.entries(template.deltas ?? {}).filter(([name]) => variants.includes(name))
    ),
    cases: (template.cases ?? []).map((item) => ({ ...item })),
  };
  EVALUATION_REPORTS.unshift(report);
  return ok(report);
}

function handleDiagnosticHealth(): ApiResponse {
  return ok(DIAGNOSTIC_HEALTH);
}

function handleDiagnosticEvents(params: Record<string, string>): ApiResponse {
  const limit = Math.min(100, Math.max(1, parseInt(params.limit ?? "50", 10)));
  return ok({
    events: DIAGNOSTIC_EVENTS.slice(0, limit),
    total: DIAGNOSTIC_EVENTS.length,
  });
}

function handleDiagnosticAction(body: Record<string, unknown>): ApiResponse {
  const action = String(body.action ?? "");
  if (action === "refresh_metrics") {
    return ok({ action, refreshed: true, message: "diagnostic metrics refreshed" });
  }
  if (action === "rebuild_index") {
    if (body.confirmed !== true) return err("confirmation_required");
    return ok({ action, accepted: true, message: "index rebuild requested" });
  }
  return err(`unknown diagnostic action: ${action}`);
}

function handleReviewItems(params: Record<string, string>): ApiResponse {
  let items = [...REVIEW_ITEMS];
  if (params.status) items = items.filter((item) => item.status === params.status);
  if (params.reason) items = items.filter((item) => item.reasons.includes(params.reason));
  if (params.severity) items = items.filter((item) => item.severity === params.severity);
  const limit = Math.min(100, Math.max(1, parseInt(params.limit ?? "50", 10)));
  return ok({ items: items.slice(0, limit), total: items.length });
}

function handleReviewDetail(params: Record<string, string>): ApiResponse {
  const reviewId = params.review_id ?? params.item_id ?? "";
  const item = REVIEW_ITEMS.find((entry) => entry.item_id === reviewId);
  if (!item) return err("Review item not found");
  return ok({ item, actions: REVIEW_ACTIONS[reviewId] ?? [] });
}

function handleReviewAction(body: Record<string, unknown>): ApiResponse {
  const reviewId = String(body.review_id ?? "");
  const action = String(body.action ?? "");
  const item = REVIEW_ITEMS.find((entry) => entry.item_id === reviewId);
  if (!item) return err("Review item not found");
  const statusByAction: Record<string, string> = {
    approve: "approved",
    edit: "edited",
    merge: "merged",
    archive: "archived",
    delete: "deleted",
    mark_safe: "safe",
  };
  const nextStatus = statusByAction[action];
  if (!nextStatus) return err(`unsupported review action: ${action}`);
  if (action === "delete" && body.confirmed !== true) return err("confirmation_required");

  const payload = body.payload && typeof body.payload === "object" && !Array.isArray(body.payload)
    ? body.payload as Record<string, unknown>
    : {};
  if (action === "edit" && (typeof payload.content !== "string" || payload.content.trim() === "")) {
    return err("edit content required");
  }
  if (action === "merge" && (typeof payload.target_memory_id !== "string" || payload.target_memory_id.trim() === "")) {
    return err("target_memory_id required");
  }

  if (action === "edit") {
    item.content_preview = String(payload.content);
  }
  item.status = nextStatus;
  item.updated_at = Date.now() / 1000;

  const record = {
    action_id: `review-action-${Date.now()}`,
    item_id: reviewId,
    action: nextStatus,
    actor_id: "operator",
    payload,
    created_at: Date.now() / 1000,
  };
  REVIEW_ACTIONS[reviewId] = [record, ...(REVIEW_ACTIONS[reviewId] ?? [])];
  return ok({ item, action: record, accepted: true });
}

// Mock backups (in-memory)
const MOCK_BACKUPS: Array<Record<string, unknown>> = [
  { name: "v2.3.0", directory: "/backups/v2.3.0", file_count: 6, plugin_version: "2.3.0", backup_timestamp: "2026-06-01T10:00:00Z", files: ["memora.db", "conversations.db"] },
  { name: "manual_20260613_120000", directory: "/backups/manual_20260613_120000", file_count: 6, plugin_version: "2.4.2", backup_timestamp: "2026-06-13T12:00:00Z", files: ["memora.db", "conversations.db"] },
];

function handleLearningStatus(): ApiResponse {
  return ok({
    hit_rate: 0.78,
    avg_quality: 0.842,
    total_trials: 156,
    total_corrections: 23,
    parameters: {
      recall_weight: 0.65,
      graph_weight: 0.35,
      emotion_bonus: 0.12,
      recency_decay: 0.03,
      importance_threshold: 4.2,
      fusion_k: 60,
      mmr_lambda: 0.7,
      learning_rate: 0.01,
    },
    history: [
      { timestamp: "2026-06-15T14:30:00Z", action: "weight_adjust", detail: "recall_weight +0.03 (hit_rate improved)" },
      { timestamp: "2026-06-14T09:15:00Z", action: "threshold_tune", detail: "importance_threshold 5.0→4.2 (wider recall)" },
      { timestamp: "2026-06-13T16:45:00Z", action: "correction", detail: "emotion_bonus reverted: -0.05 (negative feedback)" },
      { timestamp: "2026-06-12T11:00:00Z", action: "param_init", detail: "Initial parameter set from defaults" },
      { timestamp: "2026-06-11T08:20:00Z", action: "weight_adjust", detail: "graph_weight +0.05 (graph route underused)" },
    ],
  });
}

function handleBackupList(): ApiResponse {
  return ok({ backups: MOCK_BACKUPS, total: MOCK_BACKUPS.length });
}

function handleBackupDelete(body: Record<string, unknown>): ApiResponse {
  const name = body.name as string;
  if (!name) return err("backup name required");
  const idx = MOCK_BACKUPS.findIndex((b) => b.name === name);
  if (idx === -1) return err(`backup not found: ${name}`);
  MOCK_BACKUPS.splice(idx, 1);
  return ok({ message: `deleted ${name}`, name });
}

function handleBackupBatchDelete(body: Record<string, unknown>): ApiResponse {
  const names = body.names as string[];
  if (!names?.length) return err("names required");
  let deleted = 0;
  for (const name of names) {
    const idx = MOCK_BACKUPS.findIndex((b) => b.name === name);
    if (idx !== -1) { MOCK_BACKUPS.splice(idx, 1); deleted++; }
  }
  return ok({ message: `deleted ${deleted}/${names.length}`, deleted, failed: names.length - deleted });
}

function handleBackupRestore(body: Record<string, unknown>): ApiResponse {
  const name = body.name as string;
  if (!name) return err("backup name required");
  const found = MOCK_BACKUPS.find((b) => b.name === name);
  if (!found) return err(`backup not found: ${name}`);
  return ok({ message: `restored ${found.file_count} files from ${name}`, restored: found.file_count });
}

function handleExportMemories(body: Record<string, unknown>): ApiResponse {
  const format = (body.format as string) ?? "jsonl";
  if (format === "markdown") {
    const content = MEMORIES.map((m, i) =>
      `## Memory #${i + 1}\n\n- **Importance**: ${m.importance?.toFixed(2) ?? "0.50"}\n- **Type**: ${m.type ?? "GENERAL"}\n\n${m.content ?? ""}\n\n---\n`
    ).join("\n");
    return ok({ content, count: MEMORIES.length, format });
  }
  const content = MEMORIES.map((m) =>
    JSON.stringify({ id: m.id, content: m.content, metadata: { type: m.type, importance: m.importance }, exported_at: Date.now() / 1000 })
  ).join("\n");
  return ok({ content, count: MEMORIES.length, format });
}

const INJECTION_WINDOWS_MS: Record<InjectionSummaryWindow, number> = {
  "1h": 3_600_000,
  "24h": 86_400_000,
  "7d": 604_800_000,
  "30d": 2_592_000_000,
};
const INJECTION_ROUTING_MODES: InjectionRoutingMode[] = [
  "manual",
  "auto",
  "hybrid",
];
const INJECTION_PRESETS: InjectionPresetName[] = [
  "tool_first",
  "low_cost",
  "balanced",
  "quality",
];
const INJECTION_OUTCOMES: InjectionOutcome[] = [
  "injected",
  "skipped",
  "empty",
  "fallback",
  "error",
];
const INJECTION_LIST_QUERY_FIELDS = new Set([
  "offset",
  "limit",
  "from_ms",
  "to_ms",
  "routing_mode",
  "resolved_preset",
  "provider_type",
  "primary_reason",
  "fallback_applied",
  "outcome",
]);

function payloadP95(rows: InjectionDecisionDetail[]): number {
  const values = rows
    .map((row) => row.actual_payload_chars)
    .sort((left, right) => left - right);
  return values[Math.max(0, Math.ceil(values.length * 0.95) - 1)] ?? 0;
}

function toInjectionRecentEvent(
  row: InjectionDecisionDetail,
): InjectionRecentEvent {
  return {
    decision_id: row.decision_id,
    created_at_ms: row.created_at_ms,
    trace_id: row.trace_id,
    routing_mode: row.routing_mode,
    resolved_preset: row.resolved_preset,
    outcome: row.outcome,
    primary_reason: row.primary_reason,
    fallback_applied: row.fallback_applied,
    actual_payload_chars: row.actual_payload_chars,
  };
}

function toInjectionListItem(
  row: InjectionDecisionDetail,
): InjectionDecisionListItem {
  return {
    ...toInjectionRecentEvent(row),
    configured_preset: row.configured_preset,
    recommended_preset: row.recommended_preset,
    preferred_delivery: row.preferred_delivery,
    resolved_delivery: row.resolved_delivery,
    provider_type: row.provider_type,
    provider_model: row.provider_model,
    error_code: row.error_code,
    candidate_count: row.candidate_count,
    selected_count: row.selected_count,
    dropped_count: row.dropped_count,
    truncated_count: row.truncated_count,
    configured_budget_chars: row.configured_budget_chars,
    effective_budget_chars: row.effective_budget_chars,
    context_headroom_chars: row.context_headroom_chars,
    decision_ms: row.decision_ms,
    format_ms: row.format_ms,
    inject_ms: row.inject_ms,
  };
}

function toInjectionDetail(
  row: InjectionDecisionDetail,
): InjectionDecisionDetail {
  return {
    ...toInjectionListItem(row),
    reason_codes: [...row.reason_codes],
  };
}

function buildInjectionCostTrend(
  rows: InjectionDecisionDetail[],
): InjectionCostPoint[] {
  const buckets = new Map<number, InjectionDecisionDetail[]>();
  for (const row of rows) {
    const bucket = Math.floor(row.created_at_ms / 3_600_000) * 3_600_000;
    buckets.set(bucket, [...(buckets.get(bucket) ?? []), row]);
  }
  return [...buckets.entries()]
    .sort(([left], [right]) => left - right)
    .map(([bucket_ms, items]) => ({
      bucket_ms,
      decision_count: items.length,
      payload_chars_p95: payloadP95(items),
      provider_fallback_rate: items.filter((item) => item.fallback_applied).length
        / items.length,
    }));
}

function handleInjectionCatalog(): ApiResponse {
  return ok({
    routing_modes: INJECTION_ROUTING_MODES,
    presets: [
      {
        name: "tool_first",
        rank: 0,
        auto_inject: false,
        memory_budget_chars: 0,
        max_memories: 0,
        content_level: "NONE",
        cost_penalty_weight: 1,
        minimum_utility: 1,
        allow_tool_fallback: true,
        preferred_delivery: "extra_user_content",
      },
      {
        name: "low_cost",
        rank: 1,
        auto_inject: true,
        memory_budget_chars: 800,
        max_memories: 2,
        content_level: "FACTS",
        cost_penalty_weight: 0.3,
        minimum_utility: 0.45,
        allow_tool_fallback: true,
        preferred_delivery: "extra_user_content",
      },
      {
        name: "balanced",
        rank: 2,
        auto_inject: true,
        memory_budget_chars: 1_200,
        max_memories: 4,
        content_level: "COMPACT",
        cost_penalty_weight: 0.18,
        minimum_utility: 0.3,
        allow_tool_fallback: true,
        preferred_delivery: "extra_user_content",
      },
      {
        name: "quality",
        rank: 3,
        auto_inject: true,
        memory_budget_chars: 2_400,
        max_memories: 6,
        content_level: "DETAILED",
        cost_penalty_weight: 0.08,
        minimum_utility: 0.2,
        allow_tool_fallback: true,
        preferred_delivery: "extra_user_content",
      },
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
  });
}

function handleInjectionSummary(params: Record<string, string>): ApiResponse {
  const windowValue = params.window ?? "24h";
  if (!(windowValue in INJECTION_WINDOWS_MS)) {
    return err("window must be one of 1h, 24h, 7d, 30d");
  }
  const window = windowValue as InjectionSummaryWindow;
  const rows = INJECTION_DECISIONS.filter(
    (row) => row.created_at_ms >= INJECTION_MOCK_NOW_MS - INJECTION_WINDOWS_MS[window],
  );
  return ok({
    window,
    decision_count: rows.length,
    payload_chars_p95: payloadP95(rows),
    provider_fallback_rate: rows.length
      ? rows.filter((row) => row.fallback_applied).length / rows.length
      : 0,
    preset_distribution: Object.fromEntries(
      INJECTION_PRESETS.map((preset) => [
        preset,
        rows.filter((row) => row.resolved_preset === preset).length,
      ]),
    ),
    cost_trend: buildInjectionCostTrend(rows),
    recent_events: rows.slice(0, 15).map(toInjectionRecentEvent),
  });
}

function injectionInteger(value: string, field: string): number {
  if (!/^-?\d+$/.test(value)) throw new Error(`${field} must be an integer`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) throw new Error(`${field} must be an integer`);
  return parsed;
}

function optionalInjectionInteger(
  params: Record<string, string>,
  field: string,
): number | undefined {
  return field in params ? injectionInteger(params[field], field) : undefined;
}

function optionalInjectionText(
  params: Record<string, string>,
  field: string,
): string | undefined {
  if (!(field in params)) return undefined;
  const value = params[field].trim();
  if (!value) throw new Error(`${field} must be a non-empty string`);
  return value;
}

function handleInjectionDecisions(params: Record<string, string>): ApiResponse {
  try {
    const unknown = Object.keys(params)
      .filter((key) => !INJECTION_LIST_QUERY_FIELDS.has(key))
      .sort();
    if (unknown.length) throw new Error(`unknown query field: ${unknown[0]}`);

    const offset = injectionInteger(params.offset ?? "0", "offset");
    const limit = injectionInteger(params.limit ?? "50", "limit");
    if (offset < 0) throw new Error("offset must be non-negative");
    if (limit < 1 || limit > 100) {
      throw new Error("limit must be between 1 and 100");
    }
    const fromMs = optionalInjectionInteger(params, "from_ms");
    const toMs = optionalInjectionInteger(params, "to_ms");
    if (fromMs !== undefined && toMs !== undefined && fromMs > toMs) {
      throw new Error("from_ms must not exceed to_ms");
    }

    const routingMode = params.routing_mode;
    if (routingMode && !INJECTION_ROUTING_MODES.includes(routingMode as never)) {
      throw new Error("routing_mode is invalid");
    }
    const resolvedPreset = params.resolved_preset;
    if (resolvedPreset && !INJECTION_PRESETS.includes(resolvedPreset as never)) {
      throw new Error("resolved_preset is invalid");
    }
    const outcome = params.outcome;
    if (outcome && !INJECTION_OUTCOMES.includes(outcome as never)) {
      throw new Error("outcome is invalid");
    }
    const providerType = optionalInjectionText(params, "provider_type");
    const primaryReason = optionalInjectionText(params, "primary_reason");
    let fallbackApplied: boolean | undefined;
    if ("fallback_applied" in params) {
      const normalized = params.fallback_applied.trim().toLowerCase();
      if (normalized !== "true" && normalized !== "false") {
        throw new Error("fallback_applied must be true or false");
      }
      fallbackApplied = normalized === "true";
    }

    const rows = INJECTION_DECISIONS
      .filter((row) => fromMs === undefined || row.created_at_ms >= fromMs)
      .filter((row) => toMs === undefined || row.created_at_ms <= toMs)
      .filter((row) => !routingMode || row.routing_mode === routingMode)
      .filter((row) => !resolvedPreset || row.resolved_preset === resolvedPreset)
      .filter((row) => !providerType || row.provider_type === providerType)
      .filter((row) => !primaryReason || row.primary_reason === primaryReason)
      .filter((row) => fallbackApplied === undefined
        || row.fallback_applied === fallbackApplied)
      .filter((row) => !outcome || row.outcome === outcome)
      .sort((left, right) => (
        right.created_at_ms - left.created_at_ms
        || right.decision_id.localeCompare(left.decision_id)
      ));
    return ok({
      items: rows.slice(offset, offset + limit).map(toInjectionListItem),
      total: rows.length,
      offset,
      limit,
    });
  } catch (error) {
    return err(error instanceof Error ? error.message : String(error));
  }
}

function handleInjectionDetail(params: Record<string, string>): ApiResponse {
  const decisionId = params.decision_id ?? "";
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(decisionId)) {
    return err("decision_id must be a valid UUID");
  }
  const normalizedId = decisionId.toLowerCase();
  const detail = INJECTION_DECISIONS.find(
    (row) => row.decision_id === normalizedId,
  );
  return detail
    ? ok(toInjectionDetail(detail))
    : err("Injection decision not found");
}

// ---- Main router ----

export async function handleApiGet(path: string, params: Record<string, string> = {}): Promise<ApiResponse> {
  await delay();
  const p = path.replace(/^page\/?/, "");
  const configResponse = configServer.handleGet(p, params);
  if (configResponse) return configResponse;

  if (p === "injection-strategy/catalog") return handleInjectionCatalog();
  if (p === "injection-strategy/summary" || p.startsWith("injection-strategy/summary?")) {
    return handleInjectionSummary(params);
  }
  if (p === "injection-strategy/decisions/detail" || p.startsWith("injection-strategy/decisions/detail?")) {
    return handleInjectionDetail(params);
  }
  if (p === "injection-strategy/decisions" || p.startsWith("injection-strategy/decisions?")) {
    return handleInjectionDecisions(params);
  }
  if (p === "stats") return handleStats();
  if (p === "metrics/summary") return handleMetricsSummary();
  if (p === "memories" || p.startsWith("memories?")) return handleMemories(params);
  // 兼容 memory/detail 和 memories/detail 两种路径
  if (p.startsWith("memory/detail") || p.startsWith("memories/detail")) return handleMemoryDetail(params.id ?? "");
  if (p === "graph/search" || p.startsWith("graph/search?")) return handleGraphSearch(params);
  if (p === "profiles" || p.startsWith("profiles?")) return handleProfiles(params);
  if (p.startsWith("profiles/detail")) return handleProfileDetail(params.user_id ?? "");
  if (p === "knowledge" || p.startsWith("knowledge?")) return handleKnowledgeList(params);
  if (p.startsWith("knowledge/search")) return handleKnowledgeSearch(params.query ?? "");
  if (p.startsWith("knowledge/detail")) return handleKnowledgeDetail(params.entry_id ?? "");
  if (p === "notes" || p.startsWith("notes?")) return handleNotesList(params);
  if (p.startsWith("notes/search")) return handleNoteSearch(params.query ?? "");
  if (p.startsWith("notes/detail")) return handleNoteDetail(params.note_id ?? "");
  if (p === "backup/list" || p.startsWith("backup/list")) return handleBackupList();
  if (p === "learning/status" || p.startsWith("learning/status")) return handleLearningStatus();
  if (p === "recall/trace/detail" || p.startsWith("recall/trace/detail")) return handleRecallTraceDetail();
  if (p === "evaluation/datasets" || p.startsWith("evaluation/datasets")) return handleEvaluationDatasets();
  if (p === "evaluation/reports/detail" || p.startsWith("evaluation/reports/detail")) return handleEvaluationReportDetail(params);
  if (p === "evaluation/reports" || p.startsWith("evaluation/reports?")) return handleEvaluationReports(params);
  if (p === "diagnostics/health") return handleDiagnosticHealth();
  if (p === "diagnostics/events" || p.startsWith("diagnostics/events?")) return handleDiagnosticEvents(params);
  if (p === "review/items" || p.startsWith("review/items?")) return handleReviewItems(params);
  if (p === "review/items/detail" || p.startsWith("review/items/detail")) return handleReviewDetail(params);
  if (p === "config/topic-segmentation") return handleTopicSegConfigGet();
  if (p === "backfill/status") return handleBackfillStatus();
  // v1.0.0+ new subsystems
  if (p === "jargon/candidates" || p.startsWith("jargon/candidates?")) return handleJargonCandidates(params);
  if (p === "jargon/meanings" || p.startsWith("jargon/meanings?")) return handleJargonMeanings(params);
  if (p === "jargon/stats" || p.startsWith("jargon/stats?")) return handleJargonStats(params);
  if (p === "affection/status" || p.startsWith("affection/status?")) return handleAffectionStatus(params);
  if (p === "social/relations" || p.startsWith("social/relations?")) return handleSocialRelations(params);
  if (p === "quality/stats" || p.startsWith("quality/stats")) return handleQualityStats();
  if (p === "quality/recent" || p.startsWith("quality/recent?")) return handleQualityRecent(params);
  if (p === "quality/alerts" || p.startsWith("quality/alerts?")) return handleQualityAlerts(params);
  if (p === "delegation/status" || p.startsWith("delegation/status")) return handleDelegationStatus();
  if (p === "expression/patterns" || p.startsWith("expression/patterns?")) return handleExpressionPatterns(params);
  if (p === "groups" || p.startsWith("groups")) return handleGroups();

  console.warn(`[Mock] Unhandled GET: ${p}`, params);
  return ok({});
}

export async function handleApiPost(path: string, body: unknown = {}): Promise<ApiResponse> {
  await delay();
  const p = path.replace(/^page\/?/, "");
  const configResponse = configServer.handlePost(p, body);
  if (configResponse) return configResponse;
  const data = body as Record<string, unknown>;

  if (p === "recall/test") return handleRecallTest(data);
  if (p === "recall/trace") return handleRecallTrace(data);
  if (p === "memory/update") return handleMemoryUpdate(data);
  if (p === "memories/batch") return handleMemoryBatch(data);
  if (p === "knowledge/create") return handleKnowledgeCreate(data);
  if (p === "knowledge/delete") return handleKnowledgeDelete(data);
  if (p === "knowledge/update") return handleKnowledgeUpdate(data);
  if (p === "knowledge/batch") return handleKnowledgeBatch(data);
  if (p === "notes/create") return handleNoteCreate(data);
  if (p === "notes/delete") return handleNoteDelete(data);
  if (p === "notes/update") return handleNoteUpdate(data);
  if (p === "notes/archive") return handleNoteArchive(data);
  if (p === "notes/batch") return handleNoteBatch(data);
  if (p === "profiles/delete") return ok({ deleted: true });
  if (p === "profiles/batch") return ok({ action: data.action ?? "delete", affected: ((data.user_ids as string[]) ?? []).length });
  if (p === "system/rebuild") return ok({ rebuilt: true });
  if (p === "system/purge") return ok({ purged: true });
  if (p === "system/compact") return ok({ compacted: true });
  if (p === "backup/create") return ok({ backup: { name: `backup_${Date.now()}`, size: 2_600_000, created: new Date().toISOString() } });
  if (p === "backup/restore") return handleBackupRestore(data);
  if (p === "backup/delete") return handleBackupDelete(data);
  if (p === "backup/batch-delete") return handleBackupBatchDelete(data);
  if (p === "export/memories") return handleExportMemories(data);
  if (p === "learning/reset") return ok({ message: "Learning parameters reset to defaults", reset: true });
  if (p === "evaluation/run") return handleEvaluationRun(data);
  if (p === "diagnostics/actions/run") return handleDiagnosticAction(data);
  if (p === "review/action") return handleReviewAction(data);
  if (p === "review/refresh") return ok({ refreshed: true, item_count: REVIEW_ITEMS.length });
  if (p === "config/topic-segmentation") return handleTopicSegConfigUpdate(data);
  if (p === "backfill/start") return handleBackfillStart();
  if (p === "backfill/status") return handleBackfillStatus();
  // v1.0.0+ new subsystems
  if (p === "jargon/confirm") return handleJargonConfirm(data);
  if (p === "jargon/mine") return handleJargonMine(data);
  if (p === "quality/reset") return handleQualityReset();

  console.warn(`[Mock] Unhandled POST: ${p}`, data);
  return ok({});
}

// ---- Topic segmentation mocks ----

interface TopicSegConfig {
  enabled: boolean;
  strategy: string;
  available_strategies: { key: string; label: string; desc: string }[];
  strategy_b: { similarity_threshold: number; min_cluster_size: number; max_clusters: number };
  strategy_c: { topic_shift_threshold: number; min_chunk_size: number };
  strategy_d: { stage1_max_topics: number; enable_parallel_stage2: boolean };
}

let _topicSegConfig: TopicSegConfig = {
  enabled: true,
  strategy: "a_b_hybrid",
  available_strategies: [
    { key: "a_b_hybrid", label: "A+B 混合模式", desc: "LLM 主分割 + 嵌入聚类兜底，零额外 API 成本" },
    { key: "a", label: "方案 A — Prompt 工程", desc: "LLM 直接输出 memories[] 数组" },
    { key: "b", label: "方案 B — 嵌入聚类", desc: "key_facts 嵌入相似度聚类分拆" },
    { key: "c", label: "方案 C — 话题预分块", desc: "LLM 调用前检测话题边界" },
    { key: "d", label: "方案 D — 两阶段 LLM", desc: "先识别话题范围再分别抽取" },
  ],
  strategy_b: { similarity_threshold: 0.5, min_cluster_size: 1, max_clusters: 5 },
  strategy_c: { topic_shift_threshold: 0.3, min_chunk_size: 2 },
  strategy_d: { stage1_max_topics: 5, enable_parallel_stage2: true },
};

let _backfillState: { status: "idle" | "running" | "completed" | "failed"; processed: number; total: number; errors: number; job_id: string; started_at: number } = {
  status: "idle", processed: 0, total: 0, errors: 0,
  job_id: "", started_at: 0,
};

function handleTopicSegConfigGet() {
  return ok(_topicSegConfig);
}

function handleTopicSegConfigUpdate(data: Record<string, unknown>) {
  if (data.strategy !== undefined) _topicSegConfig.strategy = String(data.strategy);
  if (data.enabled !== undefined) _topicSegConfig.enabled = Boolean(data.enabled);
  if (data.strategy_b && typeof data.strategy_b === "object") {
    Object.assign(_topicSegConfig.strategy_b, data.strategy_b);
  }
  if (data.strategy_c && typeof data.strategy_c === "object") {
    Object.assign(_topicSegConfig.strategy_c, data.strategy_c);
  }
  if (data.strategy_d && typeof data.strategy_d === "object") {
    Object.assign(_topicSegConfig.strategy_d, data.strategy_d);
  }
  return ok({ ok: true, updated: Object.keys(data), message: "配置已更新" });
}

function handleBackfillStart() {
  if (_backfillState.status === "running") return err("回填任务已在运行中");
  _backfillState = {
    status: "running", processed: 0, total: 1230, errors: 0,
    job_id: `bf_${Date.now()}`, started_at: Date.now(),
  };
  // Simulate async progress
  const iv = setInterval(() => {
    if (_backfillState.status !== "running") { clearInterval(iv); return; }
    _backfillState.processed += 100;
    if (_backfillState.processed >= _backfillState.total) {
      _backfillState.processed = _backfillState.total;
      _backfillState.status = "completed";
      clearInterval(iv);
    }
  }, 2000);
  return ok({ job_id: _backfillState.job_id, message: "回填任务已启动" });
}

function handleBackfillStatus() {
  return ok(_backfillState);
}

// ---- v1.0.0+ new subsystem handlers ----

function handleJargonCandidates(params: Record<string, string>): ApiResponse {
  const groupId = params.group_id;
  let items = [...JARGON_CANDIDATES];
  if (groupId) items = items.filter((c) => c.group_id === groupId);
  const limit = parseInt(params.limit ?? "20", 10);
  return ok({ candidates: items.slice(0, limit), total: items.length, group_id: groupId ?? "" });
}

function handleJargonMeanings(params: Record<string, string>): ApiResponse {
  const groupId = params.group_id;
  const confirmedOnly = params.confirmed_only !== "false";
  let items = [...JARGON_MEANINGS];
  if (groupId) items = items.filter((m) => m.group_id === groupId);
  if (confirmedOnly) items = items.filter((m) => m.is_confirmed);
  return ok({ meanings: items, total: items.length, group_id: groupId ?? "" });
}

function handleJargonStats(params: Record<string, string>): ApiResponse {
  const groupId = params.group_id ?? "group_001";
  const candidates = JARGON_CANDIDATES.filter((c) => c.group_id === groupId);
  const meanings = JARGON_MEANINGS.filter((m) => m.group_id === groupId);
  const confirmed = meanings.filter((m) => m.is_confirmed);
  return ok({
    group_id: groupId,
    total_terms: meanings.length,
    candidate_count: candidates.length,
    top_candidates: candidates.slice(0, 5),
    store_total: meanings.length,
    store_confirmed: confirmed.length,
  });
}

function handleJargonConfirm(body: Record<string, unknown>): ApiResponse {
  const term = body.term as string;
  const groupId = body.group_id as string;
  const confirmed = body.confirmed !== false;
  if (!term || !groupId) return err("term and group_id required");
  const found = JARGON_MEANINGS.find((m) => m.term === term && m.group_id === groupId);
  if (found) {
    // Mock server intentionally mutates in-memory state to simulate
    // server-side persistence across requests within a session.
    found.is_confirmed = confirmed;
    found.updated_at = Date.now() / 1000;
  }
  return ok({ term, group_id: groupId, action: confirmed ? "confirmed" : "rejected", message: confirmed ? `「${term}」已确认` : `「${term}」已驳回` });
}

function handleJargonMine(body: Record<string, unknown>): ApiResponse {
  const groupId = body.group_id as string;
  if (!groupId) return err("group_id required");
  const results = JARGON_MEANINGS.filter((m) => m.group_id === groupId).slice(0, 3);
  return ok({ group_id: groupId, inferred_count: results.length, results, message: `在 ${groupId} 中发现了 ${results.length} 个黑话` });
}

function handleAffectionStatus(params: Record<string, string>): ApiResponse {
  const groupId = params.group_id ?? "group_001";
  const data = AFFECTION_DATA[groupId];
  if (!data) return err("No affection data for this group");
  return ok(data);
}

function handleSocialRelations(params: Record<string, string>): ApiResponse {
  let items = [...SOCIAL_RELATIONS];
  if (params.group_id) items = items.filter((r) => r.group_id === params.group_id);
  if (params.category && params.category !== "all") items = items.filter((r) => r.category === params.category);
  return ok({ relations: items, total: items.length });
}

function handleQualityStats(): ApiResponse {
  const scores = QUALITY_SCORES;
  const n = scores.length;
  const avg = (arr: number[]) => arr.reduce((a, b) => a + b, 0) / n;
  return ok({
    avg_overall: avg(scores.map((s) => s.overall)),
    avg_consistency: avg(scores.map((s) => s.consistency)),
    avg_coherence: avg(scores.map((s) => s.coherence)),
    avg_relevance: avg(scores.map((s) => s.relevance)),
    avg_freshness: avg(scores.map((s) => s.freshness)),
    avg_accuracy: avg(scores.map((s) => s.accuracy)),
    total_scored: 1042,
    paused: false,
    pause_reason: "",
    alert_counts: { critical: 1, high: 1, medium: 1, info: 1 },
  });
}

function handleQualityRecent(params: Record<string, string>): ApiResponse {
  const limit = parseInt(params.limit ?? "20", 10);
  return ok({ scores: QUALITY_SCORES.slice(0, limit), total_scores: QUALITY_SCORES.length });
}

function handleQualityAlerts(params: Record<string, string>): ApiResponse {
  let items = [...QUALITY_ALERTS];
  if (params.level) items = items.filter((a) => a.level === params.level);
  const limit = parseInt(params.limit ?? "50", 10);
  return ok({ alerts: items.slice(0, limit), total_alerts: QUALITY_ALERTS.length, filtered_count: items.length });
}

function handleQualityReset(): ApiResponse {
  return ok({ message: "quality scorer and alert history reset" });
}

function handleDelegationStatus(): ApiResponse {
  return ok(DELEGATION_STATUS);
}

function handleExpressionPatterns(params: Record<string, string>): ApiResponse {
  const groupId = params.group_id ?? "group_001";
  const items = EXPRESSION_PATTERNS.filter((p) => p.group_id === groupId);
  return ok({ patterns: items, total: EXPRESSION_PATTERNS.length, group_patterns: items.length, group_id: groupId });
}

function handleGroups(): ApiResponse {
  return ok({
    groups: [
      { group_id: "group_001", source: "session", message_count: 342 },
      { group_id: "group_002", source: "jargon", message_count: 198 },
    ],
    total: 2,
  });
}
