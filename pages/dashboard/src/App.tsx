import { useState, useEffect, useCallback, useRef, lazy, Suspense } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ConfigUnsavedDialog } from "@/components/config/ConfigUnsavedDialog";
import { Sidebar } from "@/components/layout/Sidebar";
import { Button } from "@/components/ui/Button";
import { Toast } from "@/components/ui/Toast";
import { SearchBar } from "@/components/ui/SearchBar";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { useTheme } from "@/hooks/useTheme";
import { useToast } from "@/hooks/useToast";
import { useI18n, toggleLanguage } from "@/hooks/useI18n";
import { useRealtimeStream } from "@/hooks/useRealtimeStream";
import { Menu, Loader2 } from "lucide-react";
import type { PageId } from "@/types";

// Lazy-load each page so its dependencies (e.g. @antv/g6 for GraphPage,
// @tanstack/react-virtual for MemoryPage) are only fetched when the page
// is actually visited. This keeps the entry bundle small.
const GraphPage = lazy(() => import("@/pages/GraphPage").then(m => ({ default: m.GraphPage })));
const MemoryPage = lazy(() => import("@/pages/MemoryPage").then(m => ({ default: m.MemoryPage })));
const RecallPage = lazy(() => import("@/pages/RecallPage").then(m => ({ default: m.RecallPage })));
const SystemPage = lazy(() => import("@/pages/SystemPage").then(m => ({ default: m.SystemPage })));
const ConfigPage = lazy(() => import("@/pages/ConfigPage").then(m => ({ default: m.ConfigPage })));
const ProfilesPage = lazy(() => import("@/pages/ProfilesPage").then(m => ({ default: m.ProfilesPage })));
const KnowledgePage = lazy(() => import("@/pages/KnowledgePage").then(m => ({ default: m.KnowledgePage })));
const NotesPage = lazy(() => import("@/pages/NotesPage").then(m => ({ default: m.NotesPage })));
const LearningPage = lazy(() => import("@/pages/LearningPage").then(m => ({ default: m.LearningPage })));
const PreviewPage = lazy(() => import("@/pages/PreviewPage").then(m => ({ default: m.PreviewPage })));
const TimelinePage = lazy(() => import("@/pages/TimelinePage").then(m => ({ default: m.TimelinePage })));
const JargonPage = lazy(() => import("@/pages/JargonPage").then(m => ({ default: m.JargonPage })));
const AffectionPage = lazy(() => import("@/pages/AffectionPage").then(m => ({ default: m.AffectionPage })));
const SocialPage = lazy(() => import("@/pages/SocialPage").then(m => ({ default: m.SocialPage })));
const IntelligencePage = lazy(() => import("@/pages/IntelligencePage").then(m => ({ default: m.IntelligencePage })));

/** Lightweight fallback shown while a page chunk loads over the network. */
function PageLoader() {
  return (
    <div className="flex h-full items-center justify-center">
      <Loader2 size={28} className="animate-spin text-[var(--text-tertiary)]" />
    </div>
  );
}

const pageTransition = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -8 },
  transition: { duration: 0.2, ease: "easeOut" as const },
};

const HASH_TO_PAGE: Record<string, PageId> = {
  preview: "preview",
  graph: "graph", memory: "memory", timeline: "timeline",
  recall: "recall", system: "system",
  config: "config",
  profiles: "profiles", knowledge: "knowledge", notes: "notes", learning: "learning",
  jargon: "jargon", affection: "affection", social: "social",
  intelligence: "intelligence",
};

function getPageFromHash(): PageId {
  const hash = window.location.hash.replace("#/", "").replace("#", "");
  return HASH_TO_PAGE[hash] ?? "graph";
}

export default function App() {
  const { theme, toggleTheme } = useTheme();
  const { t } = useI18n();
  const { toast, showToast } = useToast();
  const [currentPage, setCurrentPage] = useState<PageId>(getPageFromHash);
  const [pendingPage, setPendingPage] = useState<PageId | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const currentPageRef = useRef(currentPage);
  const configDirtyRef = useRef(false);
  const pendingPageRef = useRef<PageId | null>(null);
  const { connected: sseConnected, unreadCount, lastEvent, markSeen } = useRealtimeStream();

  const applyPage = useCallback((page: PageId) => {
    currentPageRef.current = page;
    setCurrentPage(page);
    setMobileMenuOpen(false);
  }, []);

  const commitNavigation = useCallback((page: PageId) => {
    applyPage(page);
    const nextHash = `#/${page}`;
    if (window.location.hash !== nextHash) {
      window.location.hash = nextHash;
    }
  }, [applyPage]);

  const navigate = useCallback((page: PageId) => {
    setMobileMenuOpen(false);
    if (page === currentPageRef.current) return;

    if (currentPageRef.current === "config" && configDirtyRef.current) {
      pendingPageRef.current = page;
      setPendingPage(page);
      return;
    }

    commitNavigation(page);
  }, [commitNavigation]);

  const handleConfigDirtyChange = useCallback((dirty: boolean) => {
    configDirtyRef.current = dirty;
    if (dirty || pendingPageRef.current === null) return;

    pendingPageRef.current = null;
    setPendingPage(null);
  }, []);

  const cancelPendingNavigation = useCallback(() => {
    pendingPageRef.current = null;
    setPendingPage(null);
  }, []);

  const discardAndNavigate = useCallback(() => {
    const target = pendingPageRef.current;
    pendingPageRef.current = null;
    configDirtyRef.current = false;
    setPendingPage(null);
    if (target !== null) commitNavigation(target);
  }, [commitNavigation]);

  useEffect(() => {
    const handler = () => {
      const target = getPageFromHash();
      if (target === currentPageRef.current) return;

      if (currentPageRef.current === "config" && configDirtyRef.current) {
        pendingPageRef.current = target;
        setPendingPage(target);
        if (window.location.hash !== "#/config") {
          window.history.replaceState(window.history.state, "", "#/config");
        }
        return;
      }

      applyPage(target);
    };
    window.addEventListener("hashchange", handler);
    return () => window.removeEventListener("hashchange", handler);
  }, [applyPage]);

  const showConfigToast = useCallback((
    message: string,
    type?: "success" | "error" | "info",
  ) => {
    showToast(message, type === "error");
  }, [showToast]);

  const cycleLanguage = useCallback(() => {
    toggleLanguage();
  }, []);

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <Sidebar
        currentPage={currentPage}
        onNavigate={navigate}
        theme={theme}
        onToggleTheme={toggleTheme}
        onCycleLanguage={cycleLanguage}
        mobileOpen={mobileMenuOpen}
        onCloseMobile={() => setMobileMenuOpen(false)}
        sseConnected={sseConnected}
        unreadCount={unreadCount}
        lastEvent={lastEvent}
        onMarkSeen={markSeen}
      />

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-background">
        <header
          data-slot="app-header"
          className="flex h-14 shrink-0 items-center gap-3 border-b bg-background px-3 sm:px-4 lg:px-5"
        >
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => setMobileMenuOpen(true)}
            aria-label={t("header.openMenu")}
            className="relative md:hidden"
          >
            <Menu />
            {unreadCount > 0 && (
              <span className="absolute -right-0.5 -top-0.5 flex size-4 items-center justify-center rounded-full bg-destructive text-[10px] font-bold text-white animate-pop-in">
                {unreadCount > 9 ? "9+" : unreadCount}
              </span>
            )}
          </Button>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-foreground">{t(`nav.${currentPage}`)}</p>
            <p className="hidden text-xs text-muted-foreground sm:block">{t("header.dashboardTitle")}</p>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className={`size-2 rounded-full ${sseConnected ? "bg-emerald-500" : "bg-muted-foreground"}`} />
            <span className="hidden sm:inline">
              {sseConnected ? t("status.realtime") : t("status.offline")}
            </span>
          </div>
          <SearchBar onNavigate={navigate} />
        </header>

        <div className="min-h-0 flex-1 overflow-hidden">
          <ErrorBoundary>
            <AnimatePresence mode="wait">
              <motion.div key={currentPage} {...pageTransition} className="h-full min-h-0">
                <Suspense fallback={<PageLoader />}>
                {currentPage === "preview" && <PreviewPage showToast={showToast} />}
                {currentPage === "graph" && <GraphPage showToast={showToast} />}
                {currentPage === "memory" && <MemoryPage showToast={showToast} />}
                {currentPage === "timeline" && <TimelinePage showToast={showToast} />}
                {currentPage === "recall" && <RecallPage showToast={showToast} />}
                {currentPage === "system" && <SystemPage showToast={showToast} />}
                {currentPage === "config" && (
                  <ConfigPage
                    showToast={showConfigToast}
                    onDirtyChange={handleConfigDirtyChange}
                  />
                )}
                {currentPage === "profiles" && <ProfilesPage showToast={showToast} />}
                {currentPage === "knowledge" && <KnowledgePage showToast={showToast} />}
                {currentPage === "notes" && <NotesPage showToast={showToast} />}
                {currentPage === "learning" && <LearningPage showToast={showToast} />}
                {currentPage === "jargon" && <JargonPage showToast={showToast} />}
                {currentPage === "affection" && <AffectionPage showToast={showToast} />}
                {currentPage === "social" && <SocialPage showToast={showToast} />}
                {currentPage === "intelligence" && <IntelligencePage showToast={showToast} />}
                </Suspense>
              </motion.div>
            </AnimatePresence>
          </ErrorBoundary>
        </div>
      </main>

      <Toast toast={toast} />
      <ConfigUnsavedDialog
        open={pendingPage !== null}
        onCancel={cancelPendingNavigation}
        onDiscard={discardAndNavigate}
      />
    </div>
  );
}
