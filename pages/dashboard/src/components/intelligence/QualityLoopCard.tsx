import type { QualityLoopPayload, QualityLoopStage } from "@/types/intelligence";
import { useI18n } from "@/hooks/useI18n";
import { dashboardLocale, formatDashboardNumber, formatDashboardPercent } from "@/lib/i18n";

interface QualityLoopCardProps {
  qualityLoop: QualityLoopPayload;
}

const PERCENT_METRICS: Record<string, true> = {
  write_fact_correctness: true,
  source_faithfulness: true,
  source_evidence_completeness: true,
  candidate_hit_rate: true,
  final_injected_hit_rate: true,
  negative_injection_rate: true,
  annotated_answer_faithfulness: true,
  annotated_answer_relevancy: true,
};

function formatStageMetric(
  key: string,
  value: number | null | undefined,
  locale: string,
  unmeasured: string,
): string {
  if (value === null || value === undefined) return unmeasured;
  if (PERCENT_METRICS[key]) {
    return formatDashboardPercent(value, locale, { maximumFractionDigits: 1 });
  }
  const formatted = formatDashboardNumber(value, locale, { maximumFractionDigits: 1 });
  return key.endsWith("_latency_ms") && formatted !== "--" ? `${formatted}ms` : formatted;
}

function StageBadge({ state }: { state: string }) {
  const { t } = useI18n();
  const tone =
    state === "available"
      ? "bg-[var(--color-success)]/10 text-[var(--color-success)]"
      : state === "degraded"
        ? "bg-[var(--color-warning)]/10 text-[var(--color-warning)]"
        : "bg-[var(--color-border)]/30 text-[var(--text-tertiary)]";
  return (
    <span className={`rounded-full px-1.5 py-0.5 text-2xs font-medium ${tone}`}>
      {t(`intelligence.evaluation.qualityLoop.state.${state}`)}
    </span>
  );
}

/** 阶段指标按来源单位格式化；null 显示「未测量」，真实 0 保留为 0。 */
function StageMetricRow({
  metricKey,
  label,
  value,
  locale,
  unmeasured,
}: {
  metricKey: string;
  label: string;
  value: number | null | undefined;
  locale: string;
  unmeasured: string;
}) {
  const display = formatStageMetric(metricKey, value, locale, unmeasured);
  return (
    <div className="flex items-center justify-between gap-3 py-1">
      <span className="text-2xs text-[var(--text-tertiary)]">{label}</span>
      <span className="text-2xs font-medium tabular-nums text-[var(--text-secondary)]">{display}</span>
    </div>
  );
}

function StagePanel({ stage }: { stage: QualityLoopStage }) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const unmeasured = t("intelligence.evaluation.qualityLoop.unmeasured");
  const stageLabel = t(`intelligence.evaluation.qualityLoop.stage.${stage.stage}`);
  const reasonLabel =
    stage.reason !== "ok"
      ? t(`intelligence.evaluation.qualityLoop.reason.${stage.reason}`)
      : null;
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)] p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h5 className="text-xs font-semibold text-[var(--text-primary)]">{stageLabel}</h5>
        <StageBadge state={stage.state} />
      </div>
      {reasonLabel ? (
        <p className="mb-1 text-2xs text-[var(--text-tertiary)]">{reasonLabel}</p>
      ) : null}
      {Object.entries(stage.metrics).length === 0 ? (
        <p className="py-1 text-2xs text-[var(--text-tertiary)]">{unmeasured}</p>
      ) : (
        Object.entries(stage.metrics).map(([key, value]) => (
          <StageMetricRow
            key={key}
            metricKey={key}
            label={t(`intelligence.evaluation.qualityLoop.metric.${key}`)}
            value={value}
            locale={locale}
            unmeasured={unmeasured}
          />
        ))
      )}
    </div>
  );
}

export function QualityLoopCard({ qualityLoop }: QualityLoopCardProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const unmeasured = t("intelligence.evaluation.qualityLoop.unmeasured");
  const manifest = qualityLoop.manifest;
  const pairs = qualityLoop.pairs ?? {
    total_pairs: 0,
    should_use_hit_rate: null,
    should_silence_correct_rate: null,
  };
  const stages = Array.isArray(qualityLoop.stages) ? qualityLoop.stages : [];
  const rejected = Object.values(pairs.reject_reason_counts ?? {}).reduce(
    (sum, count) => sum + count,
    0,
  );

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--color-border)] px-4 py-3">
        <h4 className="text-sm font-semibold text-[var(--text-primary)]">
          {t("intelligence.evaluation.qualityLoop.title")}
        </h4>
        {manifest ? (
          <span className="font-mono text-2xs text-[var(--text-tertiary)]">
            {t("intelligence.evaluation.qualityLoop.seed")}={manifest.seed}
            {" · "}
            {t("intelligence.evaluation.qualityLoop.pairCount")}={manifest.pair_count}
          </span>
        ) : null}
      </div>
      <div className="space-y-4 p-4">
        {manifest ? (
          <div>
            <p className="mb-2 text-2xs uppercase text-[var(--text-tertiary)]">
              {t("intelligence.evaluation.qualityLoop.manifest")}
            </p>
            <div className="grid gap-1 font-mono text-2xs text-[var(--text-tertiary)] sm:grid-cols-2">
              <span className="truncate">code={manifest.code_revision}</span>
              <span className="truncate">config={manifest.config_hash.slice(0, 12)}</span>
              <span className="truncate">schema={manifest.schema_hash.slice(0, 12)}</span>
              <span className="truncate">fixture={manifest.fixture_hash.slice(0, 12)}</span>
              <span className="truncate">manifest={manifest.manifest_hash.slice(0, 12)}</span>
              <span className="truncate">
                db={manifest.db_snapshot_hash ? manifest.db_snapshot_hash.slice(0, 12) : unmeasured}
              </span>
            </div>
          </div>
        ) : (
          <p className="text-xs text-[var(--text-tertiary)]">
            {t("intelligence.evaluation.qualityLoop.noManifest")}
          </p>
        )}

        <div>
          <p className="mb-2 text-2xs uppercase text-[var(--text-tertiary)]">
            {t("intelligence.evaluation.qualityLoop.pairs")}
          </p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              [
                t("intelligence.evaluation.qualityLoop.totalPairs"),
                String(pairs.total_pairs),
              ],
              [
                t("intelligence.evaluation.qualityLoop.shouldUseHit"),
                pairs.should_use_hit_rate === null || pairs.should_use_hit_rate === undefined
                  ? unmeasured
                  : formatDashboardPercent(pairs.should_use_hit_rate, locale),
              ],
              [
                t("intelligence.evaluation.qualityLoop.shouldSilenceCorrect"),
                pairs.should_silence_correct_rate === null || pairs.should_silence_correct_rate === undefined
                  ? unmeasured
                  : formatDashboardPercent(pairs.should_silence_correct_rate, locale),
              ],
              [
                t("intelligence.evaluation.qualityLoop.rejected"),
                String(rejected),
              ],
            ].map(([label, value]) => (
              <div
                key={label}
                className="rounded-lg border border-[var(--color-border)] p-2"
              >
                <p className="text-2xs text-[var(--text-tertiary)]">{label}</p>
                <p className="mt-1 text-sm font-semibold tabular-nums text-[var(--text-primary)]">
                  {value}
                </p>
              </div>
            ))}
          </div>
        </div>

        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {stages.map((stage) => (
            <StagePanel key={stage.stage} stage={stage} />
          ))}
        </div>
      </div>
    </div>
  );
}
