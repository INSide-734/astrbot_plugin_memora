import { useState, useEffect, useCallback, useRef } from "react";
import { BarChart3, Database, HardDrive, RotateCw, Trash2, Download, Wrench, FileJson, FileText, Undo2, CheckSquare } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { dashboardLocale, formatDashboardDate, formatDashboardNumber, translateEnum } from "@/lib/i18n";
import { Button } from "@/components/ui/Button";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import { TopicSegmentationConfig } from "@/components/TopicSegmentationConfig";
import { QualityMonitorTab } from "@/components/system/QualityMonitorTab";
import { DelegationTab } from "@/components/system/DelegationTab";
import { UpdateNotice } from "@/components/system/UpdateNotice";
import { PageContent, PageFrame, PageHeader } from "@/components/layout/PageLayout";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Checkbox } from "@/components/ui/checkbox";
import { selectionStateVariants } from "@/components/ui/selection-state";
import { cn } from "@/lib/utils";
import { ActionConfirmDialog } from "@/components/editing/ActionConfirmDialog";

interface SystemPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

interface BackupItem {
  name: string;
  backup_type?: "manual" | "scheduled" | "version_change" | "pre_restore" | string;
  created_at?: string;
  file_count?: number;
  files?: string[];
  plugin_version?: string;
  manifest_version?: number;
  status?: string;
  integrity?: "verified" | "legacy_unverified" | "invalid" | "incompatible" | string;
  total_size_bytes?: number;
  warning_codes?: string[];
  can_restore?: boolean;
  can_hot_restore?: boolean;
  backup_timestamp?: string;
}

interface RestoreStatus {
  operation_id?: string;
  source_backup_name?: string;
  restore_status?: string;
  apply_mode?: "reload" | "restart" | string;
  reload_scheduled?: boolean;
  requires_manual_restart?: boolean;
  reason_code?: string | null;
  pre_restore_backup_name?: string | null;
}

const ACTIVE_RESTORE_STATUSES = new Set([
  "staged",
  "reload_scheduled",
  "applying",
  "validating",
  "rollback_pending",
  "rolling_back",
]);

interface SystemStats {
  total_memories?: number;
  active_count?: number;
  dormant_count?: number;
  archived_count?: number;
  deleted_count?: number;
  graph_nodes?: number;
  graph_edges?: number;
  atom_count?: number;
  importance_distribution?: Record<string, number>;
  atom_types?: Record<string, number>;
  sessions?: Record<string, unknown>;
}

interface PendingSystemOperation {
  readonly kind: "restore" | "delete" | "batch-delete" | "purge" | "install" | "build" | "reset-quality";
  readonly title: string;
  readonly description: string;
  readonly endpoint: string;
  readonly body?: Readonly<Record<string, unknown>>;
  readonly selection?: readonly string[];
  readonly successLabel: string;
  readonly destructive: boolean;
  readonly actionLabel: string;
  readonly pendingLabel: string;
}

function readableError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function snapshotOperation(operation: PendingSystemOperation): PendingSystemOperation {
  const body = operation.body
    ? Object.freeze(Object.fromEntries(Object.entries(operation.body).map(([key, value]) => [
        key,
        Array.isArray(value) ? Object.freeze([...value]) : value,
      ])))
    : undefined;
  return Object.freeze({
    ...operation,
    body,
    selection: operation.selection ? Object.freeze([...operation.selection]) : undefined,
  });
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

function formatMs(value: number | null | undefined, locale: string): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${formatDashboardNumber(value, locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} ms`
    : "--";
}

function formatSeconds(value: number | null | undefined, locale: string): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${formatDashboardNumber(value, locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} s`
    : "--";
}

function formatUnixSeconds(value: number | null | undefined, locale: string): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  return new Date(value * 1000).toLocaleString(locale);
}

function formatRatio(value: number | undefined, total: number | undefined): string {
  if (typeof value === "number" && typeof total === "number") return `${value} / ${total}`;
  if (typeof value === "number") return String(value);
  return "--";
}

function formatBytes(value: number | undefined, locale: string): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "--";
  if (value < 1024) return `${formatDashboardNumber(value, locale)} B`;
  if (value < 1024 * 1024) {
    return `${formatDashboardNumber(value / 1024, locale, { maximumFractionDigits: 1 })} KB`;
  }
  return `${formatDashboardNumber(value / 1024 / 1024, locale, { maximumFractionDigits: 1 })} MB`;
}

export function SystemPage({ showToast }: SystemPageProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const formatRuntimeStatus = (value: unknown): string => {
    const rawValue = String(value ?? "").trim();
    return rawValue ? translateEnum(t, "runtime.status", rawValue) : "--";
  };
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [backups, setBackups] = useState<BackupItem[]>([]);
  const [backupCapabilities, setBackupCapabilities] = useState({ hot_reload: false });
  const [pendingRestore, setPendingRestore] = useState<RestoreStatus | null>(null);
  const [exportFormat, setExportFormat] = useState<"jsonl" | "markdown">("jsonl");
  const [exportProgress, setExportProgress] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [loading, setLoading] = useState(false);
  const [selectedBackups, setSelectedBackups] = useState<Set<string>>(new Set());
  const [pendingOperation, setPendingOperation] = useState<PendingSystemOperation | null>(null);
  const [operationPending, setOperationPending] = useState(false);
  const [operationError, setOperationError] = useState<string | null>(null);
  const operationPendingRef = useRef(false);
  const restorePollTimerRef = useRef<number | null>(null);
  const restorePollGenerationRef = useRef(0);
  const restorePollingOperationRef = useRef<string | null>(null);
  const [qualityRefreshToken, setQualityRefreshToken] = useState(0);
  // v1.0.0+ tabs
  const [activeTab, setActiveTab] = useState<"overview" | "quality" | "delegation">("overview");
  // Dashboard 管理 (npm install / build)
  const [npmAction, setNpmAction] = useState<"idle" | "installing" | "building">("idle");
  const [npmActionType, setNpmActionType] = useState<"install" | "build">("install");
  const [npmResult, setNpmResult] = useState<{ stdout: string; stderr: string; exit_code: number; success: boolean } | null>(null);
  const [npmError, setNpmError] = useState<string | null>(null);

  const fetchStats = useCallback(async () => {
    setLoading(true);
    try {
      const res = unwrapApiData(await apiRequest("stats"));
      setStats(res as SystemStats);
    } catch (e) {
      showToast(readableError(e), true);
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  const fetchBackups = useCallback(async () => {
    try {
      const data = unwrapApiData(await apiRequest("backup/list"));
      const payload = data as Record<string, unknown>;
      const list = payload?.backups;
      setBackups(Array.isArray(list) ? (list as BackupItem[]) : []);
      const capabilities = payload?.capabilities;
      setBackupCapabilities({
        hot_reload: Boolean(
          capabilities && typeof capabilities === "object" &&
          (capabilities as Record<string, unknown>).hot_reload === true,
        ),
      });
      const pending = payload?.pending_restore;
      const restore = pending && typeof pending === "object"
        ? pending as RestoreStatus
        : null;
      setPendingRestore(
        restore && ACTIVE_RESTORE_STATUSES.has(String(restore.restore_status ?? ""))
          ? restore
          : null,
      );
    } catch { /* silent */ }
  }, []);

  const fetchMetrics = useCallback(async () => {
    try {
      const data = unwrapApiData(await apiRequest("metrics/summary"));
      setMetrics(data as MetricsSummary);
    } catch (e) {
      showToast(readableError(e), true);
      setMetrics(null);
    }
  }, [showToast]);

  useEffect(() => {
    fetchStats();
    fetchBackups();
    fetchMetrics();
  }, [fetchStats, fetchBackups, fetchMetrics]);

  useEffect(() => () => {
    restorePollGenerationRef.current += 1;
    if (restorePollTimerRef.current !== null) {
      window.clearTimeout(restorePollTimerRef.current);
      restorePollTimerRef.current = null;
    }
  }, []);

  const runDirectAction = async (endpoint: string, labelKey: string) => {
    try {
      const data = unwrapApiData(await apiRequest(endpoint, { method: "POST" }));
      showToast(String((data as Record<string, unknown>)?.message ?? t("system.actionCompleted", t(labelKey))));
      void fetchStats();
    } catch (error) {
      showToast(readableError(error), true);
    }
  };

  const requestPurge = () => {
    const label = t("system.purgeDeleted");
    openOperation({ kind: "purge", title: label, description: label, endpoint: "system/purge", successLabel: t("system.actionCompleted", label), destructive: true, actionLabel: label, pendingLabel: `${label}…` });
  };

  const requestQualityReset = () => {
    const label = t("quality.reset");
    openOperation({ kind: "reset-quality", title: label, description: t("toast.qualityReset"), endpoint: "quality/reset", successLabel: t("toast.qualityReset"), destructive: true, actionLabel: label, pendingLabel: `${label}…` });
  };

  const openOperation = (operation: PendingSystemOperation) => {
    setOperationError(null);
    setPendingOperation(snapshotOperation(operation));
  };

  const stopRestorePolling = useCallback(() => {
    restorePollGenerationRef.current += 1;
    restorePollingOperationRef.current = null;
    if (restorePollTimerRef.current !== null) {
      window.clearTimeout(restorePollTimerRef.current);
      restorePollTimerRef.current = null;
    }
  }, []);

  const pollRestoreStatus = useCallback((operationId: string) => {
    stopRestorePolling();
    restorePollingOperationRef.current = operationId;
    const generation = restorePollGenerationRef.current;
    const deadline = Date.now() + 60_000;
    const terminalStatuses = new Set([
      "succeeded",
      "failed_before_apply",
      "rolled_back",
      "cancelled",
    ]);

    const poll = async () => {
      if (restorePollGenerationRef.current !== generation) return;
      try {
        const data = unwrapApiData(await apiRequest(
          `backup/status?operation_id=${encodeURIComponent(operationId)}`,
          { retries: 0 },
        ));
        const status = data as RestoreStatus;
        setPendingRestore(status);
        const state = String(status.restore_status ?? "");
        if (terminalStatuses.has(state)) {
          stopRestorePolling();
          await fetchBackups();
          showToast(t(`system.restoreStatus.${state}`), state !== "succeeded");
          return;
        }
      } catch {
        // 插件热重载窗口中的短暂断连属于预期状态，继续有界轮询。
      }

      if (Date.now() >= deadline) {
        stopRestorePolling();
        showToast(t("system.restoreStillRunning"), true);
        return;
      }
      restorePollTimerRef.current = window.setTimeout(() => void poll(), 1000);
    };

    void poll();
  }, [fetchBackups, showToast, stopRestorePolling, t]);

  useEffect(() => {
    const operationId = pendingRestore?.operation_id;
    const state = String(pendingRestore?.restore_status ?? "");
    if (
      operationId &&
      ["reload_scheduled", "applying", "validating", "rollback_pending", "rolling_back"].includes(state) &&
      restorePollingOperationRef.current !== operationId
    ) {
      pollRestoreStatus(operationId);
    }
  }, [pendingRestore, pollRestoreStatus]);

  const cancelPendingRestore = async () => {
    const operationId = pendingRestore?.operation_id;
    if (!operationId || operationPendingRef.current) return;
    operationPendingRef.current = true;
    setOperationPending(true);
    try {
      const data = unwrapApiData(await apiRequest("backup/restore/cancel", {
        method: "POST",
        body: { operation_id: operationId },
        retries: 0,
      }));
      stopRestorePolling();
      setPendingRestore(null);
      showToast(String((data as Record<string, unknown>)?.message ?? t("system.restoreStatus.cancelled")));
      await Promise.all([fetchBackups(), fetchStats()]);
    } catch (error) {
      showToast(readableError(error), true);
    } finally {
      operationPendingRef.current = false;
      setOperationPending(false);
    }
  };

  const executeOperation = async () => {
    const operation = pendingOperation;
    if (!operation || operationPendingRef.current) return;
    operationPendingRef.current = true;
    setOperationPending(true);
    setOperationError(null);
    const npmKind = operation.kind === "install" || operation.kind === "build" ? operation.kind : null;
    if (npmKind) {
      setNpmAction(npmKind === "install" ? "installing" : "building");
      setNpmActionType(npmKind);
      setNpmResult(null);
      setNpmError(null);
    }
    try {
      const data = unwrapApiData(await apiRequest(operation.endpoint, { method: "POST", body: operation.body }));
      const resultData = data as Record<string, unknown>;
      let refreshAfterOperation = true;
      if (npmKind) {
        const result = data as { stdout: string; stderr: string; exit_code: number; success: boolean };
        setNpmResult(result);
        if (!result.success) {
          const message = t("system.commandFailed", String(result.exit_code));
          setOperationError(message);
          setNpmError(message);
          showToast(message, true);
          return;
        }
        showToast(operation.successLabel);
      } else if (operation.kind === "restore" && typeof resultData.operation_id === "string") {
        const status = resultData as RestoreStatus;
        const operationId = resultData.operation_id;
        setPendingRestore(status);
        refreshAfterOperation = false;
        if (
          status.apply_mode === "reload" ||
          status.reload_scheduled === true ||
          status.restore_status === "reload_scheduled"
        ) {
          pollRestoreStatus(operationId);
        } else {
          showToast(t("system.restoreManualRestart"), false);
        }
      } else {
        showToast(String(resultData?.message ?? operation.successLabel));
      }
      if (operation.kind === "delete" || operation.kind === "batch-delete") {
        const deletedNames = Array.isArray(resultData.deleted_names)
          ? resultData.deleted_names.filter((name): name is string => typeof name === "string")
          : operation.selection ?? [];
        const completed = new Set(deletedNames);
        setSelectedBackups((previous) => new Set(
          Array.from(previous).filter((name) => !completed.has(name)),
        ));
      }
      if (operation.kind === "reset-quality") {
        setQualityRefreshToken((token) => token + 1);
      }
      setPendingOperation(null);
      if (refreshAfterOperation) {
        void fetchStats();
        void fetchBackups();
      }
    } catch (error) {
      const message = readableError(error);
      setOperationError(message);
      if (npmKind) {
        setNpmError(message);
        showToast(message, true);
      }
    } finally {
      operationPendingRef.current = false;
      setOperationPending(false);
      if (npmKind) setNpmAction("idle");
    }
  };

  const doCreateBackup = async () => {
    try {
      const data = unwrapApiData(await apiRequest("backup/create", { method: "POST" }));
      showToast(String((data as Record<string, unknown>)?.message ?? t("system.actionCompleted", t("system.createBackup"))));
      fetchBackups();
    } catch (e) {
      showToast(String(e), true);
    }
  };

  const doRestoreBackup = (backup: BackupItem) => {
    const useHotReload = backupCapabilities.hot_reload && backup.can_hot_restore !== false;
    const applyMode = useHotReload ? "reload" : "restart";
    const legacyWarning = backup.integrity === "legacy_unverified"
      ? ` ${t("system.restoreLegacyWarning")}`
      : "";
    openOperation({
      kind: "restore",
      title: t("system.restoreBackup"),
      description: `${t("system.restoreConfirm", backup.name)}${legacyWarning}`,
      endpoint: "backup/restore",
      body: { name: backup.name, apply_mode: applyMode },
      successLabel: t("system.restoreSuccess"),
      destructive: true,
      actionLabel: useHotReload ? t("system.restoreAndReload") : t("system.restoreBackup"),
      pendingLabel: `${t("system.restoreBackup")}…`,
    });
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
    openOperation({ kind: "delete", title: t("common.delete"), description: t("system.deleteConfirm", backupName), endpoint: "backup/delete", body: { name: backupName }, selection: [backupName], successLabel: t("system.actionCompleted", t("common.delete")), destructive: true, actionLabel: t("detail.delete"), pendingLabel: `${t("detail.delete")}…` });
  };


  const doBatchDeleteBackups = async () => {
    if (selectedBackups.size === 0) return;
    const names = Array.from(selectedBackups);
    openOperation({ kind: "batch-delete", title: t("filter.deleteSelected"), description: t("system.batchDeleteConfirm", String(names.length)), endpoint: "backup/batch-delete", body: { names }, selection: names, successLabel: t("system.actionCompleted", t("filter.deleteSelected")), destructive: true, actionLabel: t("filter.deleteSelected"), pendingLabel: `${t("filter.deleteSelected")}…` });
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
      showToast(t("system.exportCompleted", format.toUpperCase()));
    } catch (e) {
      setExportProgress("error");
      showToast(readableError(e), true);
    }
  };

  const doNpmAction = (action: "install" | "build") => {
    openOperation({ kind: action, title: action === "install" ? t("system.installDeps") : t("system.buildPage"), description: action === "install" ? t("system.installConfirm") : t("system.buildConfirm"), endpoint: action === "install" ? "dashboard/install" : "dashboard/build", successLabel: t(action === "install" ? "system.installSuccess" : "system.buildSuccess"), destructive: false, actionLabel: action === "install" ? t("system.installDeps") : t("system.buildPage"), pendingLabel: action === "install" ? t("system.installing") : t("system.building") });
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
          name: t("system.taskBackfill"),
          error: backgroundTasks.schedulers.backfill.last_error ?? backgroundTasks.schedulers.backfill.status,
          suggestion: backgroundTasks.schedulers.backfill.suggestion,
        }
      : null,
    backgroundTasks.schedulers?.decay?.suggestion
      ? {
          name: t("system.taskDecay"),
          error: backgroundTasks.schedulers.decay.startup_error,
          message: backgroundTasks.schedulers.decay.startup_message,
          suggestion: backgroundTasks.schedulers.decay.suggestion,
        }
      : null,
  ].filter(Boolean) as Array<{ name: string; error?: string | null; message?: string; suggestion: string }>;

  return (
    <PageFrame variant="standard" aria-label={t("nav.system")}>
      <PageHeader
        title={t("nav.system")}
        description={t("system.subtitle")}
        icon={<BarChart3 className="size-4" />}
        actions={<div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={() => void runDirectAction("system/rebuild", "system.rebuildIndex")}><Wrench size={14} />{t("system.rebuildIndex")}</Button>
          <Button variant="secondary" size="sm" onClick={requestPurge} disabled={operationPending}><Trash2 size={14} />{t("system.purgeDeleted")}</Button>
          <Button variant="secondary" size="sm" onClick={() => void runDirectAction("system/compact", "system.compactDB")}><HardDrive size={14} />{t("system.compactDB")}</Button>
          <Button variant="secondary" size="sm" onClick={doCreateBackup}><Download size={14} />{t("system.createBackup")}</Button>
          <span className="w-px bg-border" />
          <Button variant="secondary" size="sm" onClick={() => exportData("jsonl")}><FileJson size={14} />JSONL</Button>
          <Button variant="secondary" size="sm" onClick={() => exportData("markdown")}><FileText size={14} />Markdown</Button>
        </div>}
      />

      {pendingOperation && <ActionConfirmDialog
        open
        title={pendingOperation.title}
        description={pendingOperation.description}
        cancelLabel={t("common.cancel")}
        actionLabel={pendingOperation.actionLabel}
        pendingLabel={pendingOperation.pendingLabel}
        destructive={pendingOperation.destructive}
        pending={operationPending}
        error={operationError}
        onCancel={() => setPendingOperation(null)}
        onConfirm={executeOperation}
      />}

      <Tabs
        value={activeTab}
        onValueChange={(value) => setActiveTab(value as typeof activeTab)}
        className="min-h-0 flex-1 gap-0 overflow-hidden"
      >
      <TabsList variant="line" className="h-11 w-full shrink-0 justify-start overflow-x-auto border-b px-4 sm:px-5 lg:px-6">
        {([["overview", t("nav.system")], ["quality", t("quality.title")], ["delegation", t("delegation.title")]] as [typeof activeTab, string][]).map(([tabKey, label]) => (
          <TabsTrigger
            key={tabKey}
            value={tabKey}
            className="h-10 flex-none px-3 text-xs"
          >
            {label}
          </TabsTrigger>
        ))}
      </TabsList>

      <PageContent width="full" role="tabpanel" className="space-y-6">
        {activeTab === "overview" && (<>
        {loading && !stats && <p className="text-center text-sm text-[var(--text-tertiary)]">{t("table.loading")}</p>}

        {/* Stat Cards */}
        <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-7">
          {[{ label: t("system.chartTotal"), value: s.total_memories }, { label: t("system.chartActive"), value: s.active_count },
            { label: t("system.chartDormant"), value: s.dormant_count }, { label: t("system.chartArchived"), value: s.archived_count }, { label: t("system.chartDeleted"), value: s.deleted_count },
            { label: t("system.chartGraphNodes"), value: s.graph_nodes }, { label: t("system.chartAtoms"), value: s.atom_count }].map((item) => (
              <div key={item.label} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
              <div className="text-2xl font-bold tabular-nums text-[var(--text-primary)]">{item.value ?? "--"}</div>
              <div className="text-xs text-[var(--text-tertiary)] mt-1">{item.label}</div>
            </div>
          ))}
        </div>

        <UpdateNotice showToast={showToast} />

        {/* Runtime observability */}
          <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
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
              { label: t("system.recallP95"), value: formatMs(recall.p95_total_ms, locale) },
              { label: t("system.recallP50"), value: formatMs(recall.p50_total_ms, locale) },
              { label: t("system.recallSamples"), value: recall.sample_count ?? "--" },
              { label: t("system.backgroundActive"), value: backgroundTasks.active ?? "--" },
              { label: t("system.backgroundFailures"), value: backgroundTasks.failed ?? "--" },
              { label: t("system.backfillStatus"), value: formatRuntimeStatus(backfillScheduler.status) },
              { label: t("system.backfillRetries"), value: backfillScheduler.retry_count ?? "--" },
              { label: t("system.backfillFinished"), value: formatUnixSeconds(backfillScheduler.last_finished_at, locale) },
              { label: t("system.decayNextRun"), value: formatSeconds(decayScheduler.next_run_in_seconds, locale) },
              {
                label: t("system.decayLastDate"),
                value: decayScheduler.last_decay_date
                  ? formatDashboardDate(decayScheduler.last_decay_date, locale)
                  : "--",
              },
              { label: t("system.decayRetries"), value: decayScheduler.retry_count ?? "--" },
              { label: t("system.providerStatus"), value: formatRuntimeStatus(provider.status) },
              { label: t("system.providerAttempts"), value: formatRatio(provider.attempts, provider.max_attempts) },
              { label: t("system.indexRebuildErrors"), value: formatRatio(index.last_rebuild_errors, index.last_rebuild_total) },
              { label: t("system.indexRebuildDuration"), value: formatSeconds(index.last_rebuild_duration_seconds, locale) },
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
                      <span className="font-medium text-[var(--text-primary)]">{task.name ?? t("system.backgroundTask")}</span>
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
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
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
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <h3 className="text-sm font-semibold mb-4">{t("system.atomTypes")}</h3>
            <div className="space-y-2">
              {(s.atom_types ? Object.entries(s.atom_types) : []).map(([key, value]) => (
                <div key={key} className="flex items-center gap-3">
                  <span className="w-24 text-xs text-[var(--text-secondary)] truncate">{translateEnum(t, "memory.type", key)}</span>
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
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
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
        {pendingRestore && (
          <div
            role="status"
            className="flex flex-col gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">{t("system.restoreWriteProtection")}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {t("system.restoreCurrentStatus")}: {translateEnum(
                  t,
                  "system.restoreStatus",
                  pendingRestore.restore_status,
                  String(pendingRestore.restore_status ?? "--"),
                )}
                {pendingRestore.source_backup_name ? ` · ${pendingRestore.source_backup_name}` : ""}
              </p>
              {pendingRestore.requires_manual_restart && (
                <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                  {t("system.restoreManualRestart")}
                </p>
              )}
              {pendingRestore.reason_code && (
                <p className="mt-1 text-xs text-muted-foreground">
                  {translateEnum(t, "system.restoreReason", pendingRestore.reason_code, pendingRestore.reason_code)}
                </p>
              )}
            </div>
            {pendingRestore.restore_status === "staged" && pendingRestore.operation_id && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => void cancelPendingRestore()}
                disabled={operationPending}
              >
                {t("system.cancelRestore")}
              </Button>
            )}
          </div>
        )}
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          <h3 className="text-sm font-semibold mb-3 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span>{t("system.versionBackups")} {backups.length > 0 && `(${backups.length})`}</span>
              {backups.length > 0 && (
                <Button
                  variant="link"
                  size="xs"
                  onClick={toggleSelectAll}
                >
                  {selectedBackups.size === backups.length ? t("select.deselectAll") : t("select.selectAll")}
                </Button>
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
                  disabled={operationPending}
                >
                  <Trash2 size={12} className="mr-1" />{t("filter.deleteSelected")} ({selectedBackups.size})
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
                const integrity = String(b.integrity ?? "legacy_unverified");
                const restoreDisabled = operationPending || b.can_restore === false || ["invalid", "incompatible"].includes(integrity);
                const timestamp = b.created_at ?? b.backup_timestamp;
                return (
                <div
                  key={i}
                  data-state={isSelected ? "selected" : undefined}
                  className={cn(
                    "flex items-center justify-between rounded-lg px-3 py-2 text-sm",
                    selectionStateVariants({ kind: "row", selected: isSelected }),
                    !isSelected && "hover:bg-[var(--color-surface-secondary)]",
                  )}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <Checkbox
                      aria-label={`${t("select.selectAll")} ${b.name}`}
                      checked={isSelected}
                      onCheckedChange={() => toggleSelectBackup(b.name)}
                    />
                    <Database size={14} className="text-[var(--text-tertiary)] shrink-0" />
                    <div className="min-w-0">
                      <span className="font-medium text-[var(--text-primary)]">{b.name}</span>
                      <div className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-xs text-[var(--text-tertiary)]">
                        {timestamp && <span>{new Date(timestamp).toLocaleString(locale)}</span>}
                        {b.backup_type && <span>{translateEnum(t, "system.backupType", b.backup_type, b.backup_type)}</span>}
                        <span>{translateEnum(t, "system.backupIntegrity", integrity, integrity)}</span>
                        {typeof b.total_size_bytes === "number" && <span>{formatBytes(b.total_size_bytes, locale)}</span>}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-[var(--text-tertiary)]">{t("system.filesCount", String(b.file_count ?? (b.files?.length ?? 0)))}</span>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => doRestoreBackup(b)}
                      disabled={restoreDisabled}
                    >
                      <Undo2 size={12} className="mr-1" />{t("system.restoreBackup")}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => doDeleteBackup(b.name)}
                      disabled={operationPending}
                      aria-label={t("system.deleteBackup", b.name)}
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
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
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
                <><RotateCw size={14} className="animate-spin mr-1" />{t("system.exporting")}</>
              ) : exportProgress === "done" ? (
                `${t("system.exported")} ✓`
              ) : exportProgress === "error" ? (
                t("common.retry")
              ) : (
                <><Download size={14} className="mr-1" />{t("system.export")}</>
              )}
            </Button>
          </div>
        </div>

        {/* Dashboard 管理 (npm install / build) */}
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          <h3 className="text-sm font-semibold mb-4 flex items-center gap-2"><Wrench size={16} />{t("system.dashboardManagement")}</h3>
          <div className="flex gap-3 mb-4">
            <Button
              size="sm"
              onClick={() => doNpmAction("install")}
              disabled={npmAction !== "idle" || operationPending}
            >
              {npmAction === "installing" ? (
                <><RotateCw size={14} className="animate-spin mr-1" />{t("system.installing")}</>
              ) : (
                <><Download size={14} className="mr-1" />{t("system.installDeps")}</>
              )}
            </Button>
            <Button
              size="sm"
              onClick={() => doNpmAction("build")}
              disabled={npmAction !== "idle" || operationPending}
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
        {activeTab === "quality" && <QualityMonitorTab showToast={showToast} onResetRequested={requestQualityReset} refreshToken={qualityRefreshToken} resetPending={operationPending} />}

        {/* Delegation Tab */}
        {activeTab === "delegation" && <DelegationTab showToast={showToast} />}
      </PageContent>
      </Tabs>
    </PageFrame>
  );
}
