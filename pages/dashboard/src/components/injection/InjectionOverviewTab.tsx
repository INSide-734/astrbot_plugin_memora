import { StatePanel } from "@/components/ui/StatePanel";
import { useI18n } from "@/hooks/useI18n";
import type { useInjectionStrategyConfig } from "@/hooks/useInjectionStrategyConfig";
import type { useInjectionStrategySummary } from "@/hooks/useInjectionStrategySummary";

interface InjectionOverviewTabProps {
  config: ReturnType<typeof useInjectionStrategyConfig>;
  summary: ReturnType<typeof useInjectionStrategySummary>;
  onEdit: () => void;
  onOpenTrace: (traceId: string) => void;
}

export function InjectionOverviewTab(props: InjectionOverviewTabProps) {
  const { t } = useI18n();
  const { config, summary } = props;

  if (config.catalogStatus === "loading" || summary.status === "loading") {
    return (
      <StatePanel
        state="loading"
        title={t("injection.overview.loading")}
      />
    );
  }
  if (!config.catalog || !config.draft || config.catalogStatus === "error") {
    return (
      <StatePanel
        state="error"
        title={t("injection.overview.unavailable")}
        description={config.catalogError ?? undefined}
        actionLabel={t("common.retry")}
        onAction={() => { void config.retryCatalog(); }}
      />
    );
  }
  if (summary.status === "error") {
    return (
      <StatePanel
        state="error"
        title={t("injection.overview.error")}
        description={summary.error ?? undefined}
        actionLabel={t("common.retry")}
        onAction={() => { void summary.refresh(); }}
      />
    );
  }
  if (!summary.data) {
    return (
      <StatePanel
        state="empty"
        title={t("injection.overview.empty")}
      />
    );
  }

  return (
    <section
      aria-label={t("injection.overview.title")}
      className="flex min-w-0 flex-col gap-2"
    >
      <h2 className="text-sm font-semibold text-foreground">
        {t("injection.overview.title")}
      </h2>
      <p className="text-sm text-muted-foreground">
        {config.draft.routingMode}
      </p>
    </section>
  );
}
