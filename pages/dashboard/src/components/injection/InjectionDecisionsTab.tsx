import { useEffect, useMemo, useState } from "react";
import type { LucideIcon } from "lucide-react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleX,
  MinusCircle,
  RotateCcw,
} from "lucide-react";

import { PageToolbar } from "@/components/layout/PageLayout";
import { DataTable } from "@/components/data-table/DataTable";
import { actionsColumn } from "@/components/data-table/data-table-columns";
import type { DataTableColumn } from "@/components/data-table/table-types";
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
  translateEnum,
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
  "mode",
  "preset",
  "provider",
  "reason",
  "fallback",
  "outcome",
  "payloadChars",
  "totalMs",
  "actions",
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
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
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
    <PageToolbar
      aria-label={t("injection.tabs.decisions")}
      className="h-auto items-start"
    >
      <FieldGroup className="grid min-w-0 flex-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
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
            <FieldError>{t("injection.validation.timeRange")}</FieldError>
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
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="self-end"
        onClick={clear}
      >
        <RotateCcw data-icon="inline-start" />
        {t("injection.actions.clearFilters")}
      </Button>
    </PageToolbar>
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
      aria-label={t("injection.state.loading")}
      className="max-w-full overflow-x-auto rounded-lg border"
    >
      <Table className="min-w-[64rem]">
        <TableHeader>
          <TableRow>
            {decisionColumns.map((column) => (
              <TableHead key={column}>{t(`injection.column.${column}`)}</TableHead>
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
  columns,
  onOpenDecision,
  t,
}: {
  decisions: ReturnType<typeof useInjectionDecisions>;
  columns: DataTableColumn<InjectionDecisionListItem>[];
  onOpenDecision: (decisionId: string) => void;
  t: Translate;
}) {
  if (!decisions.page) return null;

  return (
    <div
      className="max-w-full overflow-x-auto rounded-lg border"
      data-testid="decision-table-scroll"
    >
      <DataTable
        tableId="injection-decisions"
        data={decisions.page.items}
        columns={columns}
        getRowId={(item) => item.decision_id}
        sort={decisions.sort}
        onSortChange={decisions.setSort}
        currentRowId={decisions.detail?.decision_id ?? null}
        onRowActivate={(item) => onOpenDecision(item.decision_id)}
        loading={decisions.status === "loading"}
        emptyLabel={t("injection.state.empty")}
        pagination={<DecisionPagination decisions={decisions} t={t} />}
      />
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
            className="w-full"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {items.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
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
  const decisionTableColumns = useMemo<DataTableColumn<InjectionDecisionListItem>[]>(
    () => [
      {
        id: "created_at_ms",
        accessorKey: "created_at_ms",
        header: t("injection.column.time"),
        meta: {
          label: t("injection.column.time"),
          serverSortKey: "created_at_ms",
        },
        cell: ({ row }) => formatDashboardDateTime(row.original.created_at_ms, locale),
      },
      {
        id: "routing_mode",
        accessorKey: "routing_mode",
        header: t("injection.column.mode"),
        meta: {
          label: t("injection.column.mode"),
          serverSortKey: "routing_mode",
        },
        cell: ({ row }) => t(`injection.mode.${row.original.routing_mode}`),
      },
      {
        id: "resolved_preset",
        accessorKey: "resolved_preset",
        header: t("injection.column.preset"),
        meta: {
          label: t("injection.column.preset"),
          serverSortKey: "resolved_preset",
        },
        cell: ({ row }) => t(`injection.preset.${row.original.resolved_preset}`),
      },
      {
        id: "provider_type",
        accessorKey: "provider_type",
        header: t("injection.column.provider"),
        meta: {
          label: t("injection.column.provider"),
          serverSortKey: "provider_type",
        },
      },
      {
        id: "primary_reason",
        accessorKey: "primary_reason",
        header: t("injection.column.reason"),
        enableSorting: false,
        meta: { label: t("injection.column.reason") },
        cell: ({ row }) => translateEnum(
          t,
          "injection.reason",
          row.original.primary_reason,
          row.original.primary_reason,
        ),
      },
      {
        id: "fallback_applied",
        accessorKey: "fallback_applied",
        header: t("injection.column.fallback"),
        enableSorting: false,
        meta: { label: t("injection.column.fallback") },
        cell: ({ row }) => (
          <StatusText
            icon={row.original.fallback_applied ? AlertTriangle : MinusCircle}
            label={row.original.fallback_applied ? t("common.yes") : t("common.no")}
          />
        ),
      },
      {
        id: "outcome",
        accessorKey: "outcome",
        header: t("injection.column.outcome"),
        meta: {
          label: t("injection.column.outcome"),
          serverSortKey: "outcome",
        },
        cell: ({ row }) => (
          <StatusText
            icon={outcomeIcon(row.original.outcome)}
            label={t(`injection.outcome.${row.original.outcome}`)}
          />
        ),
      },
      {
        id: "actual_payload_chars",
        accessorKey: "actual_payload_chars",
        header: t("injection.column.payloadChars"),
        meta: {
          label: t("injection.column.payloadChars"),
          serverSortKey: "actual_payload_chars",
          cellClassName: "text-right tabular-nums",
        },
        cell: ({ row }) => formatDashboardNumber(
          row.original.actual_payload_chars,
          locale,
        ),
      },
      {
        id: "decision_ms",
        accessorKey: "decision_ms",
        header: t("injection.column.totalMs"),
        meta: {
          label: t("injection.column.totalMs"),
          serverSortKey: "decision_ms",
          cellClassName: "text-right tabular-nums",
        },
        cell: ({ row }) => formatDashboardNumber(totalDecisionMs(row.original), locale, {
          maximumFractionDigits: 2,
        }),
      },
      {
        ...actionsColumn({
          label: t("injection.column.actions"),
          rowLabel: () => t("injection.decisions.openDetail"),
          actions: (item) => [{
            id: "open-detail",
            label: t("injection.decisions.openDetail"),
            onSelect: () => onOpenDecision(item.decision_id),
          }],
        }),
        header: t("injection.column.actions"),
      },
    ],
    [locale, onOpenDecision, t],
  );

  return (
    <section
      aria-label={t("injection.tabs.decisions")}
      className="flex min-w-0 flex-col"
    >
      <DecisionFilters decisions={decisions} t={t} />
      <div className="flex min-w-0 flex-col gap-5 px-4 py-4 sm:px-5 sm:py-5 lg:px-6 lg:py-6">
        {!catalog || decisions.status === "loading" ? (
          <DecisionTableLoading t={t} />
        ) : decisions.status === "error" ? (
          <StatePanel
            state="error"
            title={t("injection.state.error")}
            description={decisions.error ?? undefined}
            actionLabel={t("common.retry")}
            onAction={() => { void decisions.refresh(); }}
          />
        ) : !decisions.page || decisions.page.items.length === 0 ? (
          <StatePanel state="empty" title={t("injection.state.empty")} />
        ) : (
          <>
            <DecisionTable
              decisions={decisions}
              columns={decisionTableColumns}
              onOpenDecision={onOpenDecision}
              t={t}
            />
          </>
        )}
      </div>
    </section>
  );
}
