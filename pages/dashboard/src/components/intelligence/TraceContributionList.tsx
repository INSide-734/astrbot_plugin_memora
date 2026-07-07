import type { RecallTraceScoreContribution } from "@/types/intelligence";
import { useI18n } from "@/hooks/useI18n";

interface TraceContributionListProps {
  contributions: RecallTraceScoreContribution[];
}

function formatScore(value: number): string {
  return value.toFixed(3);
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
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
  const { t } = useI18n();

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
            <span className="text-xs font-medium text-[var(--text-primary)]">{item.source}</span>
            <span className="text-2xs tabular-nums text-[var(--text-secondary)]">
              {t("intelligence.trace.contributionScore", formatScore(item.score), formatPercent(item.weight))}
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
