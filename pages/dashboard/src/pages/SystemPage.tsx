import { useState, useEffect, useCallback } from "react";
import { BarChart3, Database, HardDrive, RotateCw, Trash2, Download, Wrench, FileJson, FileText, Undo2, CheckSquare } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { Button } from "@/components/ui/Button";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { TopicSegmentationConfig } from "@/components/TopicSegmentationConfig";
import { QualityMonitorTab } from "@/components/system/QualityMonitorTab";
import { DelegationTab } from "@/components/system/DelegationTab";

interface SystemPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

interface BackupItem {
  name: string;
  directory?: string;
  file_count?: number;
  files?: string[];
  plugin_version?: string;
  backup_timestamp?: string;
  [key: string]: unknown;
}

interface SystemStats {
  total_memories?: number;
  active_count?: number;
  archived_count?: number;
  deleted_count?: number;
  graph_nodes?: number;
  graph_edges?: number;
  atom_count?: number;
  importance_distribution?: Record<string, number>;
  atom_types?: Record<string, number>;
  sessions?: Record<string, unknown>;
}

interface MetricsSummary {
  recall?: {
    sample_count?: number;
    avg_total_ms?: number;
    p50_total_ms?: number | null;
    p95_total_ms?: number | null;
  };
  background_tasks?: {
    tracked?: number;
    active?: number;
    completed?: number;
    failed?: number;
    cancelled?: number;
    failed_tasks?: Array<{
      name?: string;
      error?: string;
      message?: string;
      suggestion?: string;
    }>;
    schedulers?: {
      backfill?: {
        job_id?: string | null;
        status?: string;
        errors?: number;
        last_error?: string | null;
        started_at?: number | null;
        completed_at?: number | null;
        cancelled_at?: number | null;
        last_finished_at?: number | null;
        retry_count?: number;
        suggestion?: string;
      };
      decay?: {
        check_hour?: number;
        check_minute?: number;
        next_run_in_seconds?: number | null;
        last_decay_date?: string | null;
        last_completed_at?: number | null;
        retry_count?: number;
        startup_error?: string;
        startup_message?: string;
        suggestion?: string;
      };
    };
  };
  provider?: {
    status?: string;
    attempts?: number;
    max_attempts?: number;
    missing_provider?: string[];
  };
  index?: {
    validator_available?: boolean;
    last_rebuild_success?: boolean;
    last_rebuild_errors?: number;
    last_rebuild_total?: number;
    last_rebuild_duration_seconds?: number;
  };
  write_coordinator?: {
    operations_total?: number;
    lock_retries_total?: number;
    failures_total?: number;
    last_error?: string | null;
  };
  prometheus?: {
    available?: boolean;
    collector_count?: number;
  };
}

function formatMs(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)} ms` : "--";
}

function formatSeconds(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)} s` : "--";
}

function formatUnixSeconds(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  return new Date(value * 1000).toISOString().slice(0, 19).replace("T", " ");
}

function formatRatio(value: number | undefined, total: number | undefined): string {
  if (typeof value === "number" && typeof total === "number") return `${value} / ${total}`;
  if (typeof value === "number") return String(value);
  return "--";
}

export function SystemPage({ showToast }: SystemPageProps) {
  const { t } = useI18n();
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [backups, setBackups] = useState<BackupItem[]>([]);
  const [exportFormat, setExportFormat] = useState<"jsonl" | "markdown">("jsonl");
  const [exportProgress, setExportProgress] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [selectedBackups, setSelectedBackups] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState(false);
  // 内联确认状态（AstrBot webview 不支持 window.confirm）
  const [confirmTarget, setConfirmTarget] = useState<{ type: "delete" | "batch-delete" | "restore"; name: string } | null>(null);
  // v1.0.0+ tabs
  const [activeTab, setActiveTab] = useState<"overview" | "quality" | "delegation">("overview");
  // Dashboard 管理 (npm install / build)
  const [npmAction, setNpmAction] = useState<"idle" | "installing" | "building">("idle");
  const [npmActionType, setNpmActionType] = useState<"install" | "build">("install");
  const [npmResult, setNpmResult] = useState<{ stdout: string; stderr: string; exit_code: number; success: boolean } | null>(null);
  const [npmError, setNpmError] = useState<string | null>(null);
  const [npmConfirmAction, setNpmConfirmAction] = useState<"install" | "build" | null>(null);

  const fetchStats = useCallback(async () => {
    setLoading(true);
    try {
      const res = unwrapApiData(await apiRequest("stats"));
      setStats(res as SystemStats);
    } catch (e) {
      showToast(String(e), true);
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  const fetchBackups = useCallback(async () => {
    try {
      const data = unwrapApiData(await apiRequest("backup/list"));
      const list = (data as Record<string, unknown>)?.backups;
      setBackups(Array.isArray(list) ? (list as BackupItem[]) : []);
    } catch { /* silent */ }
  }, []);

  const fetchMetrics = useCallback(async () => {
    try {
      const data = unwrapApiData(await apiRequest("metrics/summary"));
      setMetrics(data as MetricsSummary);
    } catch (e) {
      showToast(String(e), true);
      setMetrics(null);
    }
  }, [showToast]);

  useEffect(() => {
    fetchStats();
    fetchBackups();
    fetchMetrics();
  }, [fetchStats, fetchBackups, fetchMetrics]);

  const action = async (endpoint: string, label: string) => {
    try {
      await apiRequest(endpoint, { method: "POST" });
      showToast(t("system.actionCompleted", label));
      fetchStats();
    } catch (e) {
      showToast(String(e), true);
    }
  };

  const doCreateBackup = async () => {
    try {
      const data = unwrapApiData(await apiRequest("backup/create", { method: "POST" }));
      showToast(String((data as Record<string, unknown>)?.message ?? t("system.actionCompleted", "Backup")));
      fetchBackups();
    } catch (e) {
      showToast(String(e), true);
    }
  };

  const doRestoreBackup = async (backupName: string) => {
    setConfirmTarget({ type: "restore", name: backupName });
  };

  const confirmRestore = async (backupName: string) => {
    setConfirmTarget(null);
    setRestoring(backupName);
    try {
      const data = unwrapApiData(await apiRequest("backup/restore", {
        method: "POST",
        body: { name: backupName },
      }));
      showToast(String((data as Record<string, unknown>)?.message ?? "Restored"));
    } catch (e) {
      showToast(String(e), true);
    } finally {
      setRestoring(null);
    }
  };

  const toggleSelectBackup = (name: string) => {
    setSelectedBackups((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedBackups.size === backups.length) {
      setSelectedBackups(new Set());
    } else {
      setSelectedBackups(new Set(backups.map((b) => b.name)));
    }
  };

  const doDeleteBackup = async (backupName: string) => {
    setConfirmTarget({ type: "delete", name: backupName });
  };

  const confirmDelete = async (backupName: string) => {
    setConfirmTarget(null);
    try {
      await apiRequest("backup/delete", { method: "POST", body: { name: backupName } });
      showToast(t("system.actionCompleted", "Delete"));
      setSelectedBackups((prev) => { const n = new Set(prev); n.delete(backupName); return n; });
      fetchBackups();
    } catch (e) { showToast(String(e), true); }
  };

  const doBatchDeleteBackups = async () => {
    if (selectedBackups.size === 0) return;
    setConfirmTarget({ type: "batch-delete", name: String(selectedBackups.size) });
  };

  const confirmBatchDelete = async () => {
    setConfirmTarget(null);
    const names = Array.from(selectedBackups);
    setDeleting(true);
    try {
      const data = unwrapApiData(await apiRequest("backup/batch-delete", {
        method: "POST",
        body: { names },
      }));
      showToast(String((data as Record<string, unknown>)?.message ?? t("system.actionCompleted", "Batch delete")));
      setSelectedBackups(new Set());
      fetchBackups();
    } catch (e) { showToast(String(e), true); }
    finally { setDeleting(false); }
  };

  const exportData = async (format: "jsonl" | "markdown") => {
    setExportProgress("loading");
    try {
      const data = unwrapApiData(await apiRequest("export/memories", {
        method: "POST",
        body: { format },
      }));
      const content = (data as Record<string, unknown>)?.content ?? data;
      const blob = new Blob([typeof content === "string" ? content : JSON.stringify(content, null, 2)], {
        type: format === "jsonl" ? "application/jsonl" : "text/markdown",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `memora_export_${new Date().toISOString().slice(0, 10)}.${format === "jsonl" ? "jsonl" : "md"}`;
      a.click();
      URL.revokeObjectURL(url);
      setExportProgress("done");
      showToast(t("system.actionCompleted", `Export (${format.toUpperCase()})`));
    } catch (e) {
      setExportProgress("error");
      showToast(String(e), true);
    }
  };

  const doNpmAction = async (action: "install" | "build") => {
    setNpmConfirmAction(null);
    setNpmAction(action === "install" ? "installing" : "building");
    setNpmActionType(action);
    setNpmResult(null);
    setNpmError(null);
    try {
      const endpoint = action === "install" ? "dashboard/install" : "dashboard/build";
      const data = unwrapApiData(await apiRequest(endpoint, { method: "POST" }));
      const result = data as unknown as { stdout: string; stderr: string; exit_code: number; success: boolean };
      setNpmResult(result);
      if (result.success) {
        showToast(t(action === "install" ? "system.installSuccess" : "system.buildSuccess"));
      } else {
        showToast(t("system.commandFailed", String(result.exit_code)), true);
      }
    } catch (e) {
      const msg = String(e);
      setNpmError(msg);
      showToast(msg, true);
    } finally {
      setNpmAction("idle");
    }
  };

  const s = stats ?? {};
  const m = metrics ?? {};
  const recall = m.recall ?? {};
  const backgroundTasks = m.background_tasks ?? {};
  const provider = m.provider ?? {};
  const index = m.index ?? {};
  const writeCoordinator = m.write_coordinator ?? {};
  const prometheus = m.prometheus ?? {};
  const backfillScheduler = backgroundTasks.schedulers?.backfill ?? {};
  const decayScheduler = backgroundTasks.schedulers?.decay ?? {};
  const failedTasks = Array.isArray(backgroundTasks.failed_tasks)
    ? backgroundTasks.failed_tasks
    : [];
  const schedulerSuggestions = [
    backgroundTasks.schedulers?.backfill?.suggestion
      ? {
          name: "Backfill",
          error: backgroundTasks.schedulers.backfill.last_error ?? backgroundTasks.schedulers.backfill.status,
          suggestion: backgroundTasks.schedulers.backfill.suggestion,
        }
      : null,
    backgroundTasks.schedulers?.decay?.suggestion
      ? {
          name: "Decay",
          error: backgroundTasks.schedulers.decay.startup_error,
          message: backgroundTasks.schedulers.decay.startup_message,
          suggestion: backgroundTasks.schedulers.decay.suggestion,
        }
      : null,
  ].filter(Boolean) as Array<{ name: string; error?: string | null; message?: string; suggestion: string }>;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-3">
        <h1 className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
          <BarChart3 size={18} /> {t("nav.system")}
        </h1>
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={() => action("system/rebuild", "Rebuild")}><Wrench size={14} />{t("system.rebuildIndex")}</Button>
          <Button variant="secondary" size="sm" onClick={() => action("system/purge", "Purge")}><Trash2 size={14} />{t("system.purgeDeleted")}</Button>
          <Button variant="secondary" size="sm" onClick={() => action("system/compact", "Compact")}><HardDrive size={14} />{t("system.compactDB")}</Button>
          <Button variant="secondary" size="sm" onClick={doCreateBackup}><Download size={14} />{t("system.createBackup")}</Button>
          <span className="w-px bg-[var(--color-border)]" />
          <Button variant="secondary" size="sm" onClick={() => exportData("jsonl")}><FileJson size={14} />JSONL</Button>
          <Button variant="secondary" size="sm" onClick={() => exportData("markdown")}><FileText size={14} />Markdown</Button>
        </div>
      </header>

      {/* Tab bar */}
      <div className="flex items-center gap-1 border-b border-[var(--color-border)] px-6 shrink-0">
        {([["overview", t("nav.system")], ["quality", t("quality.title")], ["delegation", t("delegation.title")]] as [typeof activeTab, string][]).map(([tabKey, label]) => (
          <button
            key={tabKey}
            onClick={() => setActiveTab(tabKey)}
            className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-colors ${
              activeTab === tabKey ? "border-[var(--color-accent)] text-[var(--color-accent)]" : "border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-auto p-6 space-y-6">
        {activeTab === "overview" && (<>
        {loading && !stats && <p className="text-center text-sm text-[var(--text-tertiary)]">{t("table.loading")}</p>}

        {/* Stat Cards */}
        <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
          {[{ label: t("system.chartTotal"), value: s.total_memories }, { label: t("system.chartActive"), value: s.active_count },
            { label: t("system.chartArchived"), value: s.archived_count }, { label: t("system.chartDeleted"), value: s.deleted_count },
            { label: t("system.chartGraphNodes"), value: s.graph_nodes }, { label: t("system.chartAtoms"), value: s.atom_count }].map((item) => (
            <div key={item.label} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
              <div className="text-2xl font-bold tabular-nums text-[var(--text-primary)]">{item.value ?? "--"}</div>
              <div className="text-xs text-[var(--text-tertiary)] mt-1">{item.label}</div>
            </div>
          ))}
        </div>

        {/* Runtime observability */}
        <section className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-semibold">{t("system.runtimeObservability")}</h3>
            {writeCoordinator.last_error && (
              <span className="rounded-md bg-[var(--color-danger)]/10 px-2 py-1 text-xs text-[var(--color-danger)]">
                {String(writeCoordinator.last_error)}
              </span>
            )}
          </div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 md:grid-cols-4">
            {[
              { label: t("system.recallP95"), value: formatMs(recall.p95_total_ms) },
              { label: t("system.recallP50"), value: formatMs(recall.p50_total_ms) },
              { label: t("system.recallSamples"), value: recall.sample_count ?? "--" },
              { label: t("system.backgroundActive"), value: backgroundTasks.active ?? "--" },
              { label: t("system.backgroundFailures"), value: backgroundTasks.failed ?? "--" },
              { label: t("system.backfillStatus"), value: backfillScheduler.status ?? "--" },
              { label: t("system.backfillRetries"), value: backfillScheduler.retry_count ?? "--" },
              { label: t("system.backfillFinished"), value: formatUnixSeconds(backfillScheduler.last_finished_at) },
              { label: t("system.decayNextRun"), value: formatSeconds(decayScheduler.next_run_in_seconds) },
              { label: t("system.decayLastDate"), value: decayScheduler.last_decay_date ?? "--" },
              { label: t("system.decayRetries"), value: decayScheduler.retry_count ?? "--" },
              { label: t("system.providerStatus"), value: provider.status ?? "--" },
              { label: t("system.providerAttempts"), value: formatRatio(provider.attempts, provider.max_attempts) },
              { label: t("system.indexRebuildErrors"), value: formatRatio(index.last_rebuild_errors, index.last_rebuild_total) },
              { label: t("system.indexRebuildDuration"), value: formatSeconds(index.last_rebuild_duration_seconds) },
              { label: t("system.writeFailures"), value: writeCoordinator.failures_total ?? "--" },
              { label: t("system.lockRetries"), value: writeCoordinator.lock_retries_total ?? "--" },
              { label: t("system.writeOperations"), value: writeCoordinator.operations_total ?? "--" },
              { label: t("system.prometheusCollectors"), value: prometheus.collector_count ?? "--" },
            ].map((item) => (
              <div key={item.label} className="min-w-0">
                <div className="text-lg font-semibold tabular-nums text-[var(--text-primary)]">{item.value}</div>
                <div className="mt-1 truncate text-xs text-[var(--text-tertiary)]">{item.label}</div>
              </div>
            ))}
          </div>
          {(failedTasks.length > 0 || schedulerSuggestions.length > 0) && (
            <div className="mt-5 border-t border-[var(--color-border)] pt-4">
              <h4 className="mb-3 text-xs font-semibold text-[var(--text-secondary)]">
                {t("system.recoverySuggestions")}
              </h4>
              <div className="space-y-3">
                {failedTasks.map((task, index) => (
                  <div key={`${task.name ?? "task"}-${index}`} className="text-xs">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-[var(--text-primary)]">{task.name ?? "task"}</span>
                      {task.error && <span className="text-[var(--color-danger)]">{task.error}</span>}
                      {task.message && <span className="text-[var(--text-tertiary)]">{task.message}</span>}
                    </div>
                    {task.suggestion && (
                      <p className="mt-1 text-[var(--text-secondary)]">{task.suggestion}</p>
                    )}
                  </div>
                ))}
                {schedulerSuggestions.map((item) => (
                  <div key={item.name} className="text-xs">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-[var(--text-primary)]">{item.name}</span>
                      {item.error && <span className="text-[var(--color-danger)]">{item.error}</span>}
                      {item.message && <span className="text-[var(--text-tertiary)]">{item.message}</span>}
                    </div>
                    <p className="mt-1 text-[var(--text-secondary)]">{item.suggestion}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>

        {/* Charts */}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* Importance Distribution */}
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <h3 className="text-sm font-semibold mb-4">{t("system.importanceDist")}</h3>
            <div className="space-y-2">
              {(s.importance_distribution
                ? Object.entries(s.importance_distribution)
                : Array.from({ length: 10 }, (_, i) => [`${i + 1}`, 0])
              ).map(([key, value]) => (
                <div key={key} className="flex items-center gap-3">
                  <span className="w-8 text-right text-xs text-[var(--text-tertiary)]">{key}</span>
                  <div className="h-5 flex-1 rounded-md bg-[var(--color-surface-secondary)]">
                    <div
                      className="h-5 rounded-md bg-[var(--color-accent)] transition-all duration-500"
                      style={{ width: `${Math.min(100, (Number(value) / Math.max(1, Number(s.total_memories ?? 1))) * 100 * 3)}%` }}
                    />
                  </div>
                  <span className="w-10 text-xs tabular-nums text-[var(--text-tertiary)]">{value}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Atom Types */}
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <h3 className="text-sm font-semibold mb-4">{t("system.atomTypes")}</h3>
            <div className="space-y-2">
              {(s.atom_types ? Object.entries(s.atom_types) : []).map(([key, value]) => (
                <div key={key} className="flex items-center gap-3">
                  <span className="w-24 text-xs text-[var(--text-secondary)] truncate">{key}</span>
                  <div className="h-5 flex-1 rounded-md bg-[var(--color-surface-secondary)]">
                    <div
                      className="h-5 rounded-md bg-[var(--color-accent-secondary)] transition-all duration-500"
                      style={{ width: `${Math.min(100, (Number(value) / Math.max(1, Number(s.atom_count ?? 1))) * 100 * 2)}%` }}
                    />
                  </div>
                  <span className="w-10 text-xs tabular-nums text-[var(--text-tertiary)]">{value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Sessions */}
        {s.sessions && Object.keys(s.sessions).length > 0 && (
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <h3 className="text-sm font-semibold mb-3">{t("system.activeSessions")} ({Object.keys(s.sessions).length})</h3>
            <div className="space-y-1.5">
              {Object.entries(s.sessions).slice(0, 20).map(([id, info]) => (
                <div key={id} className="flex items-center justify-between rounded-lg px-3 py-2 text-sm hover:bg-[var(--color-surface-secondary)]">
                  <span className="font-mono text-xs text-[var(--text-secondary)]">{id}</span>
                  <span className="text-xs text-[var(--text-tertiary)]">{typeof info === "object" ? JSON.stringify(info) : String(info)}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Legacy Backfill */}
        <TopicSegmentationConfig showToast={showToast} />

        {/* Backups */}
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          {/* Inline confirmation bar (replaces window.confirm in AstrBot webview) */}
          {confirmTarget && (
            <div className="mb-3 flex items-center justify-between rounded-lg bg-[var(--color-warning)]/10 border border-[var(--color-warning)]/30 px-4 py-2.5">
              <span className="text-sm text-[var(--text-primary)]">
                {confirmTarget.type === "delete" && t("system.deleteConfirm", confirmTarget.name)}
                {confirmTarget.type === "batch-delete" && t("system.batchDeleteConfirm", confirmTarget.name)}
                {confirmTarget.type === "restore" && t("system.restoreConfirm", confirmTarget.name)}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant={confirmTarget.type === "restore" ? "secondary" : "destructive"}
                  size="sm"
                  onClick={() => {
                    if (confirmTarget.type === "delete") confirmDelete(confirmTarget.name);
                    else if (confirmTarget.type === "batch-delete") confirmBatchDelete();
                    else if (confirmTarget.type === "restore") confirmRestore(confirmTarget.name);
                  }}
                >
                  {confirmTarget.type === "delete" ? t("detail.delete")
                   : confirmTarget.type === "batch-delete" ? t("filter.deleteSelected")
                   : t("system.restoreBackup")}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setConfirmTarget(null)}>
                  {t("common.cancel")}
                </Button>
              </div>
            </div>
          )}
          <h3 className="text-sm font-semibold mb-3 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span>{t("system.versionBackups")} {backups.length > 0 && `(${backups.length})`}</span>
              {backups.length > 0 && (
                <button
                  onClick={toggleSelectAll}
                  className="text-xs text-[var(--color-accent)] hover:underline"
                >
                  {selectedBackups.size === backups.length ? t("select.deselectAll") : t("select.selectAll")}
                </button>
              )}
              {selectedBackups.size > 0 && (
                <span className="text-xs text-[var(--text-tertiary)]">{t("select.selected", String(selectedBackups.size))}</span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {selectedBackups.size > 0 && (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={doBatchDeleteBackups}
                  disabled={deleting}
                >
                  {deleting ? (
                    <><RotateCw size={12} className="animate-spin mr-1" />...</>
                  ) : (
                    <><Trash2 size={12} className="mr-1" />{t("filter.deleteSelected")} ({selectedBackups.size})</>
                  )}
                </Button>
              )}
              <Button variant="secondary" size="sm" onClick={doCreateBackup}><Download size={14} />{t("system.createBackup")}</Button>
            </div>
          </h3>
          {backups.length === 0 ? (
            <p className="text-xs text-[var(--text-tertiary)] py-4 text-center">{t("table.noData")}</p>
          ) : (
            <div className="space-y-1">
              {backups.map((b, i) => {
                const isSelected = selectedBackups.has(b.name);
                return (
                <div
                  key={i}
                  className={`flex items-center justify-between rounded-lg px-3 py-2 text-sm transition-colors ${
                    isSelected ? "bg-[var(--color-accent)]/10" : "hover:bg-[var(--color-surface-secondary)]"
                  }`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleSelectBackup(b.name)}
                      className="h-4 w-4 rounded border-[var(--color-border)] accent-[var(--color-accent)] cursor-pointer shrink-0"
                    />
                    <Database size={14} className="text-[var(--text-tertiary)] shrink-0" />
                    <div className="min-w-0">
                      <span className="font-medium text-[var(--text-primary)]">{b.name}</span>
                      {b.backup_timestamp && (
                        <span className="ml-2 text-xs text-[var(--text-tertiary)]">
                          {new Date(b.backup_timestamp).toLocaleString()}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-[var(--text-tertiary)]">{b.file_count ?? (b.files?.length ?? 0)} files</span>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => doRestoreBackup(b.name)}
                      disabled={restoring === b.name}
                    >
                      {restoring === b.name ? (
                        <><RotateCw size={12} className="animate-spin mr-1" />...</>
                      ) : (
                        <><Undo2 size={12} className="mr-1" />{t("system.restoreBackup")}</>
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => doDeleteBackup(b.name)}
                    >
                      <Trash2 size={12} className="text-[var(--color-danger)]" />
                    </Button>
                  </div>
                </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Export */}
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          <h3 className="text-sm font-semibold mb-4 flex items-center gap-2"><FileJson size={16} />{t("system.exportMemories")}</h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Select value={exportFormat} onValueChange={(v) => v && setExportFormat(v as "jsonl" | "markdown")}>
              <SelectTrigger className="w-36"><span>{exportFormat === "jsonl" ? "JSONL" : "Markdown"}</span></SelectTrigger>
              <SelectContent>
                <SelectItem value="jsonl">JSONL</SelectItem>
                <SelectItem value="markdown">Markdown</SelectItem>
              </SelectContent>
            </Select>
            <Button
              size="sm"
              onClick={() => exportData(exportFormat)}
              disabled={exportProgress === "loading"}
            >
              {exportProgress === "loading" ? (
                <><RotateCw size={14} className="animate-spin mr-1" />Exporting...</>
              ) : exportProgress === "done" ? (
                "Exported ✓"
              ) : exportProgress === "error" ? (
                "Retry"
              ) : (
                <><Download size={14} className="mr-1" />Export</>
              )}
            </Button>
          </div>
        </div>

        {/* Dashboard 管理 (npm install / build) */}
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          <h3 className="text-sm font-semibold mb-4 flex items-center gap-2"><Wrench size={16} />{t("system.dashboardManagement")}</h3>
          {npmConfirmAction && (
            <div className="mb-3 flex items-center justify-between rounded-lg bg-[var(--color-warning)]/10 border border-[var(--color-warning)]/30 px-4 py-2.5">
              <span className="text-sm text-[var(--text-primary)]">
                {npmConfirmAction === "install" ? t("system.installConfirm") : t("system.buildConfirm")}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => doNpmAction(npmConfirmAction)}
                >
                  {npmConfirmAction === "install" ? t("system.installDeps") : t("system.buildPage")}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setNpmConfirmAction(null)}>
                  {t("common.cancel")}
                </Button>
              </div>
            </div>
          )}
          <div className="flex gap-3 mb-4">
            <Button
              size="sm"
              onClick={() => setNpmConfirmAction("install")}
              disabled={npmAction !== "idle"}
            >
              {npmAction === "installing" ? (
                <><RotateCw size={14} className="animate-spin mr-1" />{t("system.installing")}</>
              ) : (
                <><Download size={14} className="mr-1" />{t("system.installDeps")}</>
              )}
            </Button>
            <Button
              size="sm"
              onClick={() => setNpmConfirmAction("build")}
              disabled={npmAction !== "idle"}
            >
              {npmAction === "building" ? (
                <><RotateCw size={14} className="animate-spin mr-1" />{t("system.building")}</>
              ) : (
                <><Wrench size={14} className="mr-1" />{t("system.buildPage")}</>
              )}
            </Button>
          </div>
          {npmError && (
            <div className="mb-3 rounded-lg bg-[var(--color-danger)]/10 border border-[var(--color-danger)]/30 px-4 py-2.5">
              <p className="text-sm text-[var(--color-danger)]">{npmError}</p>
            </div>
          )}
          {npmResult && (
            <div className="space-y-2">
              <div className={`text-sm font-medium ${npmResult.success ? "text-[var(--color-success)]" : "text-[var(--color-danger)]"}`}>
                {npmResult.success
                  ? (npmActionType === "install" ? t("system.installSuccess") : t("system.buildSuccess"))
                  : t("system.commandFailed", String(npmResult.exit_code))}
              </div>
              <details className="text-xs">
                <summary className="cursor-pointer text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]">
                  {t("system.outputLog")}
                </summary>
                <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-[var(--color-surface-secondary)] p-3 text-xs text-[var(--text-secondary)] whitespace-pre-wrap break-all">
                  {npmResult.stdout}
                  {npmResult.stderr ? `\n--- stderr ---\n${npmResult.stderr}` : ""}
                </pre>
              </details>
            </div>
          )}
        </div>
        </>)} {/* end overview tab */}

        {/* Quality Monitor Tab */}
        {activeTab === "quality" && <QualityMonitorTab showToast={showToast} />}

        {/* Delegation Tab */}
        {activeTab === "delegation" && <DelegationTab showToast={showToast} />}
      </div>
    </div>
  );
}
