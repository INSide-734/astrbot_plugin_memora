import { Button } from "@/components/ui/Button";
import { StatePanel } from "@/components/ui/StatePanel";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useI18n } from "@/hooks/useI18n";
import type { useInjectionDecisions } from "@/hooks/useInjectionDecisions";
import {
  dashboardLocale,
  formatDashboardDateTime,
  formatDashboardNumber,
  translateEnum,
} from "@/lib/i18n";
import type { Translate } from "@/lib/i18n";
import type {
  InjectionDecisionDetail,
  InjectionStrategyCatalog,
} from "@/types/injection";

interface InjectionDecisionSheetProps {
  open: boolean;
  catalog: InjectionStrategyCatalog | null;
  decisions: ReturnType<typeof useInjectionDecisions>;
  selectedDecisionId: string | null;
  onClose: () => void;
  onOpenTrace: (traceId: string) => void;
}

type DetailField = keyof InjectionDecisionDetail;

interface DetailSectionDefinition {
  title: string;
  fields: readonly DetailField[];
}

const detailSections: DetailSectionDefinition[] = [
  {
    title: "identity",
    fields: ["decision_id", "created_at_ms", "trace_id"],
  },
  {
    title: "routing",
    fields: [
      "routing_mode",
      "configured_preset",
      "recommended_preset",
      "resolved_preset",
    ],
  },
  {
    title: "delivery",
    fields: [
      "preferred_delivery",
      "resolved_delivery",
      "fallback_applied",
      "outcome",
      "error_code",
      "provider_type",
      "provider_model",
    ],
  },
  {
    title: "counts",
    fields: [
      "candidate_count",
      "selected_count",
      "dropped_count",
      "truncated_count",
    ],
  },
  {
    title: "budgets",
    fields: [
      "configured_budget_chars",
      "effective_budget_chars",
      "actual_payload_chars",
      "context_headroom_chars",
    ],
  },
  {
    title: "timings",
    fields: ["decision_ms", "format_ms", "inject_ms"],
  },
  {
    title: "reasons",
    fields: ["primary_reason", "reason_codes"],
  },
];

function formatDetailValue(
  field: DetailField,
  value: InjectionDecisionDetail[DetailField],
  locale: string,
  t: Translate,
): string {
  if (value === null || value === undefined || value === "") return "--";
  if (field === "created_at_ms") return formatDashboardDateTime(value, locale);
  if (field === "fallback_applied") {
    return value ? t("common.yes") : t("common.no");
  }
  if (field === "routing_mode") return t(`injection.mode.${String(value)}`);
  if (
    field === "configured_preset"
    || field === "recommended_preset"
    || field === "resolved_preset"
  ) return t(`injection.preset.${String(value)}`);
  if (field === "preferred_delivery" || field === "resolved_delivery") {
    return t(`injection.delivery.${String(value)}`);
  }
  if (field === "outcome") return t(`injection.outcome.${String(value)}`);
  if (field === "primary_reason") {
    return translateEnum(t, "injection.reason", value, String(value));
  }
  if (field === "reason_codes" && Array.isArray(value)) {
    return value.map((reason) => (
      translateEnum(t, "injection.reason", reason, reason)
    )).join(", ");
  }
  if (field === "decision_ms" || field === "format_ms" || field === "inject_ms") {
    return formatDashboardNumber(value, locale, { maximumFractionDigits: 2 });
  }
  if (
    field.endsWith("_count")
    || field.endsWith("_chars")
  ) return formatDashboardNumber(value, locale);
  return String(value);
}

function DetailContent({
  catalog,
  detail,
  locale,
  onOpenTrace,
  t,
}: {
  catalog: InjectionStrategyCatalog | null;
  detail: InjectionDecisionDetail;
  locale: string;
  onOpenTrace: (traceId: string) => void;
  t: Translate;
}) {
  const canOpenTrace = Boolean(
    detail.trace_id && catalog?.recall_trace_available,
  );

  return (
    <div className="flex flex-col gap-6 p-5">
      {detailSections.map((section) => (
        <section key={section.title} className="space-y-3">
          <h3 className="text-sm font-semibold text-foreground">
            {t(`injection.detail.${section.title}`)}
          </h3>
          <dl className="divide-y rounded-lg border">
            {section.fields.map((field) => (
              <div
                key={field}
                className="grid min-w-0 gap-1 px-3 py-2.5 sm:grid-cols-[minmax(0,12rem)_minmax(0,1fr)] sm:gap-3"
              >
                <dt className="text-sm text-muted-foreground">
                  {t(`injection.detail.field.${field}`)}
                </dt>
                <dd className="min-w-0 break-words text-sm text-foreground">
                  {formatDetailValue(field, detail[field], locale, t)}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
      <div className="flex justify-end border-t pt-4">
        <Button
          type="button"
          variant="outline"
          disabled={!canOpenTrace}
          onClick={() => {
            if (detail.trace_id && canOpenTrace) onOpenTrace(detail.trace_id);
          }}
        >
          {t("injection.actions.openTrace")}
        </Button>
      </div>
    </div>
  );
}

export function InjectionDecisionSheet({
  catalog,
  decisions,
  onClose,
  onOpenTrace,
  open,
  selectedDecisionId,
}: InjectionDecisionSheetProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());

  return (
    <Sheet
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onClose();
      }}
    >
      <SheetContent
        side="right"
        className="w-full max-w-full overflow-y-auto sm:max-w-xl"
      >
        <SheetHeader>
          <SheetTitle>{t("injection.detail.title")}</SheetTitle>
          <SheetDescription>{t("injection.detail.description")}</SheetDescription>
        </SheetHeader>
        {decisions.detailStatus === "loading" || decisions.detailStatus === "idle" ? (
          <StatePanel state="loading" title={t("injection.state.loading")} />
        ) : decisions.detailStatus === "error" ? (
          <StatePanel
            state="error"
            title={t("injection.state.error")}
            description={decisions.detailError ?? undefined}
            actionLabel={t("injection.detail.retry")}
            onAction={selectedDecisionId
              ? () => { void decisions.loadDetail(selectedDecisionId); }
              : undefined}
          />
        ) : decisions.detail ? (
          <DetailContent
            catalog={catalog}
            detail={decisions.detail}
            locale={locale}
            onOpenTrace={onOpenTrace}
            t={t}
          />
        ) : (
          <StatePanel state="empty" title={t("injection.state.empty")} />
        )}
      </SheetContent>
    </Sheet>
  );
}
