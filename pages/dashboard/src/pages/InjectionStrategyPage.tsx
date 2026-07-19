import { useCallback, useEffect, useRef, useState } from "react";
import { SlidersHorizontal } from "lucide-react";

import { ConfigUnsavedDialog } from "@/components/config/ConfigUnsavedDialog";
import { InjectionConfigTab } from "@/components/injection/InjectionConfigTab";
import { InjectionDecisionSheet } from "@/components/injection/InjectionDecisionSheet";
import { InjectionDecisionsTab } from "@/components/injection/InjectionDecisionsTab";
import { InjectionOverviewTab } from "@/components/injection/InjectionOverviewTab";
import { PageContent, PageFrame, PageHeader } from "@/components/layout/PageLayout";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useI18n } from "@/hooks/useI18n";
import { useInjectionDecisions } from "@/hooks/useInjectionDecisions";
import { useInjectionStrategyConfig } from "@/hooks/useInjectionStrategyConfig";
import { useInjectionStrategySummary } from "@/hooks/useInjectionStrategySummary";
import type {
  InjectionNavigationTarget,
  InjectionWorkbenchTab,
  PageId,
  PageNavigationIntent,
} from "@/types";

interface InjectionStrategyPageProps {
  navigationTarget?: InjectionNavigationTarget | null;
  onDirtyChange?: (dirty: boolean) => void;
  onNavigate: (page: PageId, intent?: PageNavigationIntent) => void;
  showToast: (
    message: string,
    type?: "success" | "error" | "info",
  ) => void;
}

export function InjectionStrategyPage({
  navigationTarget,
  onDirtyChange,
  onNavigate,
  showToast,
}: InjectionStrategyPageProps) {
  const { t } = useI18n();
  const config = useInjectionStrategyConfig();
  const summary = useInjectionStrategySummary();
  const decisions = useInjectionDecisions({ initialLimit: 25 });
  const [activeTab, setActiveTab] = useState<InjectionWorkbenchTab>("overview");
  const [pendingTab, setPendingTab] = useState<InjectionWorkbenchTab | null>(null);
  const [selectedDecisionId, setSelectedDecisionId] = useState<string | null>(null);
  const detailReturnFocusRef = useRef<HTMLElement | null>(null);
  const detailReturnFocusIdRef = useRef<string | null>(null);
  const processedTargetRef = useRef<number | null>(null);

  const requestTab = useCallback((next: string) => {
    const tab = next as InjectionWorkbenchTab;
    if (tab === activeTab) return;
    if (activeTab === "config" && config.dirty) {
      setPendingTab(tab);
      return;
    }
    setActiveTab(tab);
  }, [activeTab, config.dirty]);

  const discardAndSwitch = useCallback(() => {
    const next = pendingTab;
    setPendingTab(null);
    config.discard();
    if (next) setActiveTab(next);
  }, [config, pendingTab]);

  const openDecision = useCallback((decisionId: string) => {
    detailReturnFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    detailReturnFocusIdRef.current = decisionId;
    setSelectedDecisionId(decisionId);
    void decisions.loadDetail(decisionId);
  }, [decisions.loadDetail]);

  const closeDecision = useCallback(() => {
    setSelectedDecisionId(null);
    decisions.clearDetail();
    const returnFocus = detailReturnFocusRef.current;
    const returnFocusId = detailReturnFocusIdRef.current;
    if (returnFocus || returnFocusId) {
      setTimeout(() => {
        setTimeout(() => {
          const currentReturnFocus = returnFocusId
            ? Array.from(document.querySelectorAll<HTMLElement>("[data-row-id]"))
              .find((row) => row.dataset.rowId === returnFocusId)
              ?.querySelector<HTMLElement>('[data-slot="dropdown-menu-trigger"]')
            : null;
          (currentReturnFocus ?? returnFocus)?.focus();
          detailReturnFocusRef.current = null;
          detailReturnFocusIdRef.current = null;
        }, 0);
      }, 0);
    }
  }, [decisions.clearDetail]);

  useEffect(() => {
    if (
      !navigationTarget
      || processedTargetRef.current === navigationTarget.requestId
    ) return;
    processedTargetRef.current = navigationTarget.requestId;
    requestTab(navigationTarget.tab);
    if (navigationTarget.decisionId) openDecision(navigationTarget.decisionId);
  }, [navigationTarget, openDecision, requestTab]);

  const reportedDirtyRef = useRef(false);
  useEffect(() => {
    if (reportedDirtyRef.current === config.dirty) return;
    reportedDirtyRef.current = config.dirty;
    onDirtyChange?.(config.dirty);
  }, [config.dirty, onDirtyChange]);
  useEffect(() => () => {
    if (reportedDirtyRef.current) onDirtyChange?.(false);
  }, [onDirtyChange]);

  useEffect(() => {
    if (!config.dirty) return;
    const preventClose = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", preventClose);
    return () => window.removeEventListener("beforeunload", preventClose);
  }, [config.dirty]);

  const openTrace = useCallback((traceId: string) => {
    onNavigate("intelligence", {
      intelligenceTarget: {
        requestId: Date.now(),
        tab: "recallTrace",
        traceId,
      },
    });
  }, [onNavigate]);

  return (
    <PageFrame variant="dense" aria-label={t("injection.title")}>
      <PageHeader
        title={t("injection.title")}
        description={t("injection.subtitle")}
        icon={<SlidersHorizontal />}
      />
      <Tabs
        value={activeTab}
        onValueChange={requestTab}
        className="min-h-0 flex-1 gap-0 overflow-hidden"
      >
        <div className="shrink-0 overflow-x-auto border-b bg-background px-4 sm:px-5 lg:px-6">
          <TabsList
            variant="line"
            aria-label={t("injection.tabs.label")}
            className="h-11 min-w-max"
          >
            <TabsTrigger id="injection-tab-overview" value="overview">
              {t("injection.tabs.overview")}
            </TabsTrigger>
            <TabsTrigger id="injection-tab-config" value="config">
              {t("injection.tabs.config")}
            </TabsTrigger>
            <TabsTrigger id="injection-tab-decisions" value="decisions">
              {t("injection.tabs.decisions")}
            </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent
          value="overview"
          id="injection-panel-overview"
          aria-labelledby="injection-tab-overview"
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <PageContent width="constrained">
            <InjectionOverviewTab
              config={config}
              summary={summary}
              onEdit={() => setActiveTab("config")}
              onOpenTrace={openTrace}
            />
          </PageContent>
        </TabsContent>
        <TabsContent
          value="config"
          id="injection-panel-config"
          aria-labelledby="injection-tab-config"
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <PageContent width="constrained">
            <InjectionConfigTab config={config} showToast={showToast} />
          </PageContent>
        </TabsContent>
        <TabsContent
          value="decisions"
          id="injection-panel-decisions"
          aria-labelledby="injection-tab-decisions"
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <PageContent width="full" className="p-0">
            <InjectionDecisionsTab
              catalog={config.catalog}
              decisions={decisions}
              onOpenDecision={openDecision}
            />
          </PageContent>
        </TabsContent>
      </Tabs>
      <InjectionDecisionSheet
        open={selectedDecisionId !== null}
        catalog={config.catalog}
        decisions={decisions}
        selectedDecisionId={selectedDecisionId}
        onClose={closeDecision}
        onOpenTrace={openTrace}
      />
      <ConfigUnsavedDialog
        open={pendingTab !== null}
        onCancel={() => setPendingTab(null)}
        onDiscard={discardAndSwitch}
      />
    </PageFrame>
  );
}
