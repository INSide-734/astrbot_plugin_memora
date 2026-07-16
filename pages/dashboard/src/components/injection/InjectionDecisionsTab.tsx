import { useEffect, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleX,
  MinusCircle,
  RotateCcw,
} from "lucide-react";

import { Button } from "@/components/ui/Button";
import {
  Field,
  FieldError,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { StatePanel } from "@/components/ui/StatePanel";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useI18n } from "@/hooks/useI18n";
import type { useInjectionDecisions } from "@/hooks/useInjectionDecisions";
import {
  dashboardLocale,
  formatDashboardDateTime,
  formatDashboardNumber,
} from "@/lib/i18n";
import type { Translate } from "@/lib/i18n";
import {
  DEFAULT_INJECTION_FILTERS,
  type InjectionDecisionFilters,
  type InjectionDecisionListItem,
  type InjectionOutcome,
  type InjectionPresetName,
  type InjectionRoutingMode,
  type InjectionStrategyCatalog,
} from "@/types/injection";

interface InjectionDecisionsTabProps {
  catalog: InjectionStrategyCatalog | null;
  decisions: ReturnType<typeof useInjectionDecisions>;
  onOpenDecision: (decisionId: string) => void;
}

type SelectFilterField =
  | "routingMode"
  | "resolvedPreset"
  | "fallbackApplied"
  | "outcome";

interface FilterOption {
  label: string;
  value: string;
}

const routingModes: InjectionRoutingMode[] = ["manual", "auto", "hybrid"];
const presets: InjectionPresetName[] = [
  "tool_first",
  "low_cost",
  "balanced",
  "quality",
];
const outcomes: InjectionOutcome[] = [
  "injected",
  "skipped",
  "empty",
  "fallback",
  "error",
];
const pageSizes = [25, 50, 100] as const;

const decisionColumns = [
  "time",
  "routingMode",
  "resolvedPreset",
  "provider",
  "primaryReason",
  "fallback",
  "outcome",
  "payloadChars",
  "totalMs",
] as const;

function totalDecisionMs(item: InjectionDecisionListItem): number {
  return item.decision_ms + item.format_ms + item.inject_ms;
}

function toLocalDateTimeValue(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "";
  const date = new Date(value);
  const local = new Date(value - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function localDateTimeMs(value: string): number | null | undefined {
  if (!value) return null;
  const timestamp = new Date(value).getTime();
  return Number.isFinite(timestamp) ? timestamp : undefined;
}

function FilterSelect({
  decisions,
  field,
  options,
  t,
}: {
  decisions: ReturnType<typeof useInjectionDecisions>;
  field: SelectFilterField;
  options: FilterOption[];
  t: Translate;
}) {
  const selected = decisions.filters[field] || "all";
  const items = [
    { label: t("injection.filter.all"), value: "all" },
    ...options,
  ];
  const label = t(`injection.filter.${field}`);

  return (
    <Field>
      <FieldLabel htmlFor={`injection-filter-${field}`}>{label}</FieldLabel>
      <Select
        items={items}
        value={selected}
        onValueChange={(value) => {
          if (value) {
            decisions.setFilter(field, (value === "all" ? "" : value) as never);
          }
        }}
      >
        <SelectTrigger
          id={`injection-filter-${field}`}
          aria-label={label}
          className="w-full"
        >
          <SelectValue>
            {() => items.find((item) => item.value === selected)?.label ?? selected}
          </SelectValue>
        </SelectTrigger>
        <SelectContent alignItemWithTrigger={false}>
          <SelectGroup>
            {items.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    </Field>
  );
}

function DecisionFilters({
  decisions,
  t,
}: {
  decisions: ReturnType<typeof useInjectionDecisions>;
  t: Translate;
}) {
  const [fromValue, setFromValue] = useState(() => (
    toLocalDateTimeValue(decisions.filters.fromMs)
  ));
  const [toValue, setToValue] = useState(() => (
    toLocalDateTimeValue(decisions.filters.toMs)
  ));
  const fromMs = localDateTimeMs(fromValue);
  const toMs = localDateTimeMs(toValue);
  const invalidRange = typeof fromMs === "number"
    && typeof toMs === "number"
    && fromMs > toMs;

  useEffect(() => {
    setFromValue(toLocalDateTimeValue(decisions.filters.fromMs));
  }, [decisions.filters.fromMs]);
  useEffect(() => {
    setToValue(toLocalDateTimeValue(decisions.filters.toMs));
  }, [decisions.filters.toMs]);

  const changeDate = (field: "fromMs" | "toMs", value: string) => {
    const nextFrom = field === "fromMs" ? value : fromValue;
    const nextTo = field === "toMs" ? value : toValue;
    if (field === "fromMs") setFromValue(value);
    else setToValue(value);

    const nextFromMs = localDateTimeMs(nextFrom);
    const nextToMs = localDateTimeMs(nextTo);
    if (nextFromMs === undefined || nextToMs === undefined) return;
    if (
      typeof nextFromMs === "number"
      && typeof nextToMs === "number"
      && nextFromMs > nextToMs
    ) return;
    decisions.setFilter(field, field === "fromMs" ? nextFromMs : nextToMs);
  };

  const clear = () => {
    setFromValue("");
    setToValue("");
    decisions.setFilters(DEFAULT_INJECTION_FILTERS);
  };

  return (
    <div className="min-w-0 space-y-4 rounded-lg border p-4">
      <FieldGroup className="grid min-w-0 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Field data-invalid={invalidRange}>
          <FieldLabel htmlFor="injection-filter-from">
            {t("injection.filter.from")}
          </FieldLabel>
          <Input
            id="injection-filter-from"
            type="datetime-local"
            value={fromValue}
            aria-invalid={invalidRange}
            onChange={(event) => changeDate("fromMs", event.currentTarget.value)}
          />
        </Field>
        <Field data-invalid={invalidRange}>
          <FieldLabel htmlFor="injection-filter-to">
            {t("injection.filter.to")}
          </FieldLabel>
          <Input
            id="injection-filter-to"
            type="datetime-local"
            value={toValue}
            aria-invalid={invalidRange}
            onChange={(event) => changeDate("toMs", event.currentTarget.value)}
          />
          {invalidRange ? (
            <FieldError>{t("injection.validation.dateRange")}</FieldError>
          ) : null}
        </Field>
        <FilterSelect
          decisions={decisions}
          field="routingMode"
          options={routingModes.map((value) => ({
            label: t(`injection.mode.${value}`),
            value,
          }))}
          t={t}
        />
        <FilterSelect
          decisions={decisions}
          field="resolvedPreset"
          options={presets.map((value) => ({
            label: t(`injection.preset.${value}`),
            value,
          }))}
          t={t}
        />
        <Field>
          <FieldLabel htmlFor="injection-filter-providerType">
            {t("injection.filter.providerType")}
          </FieldLabel>
          <Input
            id="injection-filter-providerType"
            value={decisions.filters.providerType}
            onChange={(event) => decisions.setFilter(
              "providerType",
              event.currentTarget.value,
            )}
          />
        </Field>
        <Field>
          <FieldLabel htmlFor="injection-filter-primaryReason">
            {t("injection.filter.primaryReason")}
          </FieldLabel>
          <Input
            id="injection-filter-primaryReason"
            value={decisions.filters.primaryReason}
            onChange={(event) => decisions.setFilter(
              "primaryReason",
              event.currentTarget.value,
            )}
          />
        </Field>
        <FilterSelect
          decisions={decisions}
          field="fallbackApplied"
          options={[
            { label: t("common.yes"), value: "true" },
            { label: t("common.no"), value: "false" },
          ]}
          t={t}
        />
        <FilterSelect
          decisions={decisions}
          field="outcome"
          options={outcomes.map((value) => ({
            label: t(`injection.outcome.${value}`),
            value,
          }))}
          t={t}
        />
      </FieldGroup>
      <div className="flex justify-end">
        <Button type="button" variant="outline" size="sm" onClick={clear}>
          <RotateCcw data-icon="inline-start" />
          {t("injection.filters.clear")}
        </Button>
      </div>
    </div>
  );
}

function StatusText({
  icon: Icon,
  label,
}: {
  icon: LucideIcon;
  label: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <Icon aria-hidden="true" className="size-4 text-muted-foreground" />
      {label}
    </span>
  );
}

function outcomeIcon(outcome: InjectionOutcome): LucideIcon {
  if (outcome === "injected") return CheckCircle2;
  if (outcome === "fallback") return AlertTriangle;
  if (outcome === "error") return CircleX;
  return MinusCircle;
}

function DecisionTableLoading({ t }: { t: Translate }) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label={t("injection.decisions.loading")}
      className="max-w-full overflow-x-auto rounded-lg border"
    >
      <Table className="min-w-[64rem]">
        <TableHeader>
          <TableRow>
            {decisionColumns.map((column) => (
              <TableHead key={column}>{t(`injection.decisions.${column}`)}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {Array.from({ length: 4 }, (_, row) => (
            <TableRow key={row}>
              {decisionColumns.map((column) => (
                <TableCell key={column}><Skeleton className="h-4 w-full" /></TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function DecisionTable({
  decisions,
  locale,
  onOpenDecision,
  t,
}: {
  decisions: ReturnType<typeof useInjectionDecisions>;
  locale: string;
  onOpenDecision: (decisionId: string) => void;
  t: Translate;
}) {
  if (!decisions.page) return null;

  return (
    <div
      className="max-w-full overflow-x-auto rounded-lg border"
      data-testid="decision-table-scroll"
    >
      <Table className="min-w-[64rem]" aria-label={t("injection.decisions.table")}>
        <TableHeader>
          <TableRow>
            {decisionColumns.map((column) => (
              <TableHead key={column}>{t(`injection.decisions.${column}`)}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {decisions.page.items.map((item) => (
            <TableRow key={item.decision_id}>
              <TableCell>
                <div className="flex flex-col items-start gap-2">
                  <span>{formatDashboardDateTime(item.created_at_ms, locale)}</span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => onOpenDecision(item.decision_id)}
                  >
                    {t("injection.decisions.openDetail")}
                  </Button>
                </div>
              </TableCell>
              <TableCell>{t(`injection.mode.${item.routing_mode}`)}</TableCell>
              <TableCell>{t(`injection.preset.${item.resolved_preset}`)}</TableCell>
              <TableCell>{item.provider_type}</TableCell>
              <TableCell>{item.primary_reason}</TableCell>
              <TableCell>
                <StatusText
                  icon={item.fallback_applied ? AlertTriangle : MinusCircle}
                  label={item.fallback_applied ? t("common.yes") : t("common.no")}
                />
              </TableCell>
              <TableCell>
                <StatusText
                  icon={outcomeIcon(item.outcome)}
                  label={t(`injection.outcome.${item.outcome}`)}
                />
              </TableCell>
              <TableCell>{formatDashboardNumber(item.actual_payload_chars, locale)}</TableCell>
              <TableCell>
                {formatDashboardNumber(totalDecisionMs(item), locale, {
                  maximumFractionDigits: 2,
                })}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function DecisionPagination({
  decisions,
  t,
}: {
  decisions: ReturnType<typeof useInjectionDecisions>;
  t: Translate;
}) {
  const total = decisions.page?.total ?? 0;
  const currentPage = Math.floor(decisions.offset / decisions.limit) + 1;
  const pageCount = Math.max(1, Math.ceil(total / decisions.limit));
  const canPrevious = decisions.offset > 0;
  const canNext = decisions.offset + decisions.limit < total;
  const items = pageSizes.map((size) => ({ label: String(size), value: String(size) }));

  return (
    <nav
      aria-label={t("injection.pagination.label")}
      className="flex min-w-0 flex-wrap items-center justify-between gap-3"
    >
      <span className="text-sm text-muted-foreground">
        {t(
          "injection.pagination.summary",
          String(currentPage),
          String(pageCount),
          String(total),
        )}
      </span>
      <div className="flex flex-wrap items-center gap-2">
        <Select
          items={items}
          value={String(decisions.limit)}
          onValueChange={(value) => {
            if (value) decisions.setLimit(Number(value));
          }}
        >
          <SelectTrigger
            aria-label={t("injection.pagination.pageSize")}
            className="w-20"
          >
            <SelectValue>{() => String(decisions.limit)}</SelectValue>
          </SelectTrigger>
          <SelectContent alignItemWithTrigger={false}>
            <SelectGroup>
              {pageSizes.map((size) => (
                <SelectItem key={size} value={String(size)}>{size}</SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label={t("injection.pagination.previous")}
          disabled={!canPrevious}
          onClick={() => decisions.setOffset(Math.max(
            0,
            decisions.offset - decisions.limit,
          ))}
        >
          {t("injection.pagination.previous")}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label={t("injection.pagination.next")}
          disabled={!canNext}
          onClick={() => decisions.setOffset(decisions.offset + decisions.limit)}
        >
          {t("injection.pagination.next")}
        </Button>
      </div>
    </nav>
  );
}

export function InjectionDecisionsTab({
  catalog,
  decisions,
  onOpenDecision,
}: InjectionDecisionsTabProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());

  return (
    <section
      aria-label={t("injection.decisions.title")}
      className="flex min-w-0 flex-col gap-5"
    >
      <DecisionFilters decisions={decisions} t={t} />
      {!catalog || decisions.status === "loading" ? (
        <DecisionTableLoading t={t} />
      ) : decisions.status === "error" ? (
        <StatePanel
          state="error"
          title={t("injection.decisions.error")}
          description={decisions.error ?? undefined}
          actionLabel={t("common.retry")}
          onAction={() => { void decisions.refresh(); }}
        />
      ) : !decisions.page || decisions.page.items.length === 0 ? (
        <StatePanel state="empty" title={t("injection.decisions.empty")} />
      ) : (
        <>
          <DecisionTable
            decisions={decisions}
            locale={locale}
            onOpenDecision={onOpenDecision}
            t={t}
          />
          <DecisionPagination decisions={decisions} t={t} />
        </>
      )}
    </section>
  );
}
