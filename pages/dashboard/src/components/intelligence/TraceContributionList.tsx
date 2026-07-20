import type { RecallTraceScoreContribution } from "@/types/intelligence";
import { useI18n } from "@/hooks/useI18n";
import { dashboardLocale, formatDashboardNumber, formatDashboardPercent, translateEnum } from "@/lib/i18n";

interface TraceContributionListProps {
  contributions: RecallTraceScoreContribution[];
}

/** 按当前语言格式化贡献分数。 */
function formatScore(value: number, locale: string): string {
  return formatDashboardNumber(value, locale, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
}

/** 按当前语言格式化贡献权重。 */
function formatPercent(value: number, locale: string): string {
  return formatDashboardPercent(value, locale, { maximumFractionDigits: 0 });
}

/** 展示只含来源、分数和权重的安全贡献列表。 */
export function TraceContributionList({ contributions }: TraceContributionListProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());

  if (contributions.length === 0) {
    return <p className="text-xs text-[var(--text-tertiary)]">{t("intelligence.trace.noContributions")}</p>;
  }

  return (
    <div className="space-y-2">
      {contributions.map((item) => (
        <div
          key={`${item.source}-${item.score}-${item.weight}`}
          className="rounded-lg border border-[var(--color-border-light)] bg-[var(--color-surface)] p-2.5"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium text-[var(--text-primary)]">
              {translateEnum(t, "intelligence.trace.source", item.source, item.source)}
            </span>
            <span className="text-2xs tabular-nums text-[var(--text-secondary)]">
              {t("intelligence.trace.contributionScore", formatScore(item.score, locale), formatPercent(item.weight, locale))}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}
