import { useState } from "react";
import { Activity, AlertTriangle, BrainCircuit, ClipboardCheck, GitBranch, Stethoscope } from "lucide-react";
import { DiagnosticCenter } from "@/components/intelligence/DiagnosticCenter";
import { EvaluationWorkbench } from "@/components/intelligence/EvaluationWorkbench";
import { RecallTracePanel } from "@/components/intelligence/RecallTracePanel";
import { ReviewQueue } from "@/components/intelligence/ReviewQueue";
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

interface PanelProps {
  showToast: IntelligencePageProps["showToast"];
  t: (key: string, ...args: string[]) => string;
}

const tabs: TabDefinition[] = [
  { id: "evaluation", labelKey: "intelligence.tabs.evaluation", icon: <ClipboardCheck size={14} /> },
  { id: "recallTrace", labelKey: "intelligence.tabs.recallTrace", icon: <GitBranch size={14} /> },
  { id: "diagnostics", labelKey: "intelligence.tabs.diagnostics", icon: <Stethoscope size={14} /> },
  { id: "reviewQueue", labelKey: "intelligence.tabs.reviewQueue", icon: <AlertTriangle size={14} /> },
];

function EvaluationPanel({ showToast }: PanelProps) {
  return <EvaluationWorkbench showToast={showToast} />;
}

function RecallTraceTabPanel({ showToast }: PanelProps) {
  return <RecallTracePanel showToast={showToast} />;
}

function DiagnosticsPanel({ showToast }: PanelProps) {
  return <DiagnosticCenter showToast={showToast} />;
}

function ReviewQueuePanel({ showToast }: PanelProps) {
  return <ReviewQueue showToast={showToast} />;
}

const panelByTab: Record<IntelligenceTabId, (props: PanelProps) => JSX.Element> = {
  evaluation: EvaluationPanel,
  recallTrace: RecallTraceTabPanel,
  diagnostics: DiagnosticsPanel,
  reviewQueue: ReviewQueuePanel,
};

export function IntelligencePage({ showToast }: IntelligencePageProps) {
  const { t } = useI18n();
  const [activeTab, setActiveTab] = useState<IntelligenceTabId>("evaluation");
  const ActivePanel = panelByTab[activeTab];
  const activeLabel = t(tabs.find((tab) => tab.id === activeTab)?.labelKey ?? "intelligence.tabs.evaluation");
  const panelId = "intelligence-panel";

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-6 py-3">
        <div className="flex items-center gap-3">
          <BrainCircuit size={18} className="text-[var(--color-accent)]" />
          <div>
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">{t("intelligence.title")}</h2>
            <p className="mt-0.5 text-xs text-[var(--text-tertiary)]">{t("intelligence.subtitle")}</p>
          </div>
        </div>
        <div className="hidden items-center gap-2 text-xs text-[var(--text-tertiary)] sm:flex">
          <Activity size={14} />
          <span>{t("intelligence.status.shell")}</span>
        </div>
      </header>

      <div className="shrink-0 border-b border-[var(--color-border)] px-6">
        <div role="tablist" aria-label={t("intelligence.tabs.label")} className="flex gap-1 overflow-x-auto">
          {tabs.map((tab) => {
            const selected = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                id={`intelligence-tab-${tab.id}`}
                role="tab"
                type="button"
                aria-selected={selected}
                aria-controls={panelId}
                onClick={() => setActiveTab(tab.id)}
                className={`flex h-10 items-center gap-1.5 border-b-2 px-3 text-xs font-medium transition-colors ${
                  selected
                    ? "border-[var(--color-accent)] text-[var(--color-accent)]"
                    : "border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
                }`}
              >
                {tab.icon}
                {t(tab.labelKey)}
              </button>
            );
          })}
        </div>
      </div>

      <main
        id={panelId}
        role="tabpanel"
        aria-labelledby={`intelligence-tab-${activeTab}`}
        className="flex-1 overflow-auto p-6"
      >
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <p className="text-2xs uppercase tracking-normal text-[var(--text-tertiary)]">{t("intelligence.activeTab")}</p>
            <h3 className="mt-1 text-lg font-semibold text-[var(--text-primary)]">{activeLabel}</h3>
          </div>
          <span className="rounded-full border border-[var(--color-border)] px-2.5 py-1 text-2xs font-medium text-[var(--text-secondary)]">
            {t("intelligence.status.local")}
          </span>
        </div>
        <ActivePanel showToast={showToast} t={t} />
      </main>
    </div>
  );
}
