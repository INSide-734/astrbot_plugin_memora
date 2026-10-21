import { useState } from "react";
import { ShieldCheck } from "lucide-react";

import { useI18n } from "@/hooks/useI18n";
import {
  dashboardLocale,
  formatDashboardNumber,
  formatDashboardPercent,
  translateEnum,
} from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type {
  EffectGateFactor,
  EffectGateFactorName,
  EffectGateMetric,
  EffectGateMetricName,
  EffectGateReport,
} from "@/types/intelligence";

const METRIC_NAMES: EffectGateMetricName[] = [
  "final_injected_hit_rate",
  "injection_precision",
  "required_fact_coverage",
  "negative_injection_rate",
  "observed_latency_ms",
  "observed_provider_calls",
];
const RATE_METRICS: Record<string, true> = {
  final_injected_hit_rate: true,
  injection_precision: true,
  required_fact_coverage: true,
  negative_injection_rate: true,
};
const STATUS_TONE: Record<string, string> = {
  pass_enable: "bg-[var(--color-success)]/10 text-[var(--color-success)]",
  pass_disable: "bg-[var(--color-accent)]/10 text-[var(--color-accent)]",
  reject: "bg-[var(--color-danger)]/10 text-[var(--color-danger)]",
  insufficient_evidence: "bg-[var(--color-warning)]/10 text-[var(--color-warning)]",
  not_applicable: "bg-[var(--color-border)]/30 text-[var(--text-tertiary)]",
};

function formatGateValue(
  metric: EffectGateMetricName,
  value: number | null | undefined,
  locale: string,
  unmeasured: string,
): string {
  if (value === null || value === undefined) return unmeasured;
  if (RATE_METRICS[metric]) {
    return formatDashboardPercent(value, locale, { maximumFractionDigits: 1 });
  }
  const formatted = formatDashboardNumber(value, locale, {
    minimumFractionDigits: metric === "observed_provider_calls" ? 2 : 1,
    maximumFractionDigits: metric === "observed_provider_calls" ? 2 : 1,
  });
  return metric === "observed_latency_ms" ? `${formatted}ms` : formatted;
}

function formatGateDelta(
  metric: EffectGateMetricName,
  value: number | null | undefined,
  locale: string,
  unmeasured: string,
): string {
  if (value === null || value === undefined) return unmeasured;
  if (RATE_METRICS[metric]) {
    return formatDashboardPercent(value, locale, {
      maximumFractionDigits: 1,
      signDisplay: "exceptZero",
    });
  }
  const formatted = formatDashboardNumber(value, locale, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
    signDisplay: "exceptZero",
  });
  return metric === "observed_latency_ms" ? `${formatted}ms` : formatted;
}

function formatGateInterval(
  metric: EffectGateMetricName,
  item: EffectGateMetric,
  locale: string,
  unmeasured: string,
): string {
  if (item.ci_low === null || item.ci_low === undefined) return unmeasured;
  if (item.ci_high === null || item.ci_high === undefined) return unmeasured;
  return `[${formatGateValue(metric, item.ci_low, locale, unmeasured)}, ${formatGateValue(
    metric,
    item.ci_high,
    locale,
    unmeasured,
  )}]`;
}

function formatHash(value: string | null | undefined, unmeasured: string): string {
  if (!value) return unmeasured;
  return `${value.slice(0, 12)}…`;
}

function StatusBadge({ status }: { status: string }) {
  const { t } = useI18n();
  return (
    <span
      className={cn(
        "rounded-full px-1.5 py-0.5 text-2xs font-medium",
        STATUS_TONE[status] ?? STATUS_TONE.not_applicable,
      )}
    >
      {translateEnum(
        t,
        "intelligence.evaluation.effectGate.status",
        status,
        status,
      )}
    </span>
  );
}

function MetricRow({
  metricKey,
  metric,
  locale,
  unmeasured,
}: {
  metricKey: EffectGateMetricName;
  metric: EffectGateMetric | undefined;
  locale: string;
  unmeasured: string;
}) {
  const { t } = useI18n();
  const available = metric?.available === true;
  return (
    <tr className="border-t border-[var(--color-border-light)]">
      <td className="px-3 py-2 text-[var(--text-secondary)]">
        {t(`intelligence.evaluation.effectGate.metric.${metricKey}`)}
      </td>
      <td className="px-3 py-2 tabular-nums text-[var(--text-secondary)]">
        {available ? formatGateValue(metricKey, metric?.baseline, locale, unmeasured) : unmeasured}
      </td>
      <td className="px-3 py-2 tabular-nums text-[var(--text-secondary)]">
        {available ? formatGateValue(metricKey, metric?.variant, locale, unmeasured) : unmeasured}
      </td>
      <td className="px-3 py-2 tabular-nums text-[var(--text-secondary)]">
        {available ? formatGateDelta(metricKey, metric?.delta, locale, unmeasured) : unmeasured}
      </td>
      <td className="px-3 py-2 tabular-nums text-[var(--text-tertiary)]">
        {available && metric ? formatGateInterval(metricKey, metric, locale, unmeasured) : unmeasured}
      </td>
      <td className="px-3 py-2 tabular-nums text-[var(--text-tertiary)]">
        {available ? formatGateValue(metricKey, metric?.margin, locale, unmeasured) : unmeasured}
      </td>
    </tr>
  );
}

function FactorCard({
  name,
  factor,
  active,
  onSelect,
}: {
  name: EffectGateFactorName | string;
  factor: EffectGateFactor;
  active: boolean;
  onSelect: () => void;
}) {
  const { t } = useI18n();
  return (
    <button
      type="button"
      data-effect-gate-factor={name}
      aria-pressed={active}
      onClick={onSelect}
      className={cn(
        "min-w-0 rounded-lg border p-3 text-left transition-colors",
        active
          ? "border-[var(--color-accent)] bg-[var(--color-accent)]/5"
          : "border-[var(--color-border)] bg-[var(--color-surface-secondary)] hover:border-[var(--color-border)] hover:bg-[var(--color-surface)]",
      )}
    >
      <span className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-semibold text-[var(--text-primary)]">
          {translateEnum(
            t,
            "intelligence.evaluation.effectGate.factor",
            name,
            String(name),
          )}
        </span>
        <StatusBadge status={String(factor.status)} />
      </span>
      <span className="mt-2 block text-2xs text-[var(--text-secondary)]">
        {translateEnum(
          t,
          "intelligence.evaluation.effectGate.recommendation",
          factor.recommendation,
          String(factor.recommendation),
        )}
      </span>
      <span className="mt-2 flex flex-wrap gap-1">
        {factor.reason_codes.map((reason) => (
          <span
            key={reason}
            className="rounded bg-[var(--color-border-light)] px-1.5 py-0.5 text-2xs text-[var(--text-tertiary)]"
          >
            {translateEnum(
              t,
              "intelligence.evaluation.effectGate.reason",
              reason,
              reason,
            )}
          </span>
        ))}
      </span>
    </button>
  );
}

function ManifestGrid({ gate }: { gate: EffectGateReport }) {
  const { t } = useI18n();
  const manifest = gate.manifest;
  return (
    <div className="grid gap-2 text-2xs sm:grid-cols-2 xl:grid-cols-3">
      {[
        ["codeRevision", manifest.code_revision],
        ["configHash", manifest.config_hash],
        ["schemaHash", manifest.schema_hash],
        ["fixtureHmac", manifest.fixture_hmac],
        ["dbSnapshotHash", manifest.db_snapshot_hash],
        ["injectionConfigHash", manifest.injection_config_hash],
      ].map(([label, value]) => (
        <div key={label} className="min-w-0 rounded border border-[var(--color-border-light)] p-2">
          <p className="text-[var(--text-tertiary)]">
            {t(`intelligence.evaluation.effectGate.manifest.${label}`)}
          </p>
          <p className="mt-1 truncate font-mono text-[var(--text-secondary)]" title={value ?? undefined}>
            {formatHash(value, t("intelligence.evaluation.effectGate.unmeasured"))}
          </p>
        </div>
      ))}
      <div className="rounded border border-[var(--color-border-light)] p-2">
        <p className="text-[var(--text-tertiary)]">
          {t("intelligence.evaluation.effectGate.manifest.seedK")}
        </p>
        <p className="mt-1 tabular-nums text-[var(--text-secondary)]">
          {manifest.seed} / {manifest.k} / {manifest.case_count}
        </p>
      </div>
      <div className="rounded border border-[var(--color-border-light)] p-2">
        <p className="text-[var(--text-tertiary)]">
          {t("intelligence.evaluation.effectGate.manifest.policyVersion")}
        </p>
        <p className="mt-1 text-[var(--text-secondary)]">{manifest.gate_policy_version}</p>
      </div>
    </div>
  );
}

export function EffectGateCard({ gate }: { gate: EffectGateReport }) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const unmeasured = t("intelligence.evaluation.effectGate.unmeasured");
  const factorNames = Object.keys(gate.factors) as EffectGateFactorName[];
  const [selected, setSelected] = useState<EffectGateFactorName>("atom");
  const activeName = factorNames.includes(selected) ? selected : factorNames[0];
  const active = activeName ? gate.factors[activeName] : undefined;
  const readiness = gate.readiness;
  const categories = Object.entries(active?.categories ?? {});
  const configEntries = Object.entries(gate.recommended_config);

  return (
    <section
      data-effect-gate-card
      className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-secondary)]"
    >
      <div className="flex flex-wrap items-start justify-between gap-2 border-b border-[var(--color-border)] px-4 py-3">
        <div className="flex min-w-0 items-start gap-2">
          <ShieldCheck size={15} className="mt-0.5 shrink-0 text-[var(--accent)]" />
          <div className="min-w-0">
            <h4 className="text-sm font-semibold text-[var(--text-primary)]">
              {t("intelligence.evaluation.effectGate.title")}
            </h4>
            <p className="mt-1 text-2xs text-[var(--text-tertiary)]">
              {t("intelligence.evaluation.effectGate.description")}
            </p>
          </div>
        </div>
        <span
          className={cn(
            "rounded-full px-2 py-0.5 text-2xs font-medium",
            readiness.can_evaluate
              ? "bg-[var(--color-success)]/10 text-[var(--color-success)]"
              : "bg-[var(--color-warning)]/10 text-[var(--color-warning)]",
          )}
        >
          {t(
            readiness.can_evaluate
              ? "intelligence.evaluation.effectGate.ready"
              : "intelligence.evaluation.effectGate.insufficient",
          )}
        </span>
      </div>

      <div className="space-y-4 p-4">
        <div className="rounded-lg border border-[var(--color-border-light)] p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h5 className="text-xs font-semibold text-[var(--text-primary)]">
              {t("intelligence.evaluation.effectGate.readiness")}
            </h5>
            <span className="text-2xs tabular-nums text-[var(--text-tertiary)]">
              {t("intelligence.evaluation.effectGate.totalCases", String(readiness.total_cases))}
            </span>
          </div>
          <div className="mt-2 grid grid-cols-2 gap-1.5 sm:grid-cols-4">
            {readiness.required_categories.map((category) => (
              <div
                key={category}
                className="flex items-center justify-between gap-2 rounded bg-[var(--color-surface)] px-2 py-1 text-2xs"
              >
                <span className="truncate text-[var(--text-tertiary)]">
                  {translateEnum(
                    t,
                    "intelligence.evaluation.effectGate.category",
                    category,
                    category,
                  )}
                </span>
                <span className="tabular-nums text-[var(--text-secondary)]">
                  {readiness.present_counts[category] ?? 0}
                </span>
              </div>
            ))}
          </div>
          {readiness.missing.length > 0 ? (
            <p className="mt-2 text-2xs text-[var(--color-warning)]">
              {t("intelligence.evaluation.effectGate.missing")}:{" "}
              {readiness.missing
                .map((category) =>
                  translateEnum(
                    t,
                    "intelligence.evaluation.effectGate.category",
                    category,
                    category,
                  ),
                )
                .join(", ")}
            </p>
          ) : null}
        </div>

        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {factorNames.map((name) => {
            const factor = gate.factors[name];
            if (!factor) return null;
            return (
              <FactorCard
                key={name}
                name={name}
                factor={factor}
                active={activeName === name}
                onSelect={() => setSelected(name)}
              />
            );
          })}
        </div>

        {active ? (
          <div className="overflow-hidden rounded-lg border border-[var(--color-border-light)]">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--color-border-light)] px-3 py-2">
              <h5 className="text-xs font-semibold text-[var(--text-primary)]">
                {translateEnum(
                  t,
                  "intelligence.evaluation.effectGate.factor",
                  active.factor,
                  String(active.factor),
                )}{" "}
                · {t("intelligence.evaluation.effectGate.metrics")}
              </h5>
              <span className="text-2xs text-[var(--text-tertiary)]">
                {t("intelligence.evaluation.effectGate.variant")}: {active.variant}
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-left text-xs">
                <thead className="text-[var(--text-tertiary)]">
                  <tr>
                    <th className="px-3 py-2 font-medium">{t("intelligence.evaluation.effectGate.metricName")}</th>
                    <th className="px-3 py-2 font-medium">{t("intelligence.evaluation.effectGate.baseline")}</th>
                    <th className="px-3 py-2 font-medium">{t("intelligence.evaluation.effectGate.variant")}</th>
                    <th className="px-3 py-2 font-medium">{t("intelligence.evaluation.effectGate.delta")}</th>
                    <th className="px-3 py-2 font-medium">{t("intelligence.evaluation.effectGate.interval")}</th>
                    <th className="px-3 py-2 font-medium">{t("intelligence.evaluation.effectGate.margin")}</th>
                  </tr>
                </thead>
                <tbody>
                  {METRIC_NAMES.map((metricName) => (
                    <MetricRow
                      key={metricName}
                      metricKey={metricName}
                      metric={active.metrics[metricName]}
                      locale={locale}
                      unmeasured={unmeasured}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}

        <div className="overflow-hidden rounded-lg border border-[var(--color-border-light)]">
          <div className="border-b border-[var(--color-border-light)] px-3 py-2">
            <h5 className="text-xs font-semibold text-[var(--text-primary)]">
              {t("intelligence.evaluation.effectGate.categoryTable")}
            </h5>
          </div>
          {categories.length === 0 ? (
            <p className="px-3 py-3 text-2xs text-[var(--text-tertiary)]">
              {t("intelligence.evaluation.effectGate.noCategories")}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-left text-xs">
                <thead className="text-[var(--text-tertiary)]">
                  <tr>
                    <th className="px-3 py-2 font-medium">{t("intelligence.evaluation.effectGate.categoryName")}</th>
                    <th className="px-3 py-2 font-medium">{t("intelligence.evaluation.effectGate.cases")}</th>
                    {METRIC_NAMES.map((metric) => (
                      <th key={metric} className="px-3 py-2 font-medium">
                        {t(`intelligence.evaluation.effectGate.metric.${metric}`)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {categories.map(([category, item]) => item ? (
                    <tr key={category} className="border-t border-[var(--color-border-light)]">
                      <td className="px-3 py-2 text-[var(--text-secondary)]">
                        {translateEnum(
                          t,
                          "intelligence.evaluation.effectGate.category",
                          category,
                          category,
                        )}
                      </td>
                      <td className="px-3 py-2 tabular-nums text-[var(--text-secondary)]">
                        {item.case_count}
                      </td>
                      {METRIC_NAMES.map((metric) => (
                        <td
                          key={metric}
                          className="px-3 py-2 tabular-nums text-[var(--text-secondary)]"
                        >
                          {formatGateValue(
                            metric,
                            item[metric]?.variant,
                            locale,
                            unmeasured,
                          )}
                        </td>
                      ))}
                    </tr>
                  ) : null)}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="grid gap-3 lg:grid-cols-2">
          <div className="rounded-lg border border-[var(--color-border-light)] p-3">
            <h5 className="text-xs font-semibold text-[var(--text-primary)]">
              {t("intelligence.evaluation.effectGate.recommendedConfig")}
            </h5>
            <p className="mt-1 text-2xs text-[var(--text-tertiary)]">
              {t("intelligence.evaluation.effectGate.displayOnly")}
            </p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {configEntries.length === 0 ? (
                <span className="text-2xs text-[var(--text-tertiary)]">{unmeasured}</span>
              ) : (
                configEntries.map(([key, value]) => (
                  <span
                    key={key}
                    className="rounded border border-[var(--color-border-light)] bg-[var(--color-surface)] px-2 py-1 font-mono text-2xs text-[var(--text-secondary)]"
                  >
                    {translateEnum(
                      t,
                      "intelligence.evaluation.effectGate.config",
                      key,
                      key,
                    )}{" "}
                    = {String(value)}
                  </span>
                ))
              )}
            </div>
          </div>
          <div className="rounded-lg border border-[var(--color-border-light)] p-3">
            <h5 className="text-xs font-semibold text-[var(--text-primary)]">
              {t("intelligence.evaluation.effectGate.canary")}
            </h5>
            <p className="mt-1 text-2xs text-[var(--text-tertiary)]">
              {t("intelligence.evaluation.effectGate.canaryChecked", String(gate.privacy_canary.checked_values))}
            </p>
          </div>
        </div>

        <div>
          <h5 className="mb-2 text-xs font-semibold text-[var(--text-primary)]">
            {t("intelligence.evaluation.effectGate.manifest.title")}
          </h5>
          <ManifestGrid gate={gate} />
        </div>
      </div>
    </section>
  );
}
