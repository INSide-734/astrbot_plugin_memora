import { useState, useEffect, useCallback, useRef } from "react";
import { RefreshCw, AlertTriangle } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { translateEnum } from "@/lib/i18n";
import { Button } from "@/components/ui/Button";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import { cn } from "@/lib/utils";

/** 后端模式闭集；off 即回滚到无候选基线。 */
const MODES = ["off", "observe", "full", "top_k"] as const;

/** 数值配置叶（保存时映射为平铺前缀键）；overfetch_factor 第一阶段固定为 3，不放编辑集。 */
const NUMERIC_FIELDS = [
  "fixed_k",
  "activation_threshold",
  "max_full_topics",
  "max_full_prompt_tokens",
  "max_query_chars",
  "metrics_retention_days",
  "observe_max_candidates",
  "observe_max_rows",
  "observe_max_duration_ms",
] as const;
type NumericField = (typeof NUMERIC_FIELDS)[number];

interface CandidateReuseConfigProps {
  showToast: (msg: string, isError?: boolean) => void;
}

/** GET config/topic-segmentation 的 candidate_reuse 状态分支。 */
interface CandidateReuseStatus {
  catalog_status: string;
  dirty_count: number | null;
  scope_buckets: Record<string, number> | null;
  aggregated_metrics: {
    p95_latency_ms: number | null;
    p95_candidates: number | null;
    p95_tokens: number | null;
  } | null;
}

/** config/state 快照中的 candidate_reuse 配置叶（新增叶后端默认下发，前端按可选防御）。 */
interface CandidateReuseSettings {
  mode: string;
  fixed_k?: number;
  activation_threshold: number;
  max_full_topics: number;
  max_full_prompt_tokens: number;
  max_query_chars?: number;
  overfetch_factor?: number;
  metrics_retention_days?: number;
  observe_max_candidates?: number;
  observe_max_rows?: number;
  observe_max_duration_ms?: number;
}

/** config/state 响应（不带 revision 参数时附完整 config）。 */
interface ConfigStateData {
  revision: string;
  config?: Record<string, unknown>;
}

export function CandidateReuseConfig({ showToast }: CandidateReuseConfigProps) {
  const { t } = useI18n();
  const [status, setStatus] = useState<CandidateReuseStatus | null>(null);
  const [settings, setSettings] = useState<CandidateReuseSettings | null>(null);
  const [configRevision, setConfigRevision] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<Partial<CandidateReuseSettings>>({});
  const pollInterval = useRef<number>();

  const fetchStatus = useCallback(async () => {
    try {
      const res = await apiRequest("config/topic-segmentation");
      const data = unwrapApiData<{ candidate_reuse: CandidateReuseStatus }>(res);
      setStatus(data.candidate_reuse);
    } catch {
      // 状态获取失败保留上次值，不打断面板
    }
    setLoading(false);
  }, []);

  const fetchSettings = useCallback(async () => {
    try {
      const res = await apiRequest("config/state");
      const data = unwrapApiData<ConfigStateData>(res);
      setConfigRevision(data.revision);
      const segmentation = (data.config ?? {})["topic_segmentation"] as
        | Record<string, unknown>
        | undefined;
      const reuse = segmentation?.["candidate_reuse"];
      if (reuse && typeof reuse === "object") {
        setSettings(reuse as unknown as CandidateReuseSettings);
      }
    } catch {
      // 配置读取失败保留上次值
    }
  }, []);

  useEffect(() => {
    void fetchStatus();
    void fetchSettings();
    pollInterval.current = window.setInterval(fetchStatus, 30000);
    return () => {
      clearInterval(pollInterval.current);
    };
  }, [fetchStatus, fetchSettings]);

  const handleSave = async () => {
    if (!settings) return;

    const updated = { ...settings, ...draft };

    if (updated.fixed_k !== undefined && updated.fixed_k > updated.max_full_topics) {
      showToast(t("candidateReuse.validation.fixedKExceedsMax"), true);
      return;
    }
    if (updated.activation_threshold > updated.max_full_topics) {
      showToast(t("candidateReuse.validation.thresholdExceedsMax"), true);
      return;
    }
    setSaving(true);
    try {
      if (!configRevision) {
        showToast("配置 revision 不可用", true);
        return;
      }
      const body: Record<string, unknown> = { base_revision: configRevision };
      if (draft.mode !== undefined) {
        body["topic_segmentation.candidate_reuse.mode"] = draft.mode;
      }
      for (const field of NUMERIC_FIELDS) {
        if (draft[field] !== undefined) {
          body[`topic_segmentation.candidate_reuse.${field}`] = draft[field];
        }
      }
      await apiRequest("config/topic-segmentation", {
        method: "POST",
        body,
        retries: 0,
      });
      showToast(t("candidateReuse.saveSuccess"));
      setDraft({});
      await Promise.all([fetchStatus(), fetchSettings()]);
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), true);
    } finally {
      setSaving(false);
    }
  };

  const handleModeChange = (mode: string | null) => {
    if (!mode) return;
    setDraft((prev) => ({ ...prev, mode }));
  };

  const handleFieldChange = (field: NumericField, value: number) => {
    setDraft((prev) => ({ ...prev, [field]: value }));
  };

  if (loading || !status || !settings) {
    return (
      <div className="flex flex-col gap-4 rounded-lg border bg-card p-6">
        <div className="text-sm text-muted-foreground">{t("candidateReuse.loading")}</div>
      </div>
    );
  }

  const currentMode = draft.mode ?? settings.mode;

  /** 解析草稿/当前值；settings 缺失新叶时回落 Pydantic 默认。 */
  const currentValue = (field: NumericField, fallback: number): number =>
    draft[field] ?? settings[field] ?? fallback;

  const catalogDegraded = status.catalog_status !== "ready";
  const hasDraft = Object.keys(draft).length > 0;

  return (
    <div className="flex flex-col gap-4 rounded-lg border bg-card p-6">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">{t("candidateReuse.title")}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{t("candidateReuse.description")}</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            void fetchStatus();
            void fetchSettings();
          }}
          disabled={loading}
        >
          <RefreshCw className="mr-2 h-4 w-4" />
          {t("candidateReuse.refresh")}
        </Button>
      </div>

      {catalogDegraded && (
        <div className="flex items-start gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
          <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="min-w-0 flex-1 text-xs">
            <p className="font-medium text-amber-900 dark:text-amber-100">
              {t("candidateReuse.catalogDegraded")}
            </p>
            <p className="mt-1 text-amber-700 dark:text-amber-300">
              {t("candidateReuse.catalogDegradedHint")}
            </p>
          </div>
        </div>
      )}

      {/* Config Section */}
      <div className="space-y-4">
        <h4 className="text-xs font-semibold text-muted-foreground">{t("candidateReuse.config")}</h4>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-2 block text-xs font-medium">{t("candidateReuse.modeLabel")}</label>
            <Select value={currentMode} onValueChange={handleModeChange} disabled={catalogDegraded}>
              <SelectTrigger className={cn(catalogDegraded && "cursor-not-allowed opacity-50")}>
                {translateEnum(t, "candidateReuse.mode", currentMode)}
              </SelectTrigger>
              <SelectContent>
                {MODES.map((mode) => (
                  <SelectItem key={mode} value={mode}>
                    {translateEnum(t, "candidateReuse.mode", mode)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {currentMode === "top_k" && (
            <div>
              <label className="mb-2 block text-xs font-medium">{t("candidateReuse.fixedK")}</label>
              <input
                type="number"
                min={1}
                max={24}
                value={currentValue("fixed_k", 8)}
                onChange={(e) => handleFieldChange("fixed_k", Number(e.target.value))}
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
              />
            </div>
          )}

          <div>
            <label className="mb-2 block text-xs font-medium">{t("candidateReuse.activationThreshold")}</label>
            <input
              type="number"
              min={1}
              max={100}
              value={currentValue("activation_threshold", 32)}
              onChange={(e) => handleFieldChange("activation_threshold", Number(e.target.value))}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            />
          </div>

          <div>
            <label className="mb-2 block text-xs font-medium">{t("candidateReuse.maxFullTopics")}</label>
            <input
              type="number"
              min={1}
              max={50}
              value={currentValue("max_full_topics", 32)}
              onChange={(e) => handleFieldChange("max_full_topics", Number(e.target.value))}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            />
          </div>

          <div className="sm:col-span-2">
            <label className="mb-2 block text-xs font-medium">{t("candidateReuse.maxFullPromptTokens")}</label>
            <input
              type="number"
              min={50}
              step={50}
              value={currentValue("max_full_prompt_tokens", 256)}
              onChange={(e) => handleFieldChange("max_full_prompt_tokens", Number(e.target.value))}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            />
          </div>

          <div>
            <label className="mb-2 block text-xs font-medium">{t("candidateReuse.maxQueryChars")}</label>
            <input
              type="number"
              min={1}
              max={10000}
              value={currentValue("max_query_chars", 2000)}
              onChange={(e) => handleFieldChange("max_query_chars", Number(e.target.value))}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            />
          </div>

          <div>
            <label className="mb-2 block text-xs font-medium">{t("candidateReuse.metricsRetentionDays")}</label>
            <input
              type="number"
              min={1}
              max={3650}
              value={currentValue("metrics_retention_days", 30)}
              onChange={(e) => handleFieldChange("metrics_retention_days", Number(e.target.value))}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            />
          </div>

          <div>
            <label className="mb-2 block text-xs font-medium">{t("candidateReuse.observeMaxCandidates")}</label>
            <input
              type="number"
              min={1}
              max={512}
              value={currentValue("observe_max_candidates", 32)}
              onChange={(e) => handleFieldChange("observe_max_candidates", Number(e.target.value))}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            />
          </div>

          <div>
            <label className="mb-2 block text-xs font-medium">{t("candidateReuse.observeMaxRows")}</label>
            <input
              type="number"
              min={1}
              max={4096}
              value={currentValue("observe_max_rows", 96)}
              onChange={(e) => handleFieldChange("observe_max_rows", Number(e.target.value))}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            />
          </div>

          <div>
            <label className="mb-2 block text-xs font-medium">{t("candidateReuse.observeMaxDurationMs")}</label>
            <input
              type="number"
              min={1}
              max={60000}
              value={currentValue("observe_max_duration_ms", 250)}
              onChange={(e) => handleFieldChange("observe_max_duration_ms", Number(e.target.value))}
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            />
          </div>

          <div className="sm:col-span-2">
            <label className="mb-2 block text-xs font-medium">{t("candidateReuse.overfetchFactor")}</label>
            <input
              type="number"
              min={3}
              max={3}
              value={settings.overfetch_factor ?? 3}
              readOnly
              aria-readonly
              className="w-full cursor-not-allowed rounded-md border bg-muted px-3 py-2 text-sm text-muted-foreground"
            />
          </div>
        </div>

        {hasDraft && (
          <div className="flex gap-2">
            <Button onClick={() => void handleSave()} disabled={saving}>
              {saving ? t("candidateReuse.saving") : t("candidateReuse.save")}
            </Button>
            <Button variant="outline" onClick={() => setDraft({})} disabled={saving}>
              {t("candidateReuse.discard")}
            </Button>
          </div>
        )}
      </div>

      {/* Status Section */}
      <div className="space-y-4">
        <h4 className="text-xs font-semibold text-muted-foreground">{t("candidateReuse.status")}</h4>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <div className="text-xs text-muted-foreground">{t("candidateReuse.catalogStatusLabel")}</div>
            <div className="mt-1 flex items-center gap-2">
              <span
                className={cn(
                  "inline-block h-2 w-2 rounded-full",
                  status.catalog_status === "ready" ? "bg-green-500" : "bg-amber-500"
                )}
              />
              <span className="text-sm font-medium">
                {translateEnum(t, "candidateReuse.catalogStatus", status.catalog_status)}
              </span>
            </div>
          </div>

          <div>
            <div className="text-xs text-muted-foreground">{t("candidateReuse.dirtyCount")}</div>
            <div className="mt-1 text-sm font-medium tabular-nums">
              {status.dirty_count ?? "--"}
            </div>
          </div>
        </div>

        {status.scope_buckets && Object.keys(status.scope_buckets).length > 0 && (
          <div>
            <div className="mb-2 text-xs text-muted-foreground">{t("candidateReuse.sizeBuckets")}</div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {Object.entries(status.scope_buckets).map(([bucket, count]) => (
                <div key={bucket} className="rounded-md border bg-muted/30 px-3 py-2">
                  <div className="text-xs text-muted-foreground">{bucket}</div>
                  <div className="mt-1 text-sm font-medium tabular-nums">{count}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {status.aggregated_metrics && (
          <div>
            <div className="mb-2 text-xs text-muted-foreground">{t("candidateReuse.metrics")}</div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <div>
                <div className="text-xs text-muted-foreground">{t("candidateReuse.p95Latency")}</div>
                <div className="mt-1 text-sm font-medium tabular-nums">
                  {status.aggregated_metrics.p95_latency_ms?.toFixed(1) ?? "--"}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">{t("candidateReuse.p95Candidates")}</div>
                <div className="mt-1 text-sm font-medium tabular-nums">
                  {status.aggregated_metrics.p95_candidates?.toFixed(1) ?? "--"}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">{t("candidateReuse.p95Tokens")}</div>
                <div className="mt-1 text-sm font-medium tabular-nums">
                  {status.aggregated_metrics.p95_tokens?.toFixed(0) ?? "--"}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
