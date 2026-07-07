import { useState, useEffect, useCallback, lazy, Suspense } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Sidebar } from "@/components/layout/Sidebar";
import { Toast } from "@/components/ui/Toast";
import { SearchBar } from "@/components/ui/SearchBar";
import { ErrorBoundary } from "@/components/ui/ErrorBoundary";
import { useTheme } from "@/hooks/useTheme";
import { useToast } from "@/hooks/useToast";
import { useI18n, toggleLanguage } from "@/hooks/useI18n";
import { useRealtimeStream } from "@/hooks/useRealtimeStream";
import { Menu, Radio, Loader2 } from "lucide-react";
import type { PageId } from "@/types";

// Lazy-load each page so its dependencies (e.g. @antv/g6 for GraphPage,
// @tanstack/react-virtual for MemoryPage) are only fetched when the page
// is actually visited. This keeps the entry bundle small.
const GraphPage = lazy(() => import("@/pages/GraphPage").then(m => ({ default: m.GraphPage })));
const MemoryPage = lazy(() => import("@/pages/MemoryPage").then(m => ({ default: m.MemoryPage })));
const RecallPage = lazy(() => import("@/pages/RecallPage").then(m => ({ default: m.RecallPage })));
const SystemPage = lazy(() => import("@/pages/SystemPage").then(m => ({ default: m.SystemPage })));
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
  const { toast, showToast } = useToast();
  const [currentPage, setCurrentPage] = useState<PageId>(getPageFromHash);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const { connected: sseConnected, unreadCount, lastEvent, markSeen } = useRealtimeStream();

  useEffect(() => {
    const handler = () => setCurrentPage(getPageFromHash());
    window.addEventListener("hashchange", handler);
    return () => window.removeEventListener("hashchange", handler);
  }, []);

  const navigate = useCallback((page: PageId) => {
    window.location.hash = `#/${page}`;
    setMobileMenuOpen(false);
  }, []);

  const cycleLanguage = useCallback(() => {
    toggleLanguage();
  }, []);

  return (
    <div className="flex h-screen overflow-hidden">
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

      <main className="flex-1 overflow-hidden bg-[var(--color-surface)] flex flex-col">
        {/* Mobile header bar */}
        <div className="md:hidden flex h-12 items-center gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-4 shrink-0">
          <button
            onClick={() => setMobileMenuOpen(true)}
            className="p-1.5 rounded-lg hover:bg-[var(--color-surface)] relative"
            aria-label="Open menu"
          >
            <Menu size={20} />
            {unreadCount > 0 && (
              <span className="absolute -top-0.5 -right-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-[var(--color-danger)] text-2xs font-bold text-white animate-pop-in">
                {unreadCount > 9 ? "9+" : unreadCount}
              </span>
            )}
          </button>
          <div className="flex items-center gap-1.5 ml-1">
            <div className={`h-1.5 w-1.5 rounded-full ${sseConnected ? "bg-[var(--color-success)]" : "bg-[var(--text-tertiary)]"}`} />
            <span className="text-2xs text-[var(--text-tertiary)]">
              {sseConnected ? "实时" : "离线"}
            </span>
          </div>
          <SearchBar onNavigate={(page) => navigate(page)} />
        </div>

        <div className="flex-1 overflow-auto">
          <ErrorBoundary>
            <AnimatePresence mode="wait">
              <motion.div key={currentPage} {...pageTransition} className="h-full">
                <Suspense fallback={<PageLoader />}>
                {currentPage === "preview" && <PreviewPage showToast={showToast} />}
                {currentPage === "graph" && <GraphPage showToast={showToast} />}
                {currentPage === "memory" && <MemoryPage showToast={showToast} />}
                {currentPage === "timeline" && <TimelinePage showToast={showToast} />}
                {currentPage === "recall" && <RecallPage showToast={showToast} />}
                {currentPage === "system" && <SystemPage showToast={showToast} />}
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
    </div>
  );
}
