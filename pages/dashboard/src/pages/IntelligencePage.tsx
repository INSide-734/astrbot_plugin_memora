import { Activity, AlertTriangle, BrainCircuit, ClipboardCheck, GitBranch, Stethoscope } from "lucide-react";
import { PageContent, PageFrame, PageHeader } from "@/components/layout/PageLayout";
import { DiagnosticCenter } from "@/components/intelligence/DiagnosticCenter";
import { EvaluationWorkbench } from "@/components/intelligence/EvaluationWorkbench";
import { RecallTracePanel } from "@/components/intelligence/RecallTracePanel";
import { ReviewQueue } from "@/components/intelligence/ReviewQueue";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useI18n } from "@/hooks/useI18n";
import type { IntelligenceTabId } from "@/types/intelligence";

interface IntelligencePageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

interface TabDefinition {
  id: IntelligenceTabId;
  labelKey: string;
  icon: React.ReactNode;
}

const tabs: TabDefinition[] = [
  { id: "evaluation", labelKey: "intelligence.tabs.evaluation", icon: <ClipboardCheck size={14} /> },
  { id: "recallTrace", labelKey: "intelligence.tabs.recallTrace", icon: <GitBranch size={14} /> },
  { id: "diagnostics", labelKey: "intelligence.tabs.diagnostics", icon: <Stethoscope size={14} /> },
  { id: "reviewQueue", labelKey: "intelligence.tabs.reviewQueue", icon: <AlertTriangle size={14} /> },
];

const panelByTab: Record<IntelligenceTabId, (showToast: IntelligencePageProps["showToast"]) => JSX.Element> = {
  evaluation: (showToast) => <EvaluationWorkbench showToast={showToast} />,
  recallTrace: (showToast) => <RecallTracePanel showToast={showToast} />,
  diagnostics: (showToast) => <DiagnosticCenter showToast={showToast} />,
  reviewQueue: (showToast) => <ReviewQueue showToast={showToast} />,
};

export function IntelligencePage({ showToast }: IntelligencePageProps) {
  const { t } = useI18n();
  return (
    <PageFrame variant="standard" aria-label={t("intelligence.title")}>
      <PageHeader
        title={t("intelligence.title")}
        description={t("intelligence.subtitle")}
        icon={<BrainCircuit />}
        status={<div className="hidden items-center gap-2 text-xs text-muted-foreground sm:flex">
          <Activity size={14} />
          <span>{t("intelligence.status.shell")}</span>
        </div>}
      />
      <Tabs defaultValue="evaluation" className="min-h-0 flex-1 gap-0">
        <div className="shrink-0 overflow-x-auto border-b px-4 sm:px-5 lg:px-6">
          <TabsList variant="line" aria-label={t("intelligence.tabs.label")} className="h-11 min-w-max">
          {tabs.map((tab) => {
            return (
              <TabsTrigger
                key={tab.id}
                value={tab.id}
                id={`intelligence-tab-${tab.id}`}
                className="px-3 text-xs"
              >
                <span data-icon="inline-start">{tab.icon}</span>
                {t(tab.labelKey)}
              </TabsTrigger>
            );
          })}
          </TabsList>
        </div>
        {tabs.map((tab) => (
          <TabsContent
            key={tab.id}
            value={tab.id}
            id={`intelligence-panel-${tab.id}`}
            aria-labelledby={`intelligence-tab-${tab.id}`}
            className="min-h-0 overflow-auto"
          >
            <PageContent>
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-xs uppercase text-muted-foreground">{t("intelligence.activeTab")}</p>
                  <h2 className="mt-1 text-lg font-semibold text-foreground">{t(tab.labelKey)}</h2>
                </div>
                <span className="rounded-full border px-2.5 py-1 text-xs font-medium text-muted-foreground">
                  {t("intelligence.status.local")}
                </span>
              </div>
              {panelByTab[tab.id](showToast)}
            </PageContent>
          </TabsContent>
        ))}
      </Tabs>
    </PageFrame>
  );
}
