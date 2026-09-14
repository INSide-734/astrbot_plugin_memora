import { RefreshCw } from "lucide-react";

import { MetricGrid } from "@/components/layout/PageLayout";
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
import { useMemoryDedupMetrics } from "@/hooks/useMemoryDedupMetrics";
import {
  dashboardLocale,
  formatDashboardDateTime,
  formatDashboardNumber,
  formatDashboardPercent,
} from "@/lib/i18n";
import type {
  MemoryDedupMode,
  MemoryDedupOutcome,
  MemoryDedupSummaryWindow,
} from "@/types/memoryDedup";

export function MemoryDedupMetrics() {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const summary = useMemoryDedupMetrics();
  const data = summary.data;

  const windowItems: Array<{ value: MemoryDedupSummaryWindow; label: string }> = [
    { value: "1h", label: t("memoryDedup.window.1h") },
    { value: "24h", label: t("memoryDedup.window.24h") },
    { value: "7d", label: t("memoryDedup.window.7d") },
    { value: "30d", label: t("memoryDedup.window.30d") },
  ];

  const outcomeLabels: Array<[MemoryDedupOutcome, string]> = [
    ["checked", t("memoryDedup.outcome.checked")],
    ["hit", t("memoryDedup.outcome.hit")],
    ["merged", t("memoryDedup.outcome.merged")],
    ["fact_mismatch", t("memoryDedup.outcome.fact_mismatch")],
    ["fact_overlap", t("memoryDedup.outcome.fact_overlap")],
    ["conflict", t("memoryDedup.outcome.conflict")],
    ["failed", t("memoryDedup.outcome.failed")],
  ];

  const modeLabels: Array<[MemoryDedupMode, string]> = [
    ["observe", t("memoryDedup.mode.observe")],
    ["enforce", t("memoryDedup.mode.enforce")],
  ];

  const metrics = data
    ? [
        {
          label: t("memoryDedup.checked"),
          value: formatDashboardNumber(data.checked, locale),
        },
        {
          label: t("memoryDedup.hitRate"),
          value: formatDashboardPercent(data.hit_rate, locale, {
            maximumFractionDigits: 1,
          }),
        },
        {
          label: t("memoryDedup.merged"),
          value: formatDashboardNumber(data.merged, locale),
        },
        {
          label: t("memoryDedup.factMismatch"),
          value: formatDashboardNumber(data.fact_mismatch, locale),
        },
        {
          label: t("memoryDedup.factOverlap"),
          value: formatDashboardNumber(data.fact_overlap, locale),
        },
        {
          label: t("memoryDedup.guardRate"),
          value: formatDashboardPercent(data.guard_rate, locale, {
            maximumFractionDigits: 1,
          }),
        },
        {
          label: t("memoryDedup.overlapRate"),
          value: formatDashboardPercent(data.overlap_rate, locale, {
            maximumFractionDigits: 1,
          }),
        },
        {
          label: t("memoryDedup.failureRate"),
          value: formatDashboardPercent(data.failure_rate, locale, {
            maximumFractionDigits: 1,
          }),
        },
      ]
    : [];

  const hasData = data !== null && (data.checked > 0 || data.trend.length > 0);

  return (
    <div className="flex flex-col gap-4 rounded-lg border bg-card p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">{t("memoryDedup.title")}</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("memoryDedup.description")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select
            value={summary.windowValue}
            onValueChange={(value) => {
              if (value) {
                summary.setWindowValue(value as MemoryDedupSummaryWindow);
              }
            }}
          >
            <SelectTrigger aria-label={t("memoryDedup.windowLabel")}>
              {windowItems.find((item) => item.value === summary.windowValue)
                ?.label ?? t("memoryDedup.window.24h")}
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
            onClick={() => void summary.refresh()}
            disabled={summary.status === "loading"}
          >
            <RefreshCw className="mr-2 h-4 w-4" />
            {t("memoryDedup.refresh")}
          </Button>
        </div>
      </div>

      {!data && summary.status === "error" && (
        <StatePanel
          state="error"
          title={t("memoryDedup.state.error")}
          description={summary.error ?? undefined}
          actionLabel={t("common.retry")}
          onAction={() => void summary.refresh()}
        />
      )}
      {!data && summary.status !== "error" && (
        <StatePanel state="loading" title={t("memoryDedup.state.loading")} />
      )}
      {data && !hasData && (
        <StatePanel
          state="empty"
          title={t("memoryDedup.state.empty")}
          description={t("memoryDedup.state.emptyHint")}
        />
      )}

      {data && hasData && (
        <>
          <MetricGrid minItemWidth="9rem">
            {metrics.map((metric) => (
              <Card key={metric.label} size="sm">
                <CardHeader>
                  <CardDescription>{metric.label}</CardDescription>
                  <CardTitle className="break-words tabular-nums">
                    {metric.value}
                  </CardTitle>
                </CardHeader>
              </Card>
            ))}
          </MetricGrid>

          <div className="grid min-w-0 gap-4 lg:grid-cols-2">
            <Card className="min-w-0">
              <CardHeader>
                <CardTitle>{t("memoryDedup.trend")}</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="flex flex-col gap-2">
                  {data.trend.slice(-12).map((point) => (
                    <li
                      key={point.bucket_ms}
                      className="rounded-md border bg-muted/20 px-3 py-2"
                    >
                      <div className="text-xs tabular-nums text-muted-foreground">
                        {formatDashboardDateTime(point.bucket_ms, locale)}
                      </div>
                      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                        {outcomeLabels.map(([outcome, label]) => (
                          <span key={outcome} className="tabular-nums">
                            <span className="text-muted-foreground">
                              {label}{" "}
                            </span>
                            <strong className="font-medium">
                              {formatDashboardNumber(point[outcome], locale)}
                            </strong>
                          </span>
                        ))}
                      </div>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>

            <Card className="min-w-0">
              <CardHeader>
                <CardTitle>{t("memoryDedup.byMode")}</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                {modeLabels.map(([mode, label]) => (
                  <div key={mode} className="min-w-0">
                    <div className="text-xs font-medium text-muted-foreground">
                      {label}
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
                      {outcomeLabels.map(([outcome, outcomeLabel]) => (
                        <div
                          key={outcome}
                          className="rounded-md border bg-muted/30 px-3 py-2"
                        >
                          <div className="text-xs text-muted-foreground">
                            {outcomeLabel}
                          </div>
                          <div className="mt-1 text-sm font-medium tabular-nums">
                            {formatDashboardNumber(
                              data.by_mode[mode][outcome],
                              locale
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
                <p className="text-xs text-muted-foreground">
                  {t("memoryDedup.outcomeHint")}
                </p>
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
