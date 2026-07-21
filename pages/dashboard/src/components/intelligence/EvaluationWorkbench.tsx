import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ClipboardList, Loader2, Play, RefreshCw, Upload } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/checkbox";
import { selectionStateVariants } from "@/components/ui/selection-state";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { dashboardLocale, formatDashboardNumber, formatDashboardPercent, translateEnum } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type {
  EvaluationDataset,
  EvaluationReport,
  EvaluationVariantDescriptor,
  EvaluationVariantDelta,
} from "@/types/intelligence";

import { EvaluationCaseTable } from "./EvaluationCaseTable";

interface EvaluationWorkbenchProps {
  showToast: (msg: string, isError?: boolean) => void;
}

const LEGACY_VARIANT_DESCRIPTORS: EvaluationVariantDescriptor[] = [
  { name: "baseline", available: true, reason_code: "available", default_selected: true },
  { name: "graph_expansion_off", available: true, reason_code: "available", default_selected: true },
  { name: "topic_expansion_off", available: true, reason_code: "available", default_selected: true },
];

/** 读取用户选择的 JSONL；兼容未实现 File.text 的嵌入式浏览器。 */
function readDatasetFile(file: File): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("evaluation_dataset_read_failed"));
    reader.readAsText(file, "utf-8");
  });
}

/** 返回 descriptor 中可执行的默认选择，并保证至少保留一个可执行变体。 */
function defaultVariantSelection(descriptors: EvaluationVariantDescriptor[]): string[] {
  const available = descriptors.filter((descriptor) => descriptor.available);
  const selected = available
    .filter((descriptor) => descriptor.default_selected)
    .map((descriptor) => descriptor.name);
  if (selected.length > 0) return selected;
  const baseline = available.find((descriptor) => descriptor.name === "baseline");
  return (baseline ? [baseline] : available.slice(0, 1)).map((descriptor) => descriptor.name);
}

function formatPercent(value: number, locale: string): string {
  return formatDashboardPercent(value, locale, { maximumFractionDigits: 0 });
}

function formatMs(value: number, locale: string): string {
  return `${formatDashboardNumber(value, locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}ms`;
}

function formatDelta(value: number | null, locale: string, notAvailable: string, suffix = ""): string {
  if (value === null) return notAvailable;
  return `${formatDashboardNumber(value, locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: "exceptZero",
  })}${suffix}`;
}

function clampK(value: number): number {
  if (!Number.isFinite(value)) return 5;
  return Math.min(20, Math.max(1, Math.round(value)));
}

function VariantDeltaRow({ name, delta }: { name: string; delta: EvaluationVariantDelta }) {
  const { t, currentLang } = useI18n();
  const notAvailable = t("common.notAvailableShort");
  const locale = dashboardLocale(currentLang());

  return (
    <tr className="border-t border-[var(--color-border-light)]">
      <td className="px-4 py-2 font-medium text-[var(--text-primary)]">
        {translateEnum(t, "intelligence.evaluation.variant", name, name)}
      </td>
      <td className="px-4 py-2 tabular-nums text-[var(--text-secondary)]">{formatDelta(delta.recall_at_k, locale, notAvailable)}</td>
      <td className="px-4 py-2 tabular-nums text-[var(--text-secondary)]">{formatDelta(delta.mrr, locale, notAvailable)}</td>
      <td className="px-4 py-2 tabular-nums text-[var(--text-secondary)]">{formatDelta(delta.ndcg_at_k, locale, notAvailable)}</td>
      <td className="px-4 py-2 text-right tabular-nums text-[var(--text-secondary)]">{formatDelta(delta.p95_latency_ms, locale, notAvailable, "ms")}</td>
    </tr>
  );
}

interface VariantSelectionCardProps {
  descriptor: EvaluationVariantDescriptor;
  selected: boolean;
  onToggle: (name: string) => void;
}

/** 渲染整面可点、状态层级清晰的紧凑变体选择卡。 */
function VariantSelectionCard({ descriptor, selected, onToggle }: VariantSelectionCardProps) {
  const { t } = useI18n();
  const label = translateEnum(
    t,
    "intelligence.evaluation.variant",
    descriptor.name,
    descriptor.name,
  );
  const unavailableReason = descriptor.available
    ? ""
    : translateEnum(
      t,
      "intelligence.evaluation.reason",
      descriptor.reason_code,
      descriptor.reason_code,
    );

  return (
    <label
      data-variant-card={descriptor.name}
      data-selected={selected ? "true" : undefined}
      aria-disabled={!descriptor.available || undefined}
      title={unavailableReason || undefined}
      className={cn(
        "group relative flex min-h-[4.25rem] w-full items-start gap-2.5 overflow-hidden rounded-lg border border-border bg-card p-3 text-left text-card-foreground",
        "focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/50 focus-within:ring-offset-1 focus-within:ring-offset-background",
        descriptor.available
          ? "cursor-pointer hover:border-foreground/20 hover:bg-muted/40"
          : "cursor-not-allowed border-dashed bg-muted/40",
        selectionStateVariants({ kind: "surface", selected }),
      )}
    >
      <Checkbox
        checked={selected}
        disabled={!descriptor.available}
        className="mt-0.5 shrink-0"
        onCheckedChange={() => onToggle(descriptor.name)}
      />
      <span className="min-w-0 flex-1">
        <span className="flex items-start justify-between gap-2">
          <span className="text-xs font-semibold leading-5 text-foreground">
            {label}
          </span>
          {!descriptor.available ? (
            <span className="shrink-0 rounded-full border border-border bg-muted/60 px-1.5 py-0.5 text-2xs font-medium text-muted-foreground">
              {t("runtime.status.unavailable")}
            </span>
          ) : null}
        </span>
        {unavailableReason ? (
          <span className="mt-1 line-clamp-2 block text-2xs leading-4 text-muted-foreground">
            {unavailableReason}
          </span>
        ) : null}
      </span>
    </label>
  );
}

export function EvaluationWorkbench({ showToast }: EvaluationWorkbenchProps) {
  const { t, currentLang } = useI18n();
  const tRef = useRef(t);
  tRef.current = t;
  const datasetInputRef = useRef<HTMLInputElement>(null);
  const locale = dashboardLocale(currentLang());
  const [datasets, setDatasets] = useState<EvaluationDataset[]>([]);
  const [selectedDatasets, setSelectedDatasets] = useState<string[]>([]);
  const [variantDescriptors, setVariantDescriptors] = useState<EvaluationVariantDescriptor[]>(
    LEGACY_VARIANT_DESCRIPTORS,
  );
  const [selectedVariants, setSelectedVariants] = useState<string[]>([]);
  const [k, setK] = useState(5);
  const [report, setReport] = useState<EvaluationReport | null>(null);
  const [history, setHistory] = useState<EvaluationReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [importingDataset, setImportingDataset] = useState(false);
  const [running, setRunning] = useState(false);
  const runningRef = useRef(false);
  const [runError, setRunError] = useState<string | null>(null);

  const failedCases = useMemo(
    () => (report?.cases ?? []).filter((row) => row.recall_at_k === 0),
    [report]
  );
  const variantDeltas = useMemo(
    () => Object.entries(report?.deltas ?? {}),
    [report]
  );
  const variantOutcomes = useMemo(
    () => Object.entries(report?.variants ?? {}),
    [report]
  );

  const loadWorkbench = useCallback(async () => {
    setLoading(true);
    try {
      const [datasetResponse, reportResponse] = await Promise.all([
        apiRequest("evaluation/datasets"),
        apiRequest("evaluation/reports?limit=10"),
      ]);
      const datasetData = unwrapApiData<{
        datasets?: EvaluationDataset[];
        variants?: EvaluationVariantDescriptor[];
      }>(datasetResponse);
      const reportData = unwrapApiData<{ reports?: EvaluationReport[] }>(reportResponse);
      const nextDatasets = datasetData.datasets ?? [];
      setDatasets(nextDatasets);
      setSelectedDatasets((current) => {
        const available = new Set(nextDatasets.map((dataset) => dataset.name));
        const retained = current.filter((name) => available.has(name));
        return retained.length > 0 ? retained : nextDatasets.slice(0, 1).map((dataset) => dataset.name);
      });
      const nextDescriptors = datasetData.variants?.length
        ? datasetData.variants
        : LEGACY_VARIANT_DESCRIPTORS;
      setVariantDescriptors(nextDescriptors);
      setSelectedVariants((current) => {
        const available = new Set(
          nextDescriptors
            .filter((descriptor) => descriptor.available)
            .map((descriptor) => descriptor.name),
        );
        const retained = current.filter((name) => available.has(name));
        return retained.length > 0 ? retained : defaultVariantSelection(nextDescriptors);
      });
      setHistory(reportData.reports ?? []);
    } catch (error) {
      showToast(tRef.current("common.errorPrefix", error instanceof Error ? error.message : String(error)), true);
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadWorkbench();
  }, [loadWorkbench]);

  /** 读取并提交生产标注集，成功后重新获取服务端数据集目录。 */
  const importDataset = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file || importingDataset) return;
    setImportingDataset(true);
    try {
      const content = await readDatasetFile(file);
      const response = await apiRequest("evaluation/datasets/import", {
        method: "POST",
        body: { filename: file.name, content },
      });
      const data = unwrapApiData<{
        dataset?: { name?: string };
      }>(response);
      await loadWorkbench();
      showToast(t("intelligence.evaluation.datasetImported", data.dataset?.name ?? file.name));
    } catch (error) {
      showToast(t("common.errorPrefix", error instanceof Error ? error.message : String(error)), true);
    } finally {
      input.value = "";
      setImportingDataset(false);
    }
  };

  const toggleDataset = (name: string) => {
    setSelectedDatasets((current) => (
      current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name]
    ));
  };

  const toggleVariant = (name: string) => {
    if (!variantDescriptors.some((descriptor) => descriptor.name === name && descriptor.available)) return;
    setSelectedVariants((current) => {
      if (current.includes(name)) {
        return current.length === 1 ? current : current.filter((item) => item !== name);
      }
      return [...current, name];
    });
  };

  const runEvaluation = async () => {
    if (runningRef.current) return;
    if (selectedDatasets.length === 0) {
      showToast(t("intelligence.evaluation.selectDataset"), true);
      return;
    }

    const nextK = clampK(k);
    setK(nextK);
    runningRef.current = true;
    setRunning(true);
    setRunError(null);
    try {
      const response = await apiRequest("evaluation/run", {
        method: "POST",
        body: {
          datasets: selectedDatasets,
          k: nextK,
          variants: selectedVariants,
          baseline: "baseline",
          save_report: true,
        },
      });
      const nextReport = unwrapApiData<EvaluationReport>(response);
      setReport(nextReport);
      setHistory((current) => [nextReport, ...current.filter((item) => item.report_id !== nextReport.report_id)].slice(0, 10));
      showToast(t("intelligence.evaluation.reportReady", nextReport.report_id));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setRunError(message);
      showToast(t("common.errorPrefix", message), true);
    } finally {
      runningRef.current = false;
      setRunning(false);
    }
  };

  const openReport = async (item: EvaluationReport) => {
    try {
      const response = await apiRequest(`evaluation/reports/detail?report_id=${encodeURIComponent(item.report_id)}`);
      const data = unwrapApiData<{ report?: EvaluationReport }>(response);
      setReport(data.report ?? item);
    } catch (error) {
      showToast(t("common.errorPrefix", error instanceof Error ? error.message : String(error)), true);
    }
  };

  return (
    <section className="grid gap-4 xl:grid-cols-[0.78fr_1.22fr]">
      <div className="space-y-4">
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--color-border)] px-4 py-3">
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.evaluation.workbench")}</h3>
            <div className="flex items-center gap-2">
              <input
                ref={datasetInputRef}
                type="file"
                accept=".jsonl,application/x-ndjson,application/json"
                className="sr-only"
                tabIndex={-1}
                onChange={(event) => { void importDataset(event); }}
              />
              <Button
                variant="outline"
                size="sm"
                onClick={() => datasetInputRef.current?.click()}
                disabled={importingDataset || running}
              >
                {importingDataset ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
                {t(importingDataset
                  ? "intelligence.evaluation.importingDataset"
                  : "intelligence.evaluation.importDataset")}
              </Button>
              <Button variant="outline" size="sm" onClick={loadWorkbench} disabled={loading || running || importingDataset}>
                <RefreshCw size={13} />
                {t("common.refresh")}
              </Button>
            </div>
          </div>
          <div className="space-y-5 p-4">
            <div>
              <div className="mb-2 flex items-center justify-between">
                <p className="text-2xs uppercase text-[var(--text-tertiary)]">{t("intelligence.evaluation.datasets")}</p>
                <span className="text-2xs tabular-nums text-[var(--text-tertiary)]">{selectedDatasets.length}/{datasets.length}</span>
              </div>
              <div className="space-y-2">
                {loading ? (
                  <p className="py-3 text-xs text-[var(--text-tertiary)]">{t("intelligence.evaluation.loadingDatasets")}</p>
                ) : datasets.length === 0 ? (
                  <p role="status" className="py-3 text-xs text-[var(--text-tertiary)]">
                    {t("intelligence.evaluation.noDatasets")}
                  </p>
                ) : datasets.map((dataset) => (
                  <label
                    key={dataset.name}
                    data-selected={selectedDatasets.includes(dataset.name) ? "true" : undefined}
                    className={cn(
                      "flex cursor-pointer items-start gap-3 rounded-lg border border-[var(--color-border-light)] bg-[var(--color-surface)] p-3 hover:border-[var(--color-border)]",
                      selectionStateVariants({
                        kind: "surface",
                        selected: selectedDatasets.includes(dataset.name),
                      }),
                    )}
                  >
                    <Checkbox
                      className="mt-0.5"
                      checked={selectedDatasets.includes(dataset.name)}
                      onCheckedChange={() => toggleDataset(dataset.name)}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium text-[var(--text-primary)]">
                        {dataset.source === "current_memories"
                          ? t("intelligence.evaluation.currentMemories")
                          : dataset.name}
                      </span>
                      <span className="mt-1 block text-2xs text-[var(--text-tertiary)]">
                        {dataset.source === "current_memories"
                          ? t("intelligence.evaluation.currentMemoriesDescription")
                          : dataset.path}
                      </span>
                      <span className="mt-2 flex flex-wrap gap-1">
                        <span className="rounded bg-[var(--color-border-light)] px-1.5 py-0.5 text-2xs text-[var(--text-secondary)]">
                          {t("intelligence.evaluation.caseCount", String(dataset.case_count))}
                        </span>
                        {dataset.intents.map((intent) => (
                          <span key={intent} className="rounded bg-[var(--color-accent)]/10 px-1.5 py-0.5 text-2xs text-[var(--color-accent)]">
                            {translateEnum(t, "intelligence.evaluation.intent", intent, intent)}
                          </span>
                        ))}
                        {dataset.chat_types.map((chatType) => (
                          <span key={chatType} className="rounded bg-[var(--color-border-light)] px-1.5 py-0.5 text-2xs text-[var(--text-secondary)]">
                            {translateEnum(t, "intelligence.trace.chatType", chatType, chatType)}
                          </span>
                        ))}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            </div>

            <div className="space-y-3">
              <label className="flex items-center justify-between gap-3 text-xs font-medium text-[var(--text-secondary)]">
                <span>k</span>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={k}
                  onBlur={() => setK((value) => clampK(value))}
                  onChange={(event) => setK(clampK(Number(event.target.value)))}
                  className="h-8 w-24 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 text-xs text-[var(--text-primary)]"
                />
              </label>
              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <p className="text-xs font-medium text-[var(--text-secondary)]">
                    {t("intelligence.evaluation.variants")}
                  </p>
                  <span
                    aria-label={t("select.selected", String(selectedVariants.length))}
                    className="rounded-full border border-border bg-muted/60 px-2 py-0.5 text-2xs font-medium tabular-nums text-muted-foreground"
                  >
                    {selectedVariants.length} / {variantDescriptors.filter((variant) => variant.available).length}
                  </span>
                </div>
                <div
                  data-variant-grid
                  className="grid grid-cols-1 gap-2 sm:grid-cols-2"
                >
                  {variantDescriptors.map((variant) => (
                    <VariantSelectionCard
                      key={variant.name}
                      descriptor={variant}
                      selected={selectedVariants.includes(variant.name)}
                      onToggle={toggleVariant}
                    />
                  ))}
                </div>
              </div>
            </div>

            <Button
              onClick={runEvaluation}
              disabled={running || loading || selectedDatasets.length === 0 || selectedVariants.length === 0}
              className="w-full"
            >
              {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
              {running ? t("intelligence.evaluation.running") : t("intelligence.evaluation.run")}
            </Button>
            {runError ? <p role="alert" className="text-sm text-destructive">{runError}</p> : null}
          </div>
        </div>

        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
          <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-4 py-3">
            <ClipboardList size={14} className="text-[var(--color-accent)]" />
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.evaluation.reportHistory")}</h3>
          </div>
          <div className="divide-y divide-[var(--color-border-light)]">
            {history.length === 0 ? (
              <p className="px-4 py-5 text-xs text-[var(--text-tertiary)]">{t("intelligence.evaluation.noSavedReports")}</p>
            ) : history.map((item) => (
              <button
                key={item.report_id}
                type="button"
                onClick={() => { void openReport(item); }}
                className="block w-full px-4 py-3 text-left transition-colors hover:bg-[var(--color-surface)]"
              >
                <span className="block text-xs font-medium text-[var(--text-primary)]">{item.report_id}</span>
                <span className="mt-1 block text-2xs text-[var(--text-tertiary)]">
                  {item.datasets.join(", ")} / {formatPercent(item.summary.recall_at_k, locale)} / {new Date(item.created_at * 1000).toLocaleString(locale)}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="space-y-4">
        {report ? (
          <>
            <div className="grid gap-3 md:grid-cols-5">
              {[
                [t("intelligence.evaluation.metric.cases"), String(report.summary.total_cases)],
                ["Recall@K", formatPercent(report.summary.recall_at_k, locale)],
                ["MRR", formatPercent(report.summary.mrr, locale)],
                ["nDCG@K", formatPercent(report.summary.ndcg_at_k, locale)],
                ["p95", formatMs(report.summary.p95_latency_ms, locale)],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)] p-3">
                  <p className="text-2xs uppercase text-[var(--text-tertiary)]">{label}</p>
                  <p className="mt-2 text-xl font-semibold tabular-nums text-[var(--text-primary)]">{value}</p>
                </div>
              ))}
            </div>

            {variantOutcomes.length > 0 && (
              <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
                <div className="border-b border-[var(--color-border)] px-4 py-3">
                  <h4 className="text-sm font-semibold text-[var(--text-primary)]">
                    {t("intelligence.evaluation.variantOutcomes")}
                  </h4>
                </div>
                <div className="overflow-x-auto">
                  <table className="min-w-[560px] w-full text-left text-xs">
                    <thead className="text-[var(--text-tertiary)]">
                      <tr>
                        <th className="px-4 py-2 font-medium">{t("intelligence.table.variant")}</th>
                        <th className="px-4 py-2 font-medium">{t("intelligence.metric.status")}</th>
                        <th className="px-4 py-2 font-medium">{t("intelligence.evaluation.effectiveSettings")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {variantOutcomes.map(([name, outcome]) => {
                        const settings = Object.entries(outcome.effective_settings ?? {})
                          .map(([key, value]) => `${key}=${String(value)}`)
                          .join(", ");
                        const reason = outcome.reason_code && outcome.reason_code !== "available"
                          ? translateEnum(
                            t,
                            "intelligence.evaluation.reason",
                            outcome.reason_code,
                            outcome.reason_code,
                          )
                          : "";
                        return (
                          <tr key={name} className="border-t border-[var(--color-border-light)]">
                            <td className="px-4 py-2 font-medium text-[var(--text-primary)]">
                              {translateEnum(t, "intelligence.evaluation.variant", name, name)}
                            </td>
                            <td className="px-4 py-2 text-[var(--text-secondary)]">
                              {translateEnum(t, "runtime.status", outcome.status, outcome.status)}
                              {reason ? <span className="ml-2 text-[var(--text-tertiary)]">{reason}</span> : null}
                            </td>
                            <td className="px-4 py-2 font-mono text-2xs text-[var(--text-tertiary)]">
                              {settings || "-"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {variantDeltas.length > 0 && (
              <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
                <div className="border-b border-[var(--color-border)] px-4 py-3">
                  <h4 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.evaluation.variantDeltas")}</h4>
                </div>
                <div className="overflow-x-auto">
                  <table className="min-w-[560px] w-full text-left text-xs">
                    <thead className="text-[var(--text-tertiary)]">
                      <tr>
                        <th className="px-4 py-2 font-medium">{t("intelligence.table.variant")}</th>
                        <th className="px-4 py-2 font-medium">{t("intelligence.evaluation.recallDelta")}</th>
                        <th className="px-4 py-2 font-medium">{t("intelligence.evaluation.rrDelta")}</th>
                        <th className="px-4 py-2 font-medium">{t("intelligence.evaluation.gainDelta")}</th>
                        <th className="px-4 py-2 text-right font-medium">{t("intelligence.evaluation.p95Delta")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {variantDeltas.map(([name, delta]) => <VariantDeltaRow key={name} name={name} delta={delta} />)}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <EvaluationCaseTable cases={failedCases} />
          </>
        ) : (
          <div className="rounded-lg border border-dashed border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-4 py-10 text-center text-sm text-[var(--text-tertiary)]">
            {t("intelligence.evaluation.emptyPrompt")}
          </div>
        )}
      </div>
    </section>
  );
}
