import { useRef, useCallback } from "react";
import { cn } from "@/lib/utils";
import { useI18n } from "@/hooks/useI18n";
import type { PageId } from "@/types";
import {
  LayoutDashboard, GitGraph, ScrollText, Search, BarChart3,
  UserRound, BookOpen, StickyNote, Brain, Moon, Sun, Languages,
  X, Clock, MessageCircleCode, Heart, UsersRound, BrainCircuit,
} from "lucide-react";

interface NavItem {
  id: PageId;
  label: string;
  icon: React.ReactNode;
}

interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
  ts: number;
}

interface SidebarProps {
  currentPage: PageId;
  onNavigate: (page: PageId) => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onCycleLanguage: () => void;
  mobileOpen?: boolean;
  onCloseMobile?: () => void;
  sseConnected?: boolean;
  unreadCount?: number;
  lastEvent?: StreamEvent | null;
  onMarkSeen?: () => void;
}

const SWIPE_THRESHOLD = 60;

export function Sidebar({ currentPage, onNavigate, theme, onToggleTheme, onCycleLanguage, mobileOpen, onCloseMobile, sseConnected, unreadCount, lastEvent, onMarkSeen }: SidebarProps) {
  const { t, currentLang } = useI18n();
  const touchStartX = useRef(0);
  const touchStartY = useRef(0);

  const langLabel = { zh: "ZH", en: "EN", ru: "RU" }[currentLang()] ?? currentLang().toUpperCase();

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    touchStartX.current = e.touches[0].clientX;
    touchStartY.current = e.touches[0].clientY;
  }, []);

  const handleTouchEnd = useCallback((e: React.TouchEvent) => {
    const dx = e.changedTouches[0].clientX - touchStartX.current;
    const dy = Math.abs(e.changedTouches[0].clientY - touchStartY.current);
    // Only trigger if horizontal swipe left exceeds threshold and dominates vertical
    if (dx < -SWIPE_THRESHOLD && Math.abs(dx) > dy && onCloseMobile) {
      onCloseMobile();
    }
  }, [onCloseMobile]);

  const primaryNav: NavItem[] = [
    { id: "preview", label: t("nav.preview"), icon: <LayoutDashboard size={18} /> },
    { id: "graph", label: t("nav.graph"), icon: <GitGraph size={18} /> },
    { id: "memory", label: t("nav.memory"), icon: <ScrollText size={18} /> },
    { id: "timeline", label: t("nav.timeline"), icon: <Clock size={18} /> },
    { id: "recall", label: t("nav.recall"), icon: <Search size={18} /> },
    { id: "intelligence", label: t("nav.intelligence"), icon: <BrainCircuit size={18} /> },
    { id: "system", label: t("nav.system"), icon: <BarChart3 size={18} /> },
  ];

  const secondaryNav: NavItem[] = [
    { id: "profiles", label: t("nav.profiles"), icon: <UserRound size={18} /> },
    { id: "knowledge", label: t("nav.knowledge"), icon: <BookOpen size={18} /> },
    { id: "notes", label: t("nav.notes"), icon: <StickyNote size={18} /> },
    { id: "learning", label: t("nav.learning"), icon: <Brain size={18} /> },
    { id: "jargon", label: t("nav.jargon"), icon: <MessageCircleCode size={18} /> },
    { id: "affection", label: t("nav.affection"), icon: <Heart size={18} /> },
    { id: "social", label: t("nav.social"), icon: <UsersRound size={18} /> },
  ];

  const renderNav = (items: NavItem[]) =>
    items.map((item) => (
      <button
        key={item.id}
        onClick={() => onNavigate(item.id)}
        className={cn(
          "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-all duration-150",
          currentPage === item.id
            ? "bg-[var(--color-accent)]/10 text-[var(--color-accent)]"
            : "text-[var(--text-secondary)] hover:bg-[var(--color-surface-secondary)] hover:text-[var(--text-primary)]"
        )}
      >
        {item.icon}
        <span>{item.label}</span>
      </button>
    ));

  const sidebarContent = (
    <aside
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
      className={cn(
      "flex h-full w-60 shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface-secondary)]",
      // Desktop: always visible. Mobile: fixed overlay when open, hidden otherwise.
      "max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-40 max-md:transition-transform max-md:duration-200",
      mobileOpen !== undefined && !mobileOpen ? "max-md:-translate-x-full" : "max-md:translate-x-0",
    )}>
      <div className="flex h-14 items-center gap-2.5 border-b border-[var(--color-border)] px-5">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-[var(--color-accent)] text-white">
          <Brain size={15} />
        </div>
        <span className="text-sm font-semibold text-[var(--text-primary)]">Memora</span>
        {onCloseMobile && (
          <button onClick={onCloseMobile} className="md:hidden ml-auto p-1 rounded-lg hover:bg-[var(--color-surface)]">
            <X size={18} />
          </button>
        )}
      </div>
      <nav className="flex flex-col gap-1 px-3 py-4">
        {renderNav(primaryNav)}
      </nav>
      <div className="mx-4 h-px bg-[var(--color-border-light)]" />
      <nav className="flex flex-col gap-1 px-3 py-4">
        {renderNav(secondaryNav)}
      </nav>
      <div className="mt-auto border-t border-[var(--color-border)] p-3 space-y-1">
        {/* SSE live indicator */}
        {sseConnected !== undefined && (
          <div className="flex items-center gap-2 px-3 py-1.5">
            <div className={`h-2 w-2 rounded-full ${sseConnected ? "bg-[var(--color-success)] animate-pulse" : "bg-[var(--text-tertiary)]"}`} />
            <span className="text-2xs text-[var(--text-tertiary)]">
              {sseConnected ? "实时连接" : "离线"}
            </span>
            {unreadCount !== undefined && unreadCount > 0 && (
              <button
                onClick={onMarkSeen}
                className="ml-auto rounded-full bg-[var(--color-accent)] px-1.5 py-px text-2xs font-bold text-white hover:bg-[var(--color-accent-secondary)]"
              >
                {unreadCount > 99 ? "99+" : unreadCount}
              </button>
            )}
          </div>
        )}
        {lastEvent && sseConnected && (
          <div className="px-3 pb-1">
            <p className="text-2xs text-[var(--text-tertiary)] truncate">
              {lastEvent.event}: {String(lastEvent.data?.content ?? lastEvent.data?.doc_id ?? "").slice(0, 30)}
            </p>
          </div>
        )}
        <button
          onClick={onToggleTheme}
          className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-[var(--text-secondary)] hover:bg-[var(--color-surface)] hover:text-[var(--text-primary)] transition-all duration-150"
        >
          {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          <span>{t("header.theme")}</span>
        </button>
        <button onClick={onCycleLanguage} className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-[var(--text-secondary)] hover:bg-[var(--color-surface)] hover:text-[var(--text-primary)] transition-all duration-150">
          <Languages size={18} />
          <span className="flex-1">{t("header.lang")}</span>
          <span className="text-xs font-mono opacity-60">{langLabel}</span>
        </button>
      </div>
    </aside>
  );

  return (
    <>
      {/* Mobile backdrop */}
      {mobileOpen && onCloseMobile && (
        <div
          className="md:hidden fixed inset-0 z-30 bg-black/30 animate-fade-in"
          onClick={onCloseMobile}
        />
      )}
      {sidebarContent}
    </>
  );
}
