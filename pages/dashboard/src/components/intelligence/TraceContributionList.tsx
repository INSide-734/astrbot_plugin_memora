import type { RecallTraceScoreContribution } from "@/types/intelligence";
import { useI18n } from "@/hooks/useI18n";
import { dashboardLocale, formatDashboardNumber, formatDashboardPercent, translateEnum } from "@/lib/i18n";

interface TraceContributionListProps {
  contributions: RecallTraceScoreContribution[];
}

function formatScore(value: number, locale: string): string {
  return formatDashboardNumber(value, locale, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
}

function formatPercent(value: number, locale: string): string {
  return formatDashboardPercent(value, locale, { maximumFractionDigits: 0 });
}

function metadataChips(metadata?: Record<string, unknown>) {
  return Object.entries(metadata ?? {}).slice(0, 4).map(([key, value]) => (
    <span
      key={key}
      className="rounded bg-[var(--color-border-light)] px-1.5 py-0.5 text-2xs text-[var(--text-tertiary)]"
    >
      {key}: {String(value)}
    </span>
  ));
}

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
          {item.explanation ? (
            <p className="mt-1 text-xs text-[var(--text-secondary)]">{item.explanation}</p>
          ) : null}
          {item.metadata ? (
            <div className="mt-2 flex flex-wrap gap-1">{metadataChips(item.metadata)}</div>
          ) : null}
        </div>
      ))}
    </div>
  );
}
