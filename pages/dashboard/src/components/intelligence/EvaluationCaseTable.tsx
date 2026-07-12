import type { EvaluationCaseResult } from "@/types/intelligence";
import { useI18n } from "@/hooks/useI18n";
import { dashboardLocale, formatDashboardNumber, formatDashboardPercent } from "@/lib/i18n";

interface EvaluationCaseTableProps {
  cases: EvaluationCaseResult[];
}

function formatPercent(value: number, locale: string): string {
  return formatDashboardPercent(value, locale, { maximumFractionDigits: 0 });
}

function formatMs(value: number, locale: string): string {
  return `${formatDashboardNumber(value, locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}ms`;
}

export function EvaluationCaseTable({ cases }: EvaluationCaseTableProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());

  if (cases.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-4 py-6 text-center text-xs text-[var(--text-tertiary)]">
        {t("intelligence.evaluation.noFailedCases")}
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-3">
        <h4 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.evaluation.failedCases")}</h4>
        <span className="rounded-full bg-[var(--color-danger)]/10 px-2 py-0.5 text-2xs font-medium text-[var(--color-danger)]">
          {cases.length}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-[780px] w-full text-left text-xs">
          <thead className="text-[var(--text-tertiary)]">
            <tr>
              <th className="px-4 py-2 font-medium">{t("intelligence.evaluation.table.case")}</th>
              <th className="px-4 py-2 font-medium">{t("intelligence.evaluation.table.query")}</th>
              <th className="px-4 py-2 font-medium">{t("intelligence.evaluation.table.rankedDocs")}</th>
              <th className="px-4 py-2 font-medium">{t("intelligence.evaluation.table.recall")}</th>
              <th className="px-4 py-2 font-medium">RR</th>
              <th className="px-4 py-2 font-medium">{t("intelligence.evaluation.table.gain")}</th>
              <th className="px-4 py-2 text-right font-medium">{t("intelligence.evaluation.table.latency")}</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((row) => {
              const failed = row.recall_at_k === 0;
              return (
                <tr key={row.case_id} className="border-t border-[var(--color-border-light)]">
                  <td className="px-4 py-3 align-top">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-[var(--text-primary)]">{row.case_id}</span>
                      {failed && (
                        <span className="rounded-full bg-[var(--color-danger)]/10 px-1.5 py-0.5 text-2xs font-medium text-[var(--color-danger)]">
                          {t("intelligence.evaluation.miss")}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="max-w-[220px] px-4 py-3 align-top text-[var(--text-secondary)]">{row.query}</td>
                  <td className="max-w-[260px] px-4 py-3 align-top font-mono text-2xs text-[var(--text-tertiary)]">
                    {row.ranked_doc_ids.length > 0 ? row.ranked_doc_ids.join(", ") : "-"}
                  </td>
                  <td className="px-4 py-3 align-top tabular-nums text-[var(--text-secondary)]">{formatPercent(row.recall_at_k, locale)}</td>
                  <td className="px-4 py-3 align-top tabular-nums text-[var(--text-secondary)]">{formatDashboardNumber(row.reciprocal_rank, locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                  <td className="px-4 py-3 align-top tabular-nums text-[var(--text-secondary)]">{formatDashboardNumber(row.ndcg_at_k, locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                  <td className="px-4 py-3 text-right align-top tabular-nums text-[var(--text-secondary)]">{formatMs(row.latency_ms, locale)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
