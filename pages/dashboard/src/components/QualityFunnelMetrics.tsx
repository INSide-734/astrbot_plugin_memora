import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/Button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import { StatePanel } from "@/components/ui/StatePanel";
import { useI18n } from "@/hooks/useI18n";
import { useQualityFunnelMetrics } from "@/hooks/useQualityFunnelMetrics";
import {
  dashboardLocale,
  formatDashboardNumber,
  formatDashboardPercent,
} from "@/lib/i18n";
import type {
  QualityFunnelStage,
  QualityFunnelStageId,
  QualityFunnelTrendPoint,
  QualityFunnelWindow,
} from "@/types/qualityFunnel";

/** 面板展示白名单：只渲染固定计数/标量/比率，数据仍由 hook 归一化。 */
const STAGE_SPECS: Array<{
  id: QualityFunnelStageId;
  labelKey: string;
  counts: string[];
  values: string[];
  rates: string[];
}> = [
  {
    id: "candidates",
    labelKey: "qualityFunnel.stage.candidates",
    counts: [
      "candidates",
      "exact_reuse",
      "identity_drops",
      "budget_exceeded",
      "catalog_degraded",
    ],
    values: [],
    rates: ["reuse_rate", "degraded_rate"],
  },
  {
    id: "facts",
    labelKey: "qualityFunnel.stage.facts",
    counts: ["canonical", "merged", "quarantined", "discarded", "facts_rejected"],
    values: [],
    rates: ["merge_rate", "discard_rate"],
  },
  {
    id: "dedup",
    labelKey: "qualityFunnel.stage.dedup",
    counts: ["checked", "hit", "merged", "fact_mismatch", "fact_overlap"],
    values: [],
    rates: ["hit_rate", "guard_rate", "overlap_rate", "failure_rate"],
  },
  {
    id: "injection",
    labelKey: "qualityFunnel.stage.injection",
    counts: ["decisions", "selected", "dropped", "memory_present", "payload_injected"],
    values: ["budget_utilization"],
    rates: ["memory_present_rate", "payload_injected_rate"],
  },
];

const TREND_FIELDS = [
  "candidates",
  "canonical",
  "merged",
  "facts_rejected",
  "dedup_checked",
  "dedup_hit",
  "decisions",
  "selected",
] as const;

export function QualityFunnelMetrics() {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const funnel = useQualityFunnelMetrics();
  const data = funnel.data;

  const windowItems: Array<{ value: QualityFunnelWindow; label: string }> = [
    { value: "1h", label: t("qualityFunnel.window.1h") },
    { value: "24h", label: t("qualityFunnel.window.24h") },
    { value: "7d", label: t("qualityFunnel.window.7d") },
    { value: "30d", label: t("qualityFunnel.window.30d") },
  ];

  const stateLabels: Record<QualityFunnelStage["state"], string> = {
    available: t("qualityFunnel.state.available"),
    degraded: t("qualityFunnel.state.degraded"),
    unavailable: t("qualityFunnel.state.unavailable"),
  };

  const stageMetrics = (stage: QualityFunnelStage, spec: (typeof STAGE_SPECS)[number]) => {
    if (stage.counts === null) return [];
    const counts = stage.counts as Record<string, number>;
    const values = (stage.values ?? {}) as Record<string, number>;
    const rates = (stage.rates ?? {}) as Record<string, number>;
    return [
      ...spec.counts.map((key) => ({
        key,
        label: t(`qualityFunnel.count.${spec.id}.${key}`),
        value: formatDashboardNumber(counts[key] ?? 0, locale),
      })),
      ...spec.values.map((key) => ({
        key,
        label: t(`qualityFunnel.value.${spec.id}.${key}`),
        value: formatDashboardPercent(values[key] ?? 0, locale, {
          maximumFractionDigits: 1,
        }),
      })),
      ...spec.rates.map((key) => ({
        key,
        label: t(`qualityFunnel.rate.${spec.id}.${key}`),
        value: formatDashboardPercent(rates[key] ?? 0, locale, {
          maximumFractionDigits: 1,
        }),
      })),
    ];
  };

  const trendColumns: Array<[keyof QualityFunnelTrendPoint, string]> = [
    ["candidates", t("qualityFunnel.trend.candidates")],
    ["canonical", t("qualityFunnel.trend.canonical")],
    ["merged", t("qualityFunnel.trend.merged")],
    ["facts_rejected", t("qualityFunnel.trend.facts_rejected")],
    ["dedup_checked", t("qualityFunnel.trend.dedup_checked")],
    ["dedup_hit", t("qualityFunnel.trend.dedup_hit")],
    ["decisions", t("qualityFunnel.trend.decisions")],
    ["selected", t("qualityFunnel.trend.selected")],
  ];

  const hasStages = (data?.stages.length ?? 0) > 0;

  return (
    <div className="flex flex-col gap-4 rounded-lg border bg-card p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">{t("qualityFunnel.title")}</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("qualityFunnel.description")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select
            value={funnel.windowValue}
            onValueChange={(value) => {
              if (value) {
                funnel.setWindowValue(value as QualityFunnelWindow);
              }
            }}
          >
            <SelectTrigger aria-label={t("qualityFunnel.windowLabel")}>
              {windowItems.find((item) => item.value === funnel.windowValue)
                ?.label ?? t("qualityFunnel.window.24h")}
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                {windowItems.map((item) => (
                  <SelectItem key={item.value} value={item.value}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void funnel.refresh()}
            disabled={funnel.status === "loading"}
          >
            <RefreshCw className="mr-2 h-4 w-4" />
            {t("qualityFunnel.refresh")}
          </Button>
        </div>
      </div>

      <p className="text-xs text-muted-foreground">{t("qualityFunnel.advisory")}</p>

      {!data && funnel.status === "error" && (
        <StatePanel
          state="error"
          title={t("qualityFunnel.state.error")}
          description={funnel.error ?? undefined}
          actionLabel={t("common.retry")}
          onAction={() => void funnel.refresh()}
        />
      )}
      {!data && funnel.status !== "error" && (
        <StatePanel state="loading" title={t("qualityFunnel.state.loading")} />
      )}
      {data && !hasStages && (
        <StatePanel
          state="empty"
          title={t("qualityFunnel.state.empty")}
          description={t("qualityFunnel.state.emptyHint")}
        />
      )}

      {data && hasStages && (
        <>
          <div className="grid min-w-0 gap-4 lg:grid-cols-2">
            {data.stages.map((stage) => {
              const spec = STAGE_SPECS.find((item) => item.id === stage.id);
              if (!spec) return null;
              return (
                <Card key={spec.id} className="min-w-0">
                  <CardHeader>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <CardTitle>{t(spec.labelKey)}</CardTitle>
                      <span
                        data-state={stage.state}
                        className="rounded-full border px-2 py-0.5 text-xs font-medium text-muted-foreground"
                      >
                        {stateLabels[stage.state]}
                      </span>
                    </div>
                    {stage.state !== "available" && (
                      <CardDescription className="font-mono text-xs">
                        {t("qualityFunnel.reasonLabel")}: {stage.reason}
                      </CardDescription>
                    )}
                  </CardHeader>
                  <CardContent>
                    {stage.counts === null ? (
                      <p className="text-xs text-muted-foreground">
                        {t("qualityFunnel.countsUnavailable")}
                      </p>
                    ) : (
                      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                        {stageMetrics(stage, spec).map((metric) => (
                          <div
                            key={metric.key}
                            className="rounded-md border bg-muted/30 px-3 py-2"
                          >
                            <div className="text-xs text-muted-foreground">
                              {metric.label}
                            </div>
                            <div className="mt-1 text-sm font-medium tabular-nums">
                              {metric.value}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>

          <Card className="min-w-0">
            <CardHeader>
              <CardTitle>{t("qualityFunnel.trendTitle")}</CardTitle>
            </CardHeader>
            <CardContent>
              {data.trend.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  {t("qualityFunnel.trendEmpty")}
                </p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {data.trend.slice(-14).map((row) => (
                    <li
                      key={row.day}
                      className="rounded-md border bg-muted/20 px-3 py-2"
                    >
                      <div className="text-xs tabular-nums text-muted-foreground">
                        {row.day}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                        {trendColumns.map(([field, label]) => (
                          <span key={field} className="tabular-nums">
                            <span className="text-muted-foreground">
                              {label}{" "}
                            </span>
                            <strong className="font-medium">
                              {formatDashboardNumber(row[field], locale)}
                            </strong>
                          </span>
                        ))}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
