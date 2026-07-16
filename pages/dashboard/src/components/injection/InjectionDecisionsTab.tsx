import { StatePanel } from "@/components/ui/StatePanel";
import { useI18n } from "@/hooks/useI18n";
import type { useInjectionDecisions } from "@/hooks/useInjectionDecisions";
import type { InjectionStrategyCatalog } from "@/types/injection";

interface InjectionDecisionsTabProps {
  catalog: InjectionStrategyCatalog | null;
  decisions: ReturnType<typeof useInjectionDecisions>;
  onOpenTrace: (traceId: string) => void;
}

export function InjectionDecisionsTab(props: InjectionDecisionsTabProps) {
  const { t } = useI18n();
  const { catalog, decisions } = props;

  if (!catalog || decisions.status === "loading") {
    return (
      <StatePanel state="loading" title={t("injection.decisions.loading")} />
    );
  }
  if (decisions.status === "error") {
    return (
      <StatePanel
        state="error"
        title={t("injection.decisions.error")}
        description={decisions.error ?? undefined}
        actionLabel={t("common.retry")}
        onAction={() => { void decisions.refresh(); }}
      />
    );
  }
  if (!decisions.page || decisions.page.items.length === 0) {
    return (
      <StatePanel state="empty" title={t("injection.decisions.empty")} />
    );
  }

  return (
    <section
      aria-label={t("injection.decisions.title")}
      className="flex min-w-0 flex-col gap-2"
    >
      <h2 className="text-sm font-semibold text-foreground">
        {t("injection.decisions.title")}
      </h2>
      <p className="text-sm text-muted-foreground">
        {decisions.page.total}
      </p>
    </section>
  );
}
