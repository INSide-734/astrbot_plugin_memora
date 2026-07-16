import { StatePanel } from "@/components/ui/StatePanel";
import { useI18n } from "@/hooks/useI18n";
import type { useInjectionStrategyConfig } from "@/hooks/useInjectionStrategyConfig";

interface InjectionConfigTabProps {
  config: ReturnType<typeof useInjectionStrategyConfig>;
  showToast: (
    message: string,
    type?: "success" | "error" | "info",
  ) => void;
}

export function InjectionConfigTab(props: InjectionConfigTabProps) {
  const { t } = useI18n();
  const { config } = props;

  if (config.catalogStatus === "loading" || config.status === "loading") {
    return (
      <StatePanel state="loading" title={t("injection.config.loading")} />
    );
  }
  if (!config.catalog || !config.draft) {
    return (
      <StatePanel
        state="error"
        title={t("injection.config.unavailable")}
        description={config.catalogError ?? undefined}
        actionLabel={t("common.retry")}
        onAction={() => { void config.refresh(); }}
      />
    );
  }

  return (
    <section
      aria-label={t("injection.config.title")}
      className="flex min-w-0 flex-col gap-2"
    >
      <h2 className="text-sm font-semibold text-foreground">
        {t("injection.config.title")}
      </h2>
      <p className="text-sm text-muted-foreground">
        {config.draft.routingMode}
      </p>
    </section>
  );
}
