import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  XAxis,
  YAxis,
} from "recharts";
import { GitBranch, SlidersHorizontal } from "lucide-react";

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
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { StatePanel } from "@/components/ui/StatePanel";
import { useI18n } from "@/hooks/useI18n";
import type { useInjectionStrategyConfig } from "@/hooks/useInjectionStrategyConfig";
import type { useInjectionStrategySummary } from "@/hooks/useInjectionStrategySummary";
import {
  dashboardLocale,
  formatDashboardDateTime,
  formatDashboardNumber,
  formatDashboardPercent,
  formatDashboardShortDate,
  translateEnum,
} from "@/lib/i18n";
import type { Translate } from "@/lib/i18n";
import type {
  InjectionDeliveryMode,
  InjectionPresetName,
  InjectionRecentEvent,
  InjectionStrategyCatalog,
  InjectionStrategyDraft,
  InjectionSummaryWindow,
} from "@/types/injection";

interface InjectionOverviewTabProps {
  config: ReturnType<typeof useInjectionStrategyConfig>;
  summary: ReturnType<typeof useInjectionStrategySummary>;
  onEdit: () => void;
  onOpenTrace: (traceId: string) => void;
}

interface EventListProps {
  events: InjectionRecentEvent[];
  label: string;
  locale: string;
  traceAvailable: boolean;
  t: Translate;
  onOpenTrace: (traceId: string) => void;
}

const WINDOW_OPTIONS: InjectionSummaryWindow[] = ["1h", "24h", "7d", "30d"];

function activePreset(draft: InjectionStrategyDraft): InjectionPresetName {
  if (draft.routingMode === "manual") return draft.manualPreset;
  if (draft.routingMode === "hybrid") return draft.hybridBasePreset;
  return draft.autoFallbackPreset;
}

function effectiveDelivery(
  _draft: InjectionStrategyDraft,
  catalog: InjectionStrategyCatalog,
): InjectionDeliveryMode {
  return catalog.effective_default_delivery;
}

function EventList({
  events,
  label,
  locale,
  onOpenTrace,
  t,
  traceAvailable,
}: EventListProps) {
  return (
    <Card size="sm" className="min-w-0">
      <CardHeader>
        <CardTitle>{label}</CardTitle>
      </CardHeader>
      <CardContent>
        {events.length === 0 ? (
          <p className="py-4 text-sm text-muted-foreground">
            {t("injection.overview.noEvents")}
          </p>
        ) : (
          <ul aria-label={label} className="flex min-w-0 flex-col">
            {events.map((event) => {
              const canOpenTrace = Boolean(event.trace_id && traceAvailable);
              return (
                <li
                  key={event.decision_id}
                  className="flex min-w-0 flex-wrap items-center gap-3 border-b py-3 last:border-b-0"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-xs text-muted-foreground">
                      {formatDashboardDateTime(event.created_at_ms, locale)}
                    </p>
                    <p className="mt-1 text-sm font-medium text-foreground">
                      {t(`injection.mode.${event.routing_mode}`)} · {t(`injection.preset.${event.resolved_preset}`)} · {t(`injection.outcome.${event.outcome}`)}
                    </p>
                    <p className="mt-1 break-words text-xs text-muted-foreground">
                      {translateEnum(
                        t,
                        "injection.reason",
                        event.primary_reason,
                        event.primary_reason,
                      )} · {formatDashboardNumber(event.actual_payload_chars, locale)}
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={!canOpenTrace}
                    onClick={() => {
                      if (event.trace_id) onOpenTrace(event.trace_id);
                    }}
                    aria-label={t("injection.actions.openTrace")}
                    title={event.trace_id
                      ? t("injection.actions.openTrace")
                      : t("injection.actions.traceUnavailable")}
                  >
                    <GitBranch />
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function InjectionOverviewTab({
  config,
  onEdit,
  onOpenTrace,
  summary,
}: InjectionOverviewTabProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());

  if (
    config.catalogStatus === "loading"
    || config.status === "loading"
    || summary.status === "loading"
  ) {
    return (
      <StatePanel state="loading" title={t("injection.state.loading")} />
    );
  }
  if (config.catalogStatus === "error" || !config.catalog) {
    return (
      <StatePanel
        state="error"
        title={t("injection.state.error")}
        description={config.catalogError ?? undefined}
        actionLabel={t("common.retry")}
        onAction={() => { void config.retryCatalog(); }}
      />
    );
  }
  if (config.status === "error" || !config.draft) {
    return (
      <StatePanel
        state="error"
        title={t("injection.state.error")}
        actionLabel={t("common.retry")}
        onAction={() => { void config.refresh(); }}
      />
    );
  }
  if (summary.status === "error") {
    return (
      <StatePanel
        state="error"
        title={t("injection.state.error")}
        description={summary.error ?? undefined}
        actionLabel={t("common.retry")}
        onAction={() => { void summary.refresh(); }}
      />
    );
  }
  if (!summary.data) {
    return (
      <StatePanel state="empty" title={t("injection.overview.noEvents")} />
    );
  }

  const draft = config.draft;
  const catalog = config.catalog;
  const data = summary.data;
  const preset = activePreset(draft);
  const delivery = effectiveDelivery(draft, catalog);
  const metrics = [
    {
      label: t("injection.overview.currentMode"),
      value: t(`injection.mode.${draft.routingMode}`),
    },
    {
      label: t("injection.overview.currentPreset"),
      value: t(`injection.preset.${preset}`),
    },
    {
      label: t("injection.overview.effectiveDelivery"),
      value: t(`injection.delivery.${delivery}`),
    },
    {
      label: t("injection.overview.decisions"),
      value: formatDashboardNumber(data.decision_count, locale),
    },
    {
      label: t("injection.overview.payloadP95"),
      value: formatDashboardNumber(data.payload_chars_p95, locale),
    },
    {
      label: t("injection.overview.fallbackRate"),
      value: formatDashboardPercent(data.provider_fallback_rate, locale, {
        maximumFractionDigits: 1,
      }),
    },
  ];

  const presetRows = catalog.presets.map((item) => ({
    name: item.name,
    label: t(`injection.preset.${item.name}`),
    count: data.preset_distribution[item.name] ?? 0,
  }));
  const costRows = data.cost_trend.map((point) => ({
    ...point,
    label: formatDashboardShortDate(point.bucket_ms, locale),
  }));
  const windowItems = WINDOW_OPTIONS.map((value) => ({
    label: t(`injection.window.${value}`),
    value,
  }));
  const recentOrdinary = data.recent_events.filter(
    (item) => !item.fallback_applied && item.outcome !== "error",
  ).slice(0, 5);
  const recentFallbacks = data.recent_events.filter(
    (item) => item.fallback_applied,
  ).slice(0, 5);
  const recentErrors = data.recent_events.filter(
    (item) => item.outcome === "error",
  ).slice(0, 5);
  const presetChartConfig: ChartConfig = {
    count: {
      label: t("injection.overview.decisions"),
      color: "hsl(var(--primary))",
    },
  };
  const costChartConfig: ChartConfig = {
    payload_chars_p95: {
      label: t("injection.overview.payloadP95"),
      color: "hsl(var(--primary))",
    },
    provider_fallback_rate: {
      label: t("injection.overview.fallbackRate"),
      color: "hsl(var(--destructive))",
    },
  };

  return (
    <section
      aria-label={t("injection.tabs.overview")}
      className="flex min-w-0 flex-col gap-4"
    >
      <div className="flex min-w-0 flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <label className="text-xs font-medium text-muted-foreground">
            {t("injection.overview.window")}
          </label>
          <Select
            items={windowItems}
            value={summary.windowValue}
            onValueChange={(value) => {
              if (value) summary.setWindowValue(value as InjectionSummaryWindow);
            }}
          >
            <SelectTrigger
              aria-label={t("injection.overview.window")}
              className="mt-1 w-36"
            >
              <SelectValue>
                {() => t(`injection.window.${summary.windowValue}`)}
              </SelectValue>
            </SelectTrigger>
            <SelectContent alignItemWithTrigger={false}>
              <SelectGroup>
                {WINDOW_OPTIONS.map((windowValue) => (
                  <SelectItem key={windowValue} value={windowValue}>
                    {t(`injection.window.${windowValue}`)}
                  </SelectItem>
                ))}
              </SelectGroup>
            </SelectContent>
          </Select>
        </div>
        <Button type="button" variant="outline" onClick={onEdit}>
          <SlidersHorizontal data-icon="inline-start" />
          {t("injection.actions.edit")}
        </Button>
      </div>

      <MetricGrid minItemWidth="10rem">
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

      {data.decision_count === 0 ? (
        <StatePanel state="empty" title={t("injection.overview.noEvents")} />
      ) : (
        <>
          <div className="grid min-w-0 gap-4 xl:grid-cols-2">
            <Card className="min-w-0">
              <CardHeader>
                <CardTitle>{t("injection.overview.presetDistribution")}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="sr-only">
                  {presetRows.map((row) => `${row.label}: ${row.count}`).join("; ")}
                </p>
                <ChartContainer
                  config={presetChartConfig}
                  className="h-56 w-full"
                  aria-label={t("injection.overview.presetChartSummary")}
                >
                  <BarChart data={presetRows} accessibilityLayer>
                    <CartesianGrid vertical={false} />
                    <XAxis dataKey="label" tickLine={false} axisLine={false} />
                    <YAxis allowDecimals={false} tickLine={false} axisLine={false} />
                    <ChartTooltip
                      content={(
                        <ChartTooltipContent
                          valueLabel={t("injection.overview.decisions")}
                        />
                      )}
                    />
                    <Bar
                      dataKey="count"
                      fill="var(--color-count)"
                      radius={[4, 4, 0, 0]}
                    />
                  </BarChart>
                </ChartContainer>
              </CardContent>
            </Card>

            <Card className="min-w-0">
              <CardHeader>
                <CardTitle>{t("injection.overview.costTrend")}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="sr-only">
                  {data.cost_trend.map((point) => [
                    formatDashboardDateTime(point.bucket_ms, locale),
                    `${t("injection.overview.payloadP95")} ${formatDashboardNumber(point.payload_chars_p95, locale)}`,
                    `${t("injection.overview.fallbackRate")} ${formatDashboardPercent(point.provider_fallback_rate, locale, { maximumFractionDigits: 1 })}`,
                  ].join(": ")).join("; ")}
                </p>
                <ChartContainer
                  config={costChartConfig}
                  className="h-56 w-full"
                  aria-label={t("injection.overview.costChartSummary")}
                >
                  <LineChart data={costRows} accessibilityLayer>
                    <CartesianGrid vertical={false} />
                    <XAxis dataKey="label" tickLine={false} axisLine={false} />
                    <YAxis
                      yAxisId="payload"
                      allowDecimals={false}
                      tickLine={false}
                      axisLine={false}
                    />
                    <YAxis
                      yAxisId="fallback"
                      orientation="right"
                      domain={[0, 1]}
                      tickFormatter={(value) => formatDashboardPercent(
                        value,
                        locale,
                        { maximumFractionDigits: 0 },
                      )}
                      tickLine={false}
                      axisLine={false}
                    />
                    <ChartTooltip />
                    <Legend />
                    <Line
                      yAxisId="payload"
                      type="monotone"
                      dataKey="payload_chars_p95"
                      name={t("injection.overview.payloadP95")}
                      stroke="var(--color-payload_chars_p95)"
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      yAxisId="fallback"
                      type="monotone"
                      dataKey="provider_fallback_rate"
                      name={t("injection.overview.fallbackRate")}
                      stroke="var(--color-provider_fallback_rate)"
                      strokeWidth={2}
                      dot={false}
                    />
                  </LineChart>
                </ChartContainer>
              </CardContent>
            </Card>
          </div>

          <div className="grid min-w-0 gap-4 xl:grid-cols-3">
            <EventList
              events={recentOrdinary}
              label={t("injection.overview.recent")}
              locale={locale}
              traceAvailable={catalog.recall_trace_available}
              t={t}
              onOpenTrace={onOpenTrace}
            />
            <EventList
              events={recentFallbacks}
              label={t("injection.overview.recentFallbacks")}
              locale={locale}
              traceAvailable={catalog.recall_trace_available}
              t={t}
              onOpenTrace={onOpenTrace}
            />
            <EventList
              events={recentErrors}
              label={t("injection.overview.recentErrors")}
              locale={locale}
              traceAvailable={catalog.recall_trace_available}
              t={t}
              onOpenTrace={onOpenTrace}
            />
          </div>
        </>
      )}
    </section>
  );
}
