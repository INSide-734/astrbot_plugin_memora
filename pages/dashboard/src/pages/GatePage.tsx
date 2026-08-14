import { ShieldCheck } from "lucide-react";

import {
  PageContent,
  PageFrame,
  PageHeader,
} from "@/components/layout/PageLayout";
import { useI18n } from "@/hooks/useI18n";

export interface GatePageProps {
  showToast?: (
    message: string,
    type?: "success" | "error" | "info",
  ) => void;
  onDirtyChange?: (dirty: boolean) => void;
}

export function GatePage(_props: GatePageProps) {
  const { t } = useI18n();

  return (
    <PageFrame variant="dense" aria-label={t("nav.gate")}>
      <PageHeader
        title={t("gate.title")}
        description={t("gate.subtitle")}
        icon={<ShieldCheck aria-hidden="true" />}
      />
      <PageContent>
        <p className="py-10 text-center text-sm text-muted-foreground">
          {t("gate.wip")}
        </p>
      </PageContent>
    </PageFrame>
  );
}
