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
import type { InjectionStrategyCatalog } from "@/types/injection";

interface InjectionDecisionSheetProps {
  open: boolean;
  catalog: InjectionStrategyCatalog | null;
  decisions: ReturnType<typeof useInjectionDecisions>;
  selectedDecisionId: string | null;
  onClose: () => void;
  onOpenTrace: (traceId: string) => void;
}

export function InjectionDecisionSheet(props: InjectionDecisionSheetProps) {
  const { t } = useI18n();
  const { decisions } = props;

  return (
    <Sheet
      open={props.open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) props.onClose();
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
        {decisions.detailStatus === "loading" ? (
          <StatePanel state="loading" title={t("injection.detail.loading")} />
        ) : decisions.detailStatus === "error" ? (
          <StatePanel
            state="error"
            title={t("injection.detail.error")}
            description={decisions.detailError ?? undefined}
            actionLabel={t("common.retry")}
            onAction={props.selectedDecisionId
              ? () => { void decisions.loadDetail(props.selectedDecisionId as string); }
              : undefined}
          />
        ) : decisions.detail ? (
          <section aria-label={t("injection.detail.title")} className="p-5">
            <p className="text-sm text-muted-foreground">
              {decisions.detail.decision_id}
            </p>
          </section>
        ) : (
          <StatePanel state="empty" title={t("injection.detail.empty")} />
        )}
      </SheetContent>
    </Sheet>
  );
}
