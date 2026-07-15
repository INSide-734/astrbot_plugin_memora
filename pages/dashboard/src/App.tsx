import { useState, useEffect, useCallback, useRef, lazy, Suspense } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { UnsavedChangesDialog } from "@/components/editing/UnsavedChangesDialog";
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
import type { PageId, PageNavigationIntent } from "@/types";

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

const HISTORY_INDEX_KEY = "__memoraHistoryIndex";
const HISTORY_GUARD_KEY = "__memoraHistoryGuard";

function getHistoryIndex(state: unknown): number | null {
  if (!state || typeof state !== "object") return null;
  const value = (state as Record<string, unknown>)[HISTORY_INDEX_KEY];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function withHistoryIndex(
  state: unknown,
  index: number,
  extra: Record<string, unknown> = {},
) {
  const base = state && typeof state === "object"
    ? state as Record<string, unknown>
    : {};
  return { ...base, ...extra, [HISTORY_INDEX_KEY]: index };
}

function getPageFromHash(): PageId {
  const hash = window.location.hash.replace("#/", "").replace("#", "");
  return HASH_TO_PAGE[hash] ?? "graph";
}

export default function App() {
  const { theme, toggleTheme } = useTheme();
  const { t } = useI18n();
  const { toast, showToast } = useToast();
  const [currentPage, setCurrentPage] = useState<PageId>(getPageFromHash);
  const [currentPageDirty, setCurrentPageDirty] = useState(false);
  const [pendingPage, setPendingPage] = useState<PageId | null>(null);
  const [navigationIntent, setNavigationIntent] =
    useState<PageNavigationIntent | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const currentPageRef = useRef(currentPage);
  const currentPageDirtyRef = useRef(false);
  const pendingPageRef = useRef<PageId | null>(null);
  const pendingIntentRef = useRef<PageNavigationIntent | undefined>(undefined);
  const pendingHistoryDeltaRef = useRef<number | null>(null);
  const historyIndexRef = useRef(0);
  const restoringHistoryRef = useRef(false);
  const replayingHistoryRef = useRef(false);
  const ignoredHashChangesRef = useRef(0);
  const browserHashRef = useRef(window.location.hash);
  const { connected: sseConnected, unreadCount, lastEvent, markSeen } = useRealtimeStream();

  const applyPage = useCallback((
    page: PageId,
    intent?: PageNavigationIntent,
  ) => {
    currentPageRef.current = page;
    setCurrentPage(page);
    setNavigationIntent(intent ?? null);
    setMobileMenuOpen(false);
  }, []);

  const commitNavigation = useCallback((
    page: PageId,
    intent?: PageNavigationIntent,
  ) => {
    const nextHash = `#/${page}`;
    const nextIndex = historyIndexRef.current + 1;
    window.history.pushState(withHistoryIndex(null, nextIndex), "", nextHash);
    historyIndexRef.current = nextIndex;
    browserHashRef.current = nextHash;
    applyPage(page, intent);
  }, [applyPage]);

  const navigate = useCallback((
    page: PageId,
    intent?: PageNavigationIntent,
  ) => {
    setMobileMenuOpen(false);
    if (page === currentPageRef.current) {
      const currentEntityId = navigationIntent?.entityTarget?.id;
      const requestedEntityId = intent?.entityTarget?.id;
      if (currentPageDirtyRef.current && requestedEntityId && requestedEntityId !== currentEntityId) {
        pendingPageRef.current = page;
        pendingIntentRef.current = intent;
        pendingHistoryDeltaRef.current = null;
        setPendingPage(page);
        return;
      }
      if (intent) setNavigationIntent(intent);
      return;
    }

    if (currentPageDirtyRef.current) {
      pendingPageRef.current = page;
      pendingIntentRef.current = intent;
      pendingHistoryDeltaRef.current = null;
      setPendingPage(page);
      return;
    }

    commitNavigation(page, intent);
  }, [commitNavigation, navigationIntent]);

  const handleCurrentPageDirtyChange = useCallback((dirty: boolean) => {
    currentPageDirtyRef.current = dirty;
    setCurrentPageDirty(dirty);
    if (dirty || pendingPageRef.current === null) return;

    pendingPageRef.current = null;
    pendingIntentRef.current = undefined;
    pendingHistoryDeltaRef.current = null;
    setPendingPage(null);
  }, []);

  const cancelPendingNavigation = useCallback(() => {
    pendingPageRef.current = null;
    pendingIntentRef.current = undefined;
    pendingHistoryDeltaRef.current = null;
    setPendingPage(null);
  }, []);

  const discardAndNavigate = useCallback(() => {
    const target = pendingPageRef.current;
    const intent = pendingIntentRef.current;
    const historyDelta = pendingHistoryDeltaRef.current;
    pendingPageRef.current = null;
    pendingIntentRef.current = undefined;
    pendingHistoryDeltaRef.current = null;
    currentPageDirtyRef.current = false;
    setCurrentPageDirty(false);
    setPendingPage(null);
    if (historyDelta !== null) {
      replayingHistoryRef.current = true;
      window.history.go(historyDelta);
    } else if (target !== null) {
      commitNavigation(target, intent);
    }
  }, [commitNavigation]);

  const blockHistoryNavigation = useCallback((
    target: PageId,
    targetIndex: number | null,
  ) => {
    pendingPageRef.current = target;
    pendingIntentRef.current = undefined;

    if (targetIndex === null || targetIndex === historyIndexRef.current) {
      const sentinelIndex = historyIndexRef.current + 1;
      window.history.pushState(
        withHistoryIndex(null, sentinelIndex, { [HISTORY_GUARD_KEY]: true }),
        "",
        `#/${currentPageRef.current}`,
      );
      historyIndexRef.current = sentinelIndex;
      browserHashRef.current = `#/${currentPageRef.current}`;
      pendingHistoryDeltaRef.current = -1;
      setPendingPage(target);
      return;
    }

    const historyDelta = targetIndex - historyIndexRef.current;
    pendingHistoryDeltaRef.current = historyDelta;
    restoringHistoryRef.current = true;
    window.history.go(-historyDelta);
  }, []);

  const handleHistoryArrival = useCallback((
    target: PageId,
    targetIndex: number | null,
  ) => {
    if (restoringHistoryRef.current) {
      restoringHistoryRef.current = false;
      if (targetIndex !== null) historyIndexRef.current = targetIndex;
      if (pendingPageRef.current !== null) {
        setPendingPage(pendingPageRef.current);
      }
      return;
    }

    if (replayingHistoryRef.current) {
      replayingHistoryRef.current = false;
      if (targetIndex !== null) historyIndexRef.current = targetIndex;
      applyPage(target);
      return;
    }

    if (target === currentPageRef.current) {
      if (targetIndex !== null) historyIndexRef.current = targetIndex;
      return;
    }

    if (currentPageDirtyRef.current) {
      blockHistoryNavigation(target, targetIndex);
      return;
    }

    if (targetIndex !== null) historyIndexRef.current = targetIndex;
    applyPage(target);
  }, [applyPage, blockHistoryNavigation]);

  useEffect(() => {
    const initialIndex = getHistoryIndex(window.history.state) ?? 0;
    if (getHistoryIndex(window.history.state) === null) {
      window.history.replaceState(
        withHistoryIndex(window.history.state, initialIndex),
        "",
        window.location.href,
      );
    }
    historyIndexRef.current = initialIndex;
    browserHashRef.current = window.location.hash;

    const handlePopState = (event: PopStateEvent) => {
      const nextHash = window.location.hash;
      if (nextHash !== browserHashRef.current) {
        ignoredHashChangesRef.current += 1;
      }
      browserHashRef.current = nextHash;
      handleHistoryArrival(getPageFromHash(), getHistoryIndex(event.state));
    };

    const handleHashChange = () => {
      if (ignoredHashChangesRef.current > 0) {
        ignoredHashChangesRef.current -= 1;
        browserHashRef.current = window.location.hash;
        return;
      }

      const target = getPageFromHash();
      browserHashRef.current = window.location.hash;
      if (restoringHistoryRef.current || replayingHistoryRef.current) return;

      let targetIndex = getHistoryIndex(window.history.state);
      if (targetIndex === null || targetIndex === historyIndexRef.current) {
        targetIndex = historyIndexRef.current + 1;
        window.history.replaceState(
          withHistoryIndex(window.history.state, targetIndex),
          "",
          window.location.href,
        );
      }

      if (target !== currentPageRef.current
        && currentPageDirtyRef.current) {
        pendingPageRef.current = target;
        pendingIntentRef.current = undefined;
        pendingHistoryDeltaRef.current = null;
        window.history.replaceState(
          withHistoryIndex(window.history.state, targetIndex),
          "",
          `#/${currentPageRef.current}`,
        );
        historyIndexRef.current = targetIndex;
        browserHashRef.current = `#/${currentPageRef.current}`;
        setPendingPage(target);
        return;
      }

      handleHistoryArrival(target, targetIndex);
    };

    window.addEventListener("popstate", handlePopState);
    window.addEventListener("hashchange", handleHashChange);
    return () => {
      window.removeEventListener("popstate", handlePopState);
      window.removeEventListener("hashchange", handleHashChange);
    };
  }, [handleHistoryArrival]);

  useEffect(() => {
    if (!currentPageDirty) return;
    const preventUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", preventUnload);
    return () => window.removeEventListener("beforeunload", preventUnload);
  }, [currentPageDirty]);

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
                {currentPage === "graph" && <GraphPage showToast={showToast} theme={theme} />}
                {currentPage === "memory" && (
                  <MemoryPage
                    showToast={showToast}
                    navigationTarget={navigationIntent?.entityTarget ?? null}
                    onDirtyChange={handleCurrentPageDirtyChange}
                  />
                )}
                {currentPage === "timeline" && <TimelinePage showToast={showToast} />}
                {currentPage === "recall" && <RecallPage showToast={showToast} />}
                {currentPage === "system" && <SystemPage showToast={showToast} />}
                {currentPage === "config" && (
                  <ConfigPage
                    navigationTarget={navigationIntent?.configTarget ?? null}
                    showToast={showConfigToast}
                    onDirtyChange={handleCurrentPageDirtyChange}
                  />
                )}
                {currentPage === "profiles" && <ProfilesPage showToast={showToast} />}
                {currentPage === "knowledge" && (
                  <KnowledgePage
                    showToast={showToast}
                    navigationTarget={navigationIntent?.entityTarget ?? null}
                    onDirtyChange={handleCurrentPageDirtyChange}
                  />
                )}
                {currentPage === "notes" && (
                  <NotesPage
                    showToast={showToast}
                    navigationTarget={navigationIntent?.entityTarget ?? null}
                    onDirtyChange={handleCurrentPageDirtyChange}
                  />
                )}
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
      <UnsavedChangesDialog
        open={pendingPage !== null}
        title={t("config.unsaved.title")}
        description={t("config.unsaved.description")}
        keepEditingLabel={t("config.unsaved.keepEditing")}
        discardLabel={t("config.unsaved.discard")}
        onKeepEditing={cancelPendingNavigation}
        onDiscard={discardAndNavigate}
      />
    </div>
  );
}
