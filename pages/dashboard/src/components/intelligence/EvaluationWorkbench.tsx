import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ClipboardList, Loader2, Play, RefreshCw } from "lucide-react";

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
  EvaluationVariantDelta,
} from "@/types/intelligence";

import { EvaluationCaseTable } from "./EvaluationCaseTable";

interface EvaluationWorkbenchProps {
  showToast: (msg: string, isError?: boolean) => void;
}

type VariantName = "baseline" | "graph_expansion_off" | "topic_expansion_off";

const variantOptions: Array<{ value: VariantName; label: string }> = [
  { value: "baseline", label: "baseline" },
  { value: "graph_expansion_off", label: "graph off" },
  { value: "topic_expansion_off", label: "topic off" },
];

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

export function EvaluationWorkbench({ showToast }: EvaluationWorkbenchProps) {
  const { t, currentLang } = useI18n();
  const tRef = useRef(t);
  tRef.current = t;
  const locale = dashboardLocale(currentLang());
  const [datasets, setDatasets] = useState<EvaluationDataset[]>([]);
  const [selectedDatasets, setSelectedDatasets] = useState<string[]>([]);
  const [selectedVariants, setSelectedVariants] = useState<VariantName[]>([
    "baseline",
    "graph_expansion_off",
    "topic_expansion_off",
  ]);
  const [k, setK] = useState(5);
  const [report, setReport] = useState<EvaluationReport | null>(null);
  const [history, setHistory] = useState<EvaluationReport[]>([]);
  const [loading, setLoading] = useState(true);
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

  const loadWorkbench = useCallback(async () => {
    setLoading(true);
    try {
      const [datasetResponse, reportResponse] = await Promise.all([
        apiRequest("evaluation/datasets"),
        apiRequest("evaluation/reports?limit=10"),
      ]);
      const datasetData = unwrapApiData<{ datasets?: EvaluationDataset[] }>(datasetResponse);
      const reportData = unwrapApiData<{ reports?: EvaluationReport[] }>(reportResponse);
      const nextDatasets = datasetData.datasets ?? [];
      setDatasets(nextDatasets);
      setSelectedDatasets((current) => {
        const available = new Set(nextDatasets.map((dataset) => dataset.name));
        const retained = current.filter((name) => available.has(name));
        return retained.length > 0 ? retained : nextDatasets.slice(0, 1).map((dataset) => dataset.name);
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

  const toggleDataset = (name: string) => {
    setSelectedDatasets((current) => (
      current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name]
    ));
  };

  const toggleVariant = (name: VariantName) => {
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
          <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-3">
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.evaluation.workbench")}</h3>
            <Button variant="outline" size="sm" onClick={loadWorkbench} disabled={loading || running}>
              <RefreshCw size={13} />
              {t("common.refresh")}
            </Button>
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
                      <span className="block text-sm font-medium text-[var(--text-primary)]">{dataset.name}</span>
                      <span className="mt-1 block truncate text-2xs text-[var(--text-tertiary)]">{dataset.path}</span>
                      <span className="mt-2 flex flex-wrap gap-1">
                        <span className="rounded bg-[var(--color-border-light)] px-1.5 py-0.5 text-2xs text-[var(--text-secondary)]">
                          {t("intelligence.evaluation.caseCount", String(dataset.case_count))}
                        </span>
                        {dataset.intents.map((intent) => (
                          <span key={intent} className="rounded bg-[var(--color-accent)]/10 px-1.5 py-0.5 text-2xs text-[var(--color-accent)]">
                            {intent}
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

            <div className="grid gap-3 sm:grid-cols-[100px_1fr]">
              <label className="text-xs font-medium text-[var(--text-secondary)]">
                k
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={k}
                  onBlur={() => setK((value) => clampK(value))}
                  onChange={(event) => setK(clampK(Number(event.target.value)))}
                  className="mt-1 h-8 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 text-xs text-[var(--text-primary)]"
                />
              </label>
              <div>
                <p className="mb-1 text-xs font-medium text-[var(--text-secondary)]">{t("intelligence.evaluation.variants")}</p>
                <div className="flex flex-wrap gap-2">
                  {variantOptions.map((variant) => (
                    <label
                      key={variant.value}
                      data-selected={selectedVariants.includes(variant.value) ? "true" : undefined}
                      className={cn(
                        "inline-flex h-8 cursor-pointer items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 text-xs text-[var(--text-secondary)]",
                        selectionStateVariants({
                          kind: "surface",
                          selected: selectedVariants.includes(variant.value),
                        }),
                      )}
                    >
                      <Checkbox
                        checked={selectedVariants.includes(variant.value)}
                        onCheckedChange={() => toggleVariant(variant.value)}
                      />
                      {translateEnum(t, "intelligence.evaluation.variant", variant.value, variant.label)}
                    </label>
                  ))}
                </div>
              </div>
            </div>

            <Button onClick={runEvaluation} disabled={running || loading || selectedDatasets.length === 0} className="w-full">
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
