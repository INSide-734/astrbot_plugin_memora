import { useCallback, useRef, useState, type ReactNode } from "react";
import {
  BarChart3,
  BookOpen,
  Brain,
  BrainCircuit,
  ChevronDown,
  Clock,
  GitGraph,
  Heart,
  Languages,
  LayoutDashboard,
  MessageCircleCode,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  ScrollText,
  Search,
  StickyNote,
  Sun,
  UserRound,
  UsersRound,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/Button";
import { useI18n } from "@/hooks/useI18n";
import { cn } from "@/lib/utils";
import type { PageId } from "@/types";

interface NavItem {
  id: PageId;
  label: string;
  icon: ReactNode;
}

interface NavGroup {
  id: "overview" | "memory" | "insights" | "relationships" | "system";
  label: string;
  items: NavItem[];
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

export function Sidebar({
  currentPage,
  lastEvent,
  mobileOpen,
  onCloseMobile,
  onCycleLanguage,
  onMarkSeen,
  onNavigate,
  onToggleTheme,
  sseConnected,
  theme,
  unreadCount,
}: SidebarProps) {
  const { t, currentLang } = useI18n();
  const [collapsed, setCollapsed] = useState(false);
  const [openGroups, setOpenGroups] = useState<Record<NavGroup["id"], boolean>>({
    overview: true,
    memory: true,
    insights: true,
    relationships: true,
    system: true,
  });
  const touchStartX = useRef(0);
  const touchStartY = useRef(0);

  const langLabel = { zh: "ZH", en: "EN", ru: "RU" }[currentLang()] ?? currentLang().toUpperCase();

  const groups: NavGroup[] = [
    {
      id: "overview",
      label: t("nav.groupOverview"),
      items: [{ id: "preview", label: t("nav.preview"), icon: <LayoutDashboard /> }],
    },
    {
      id: "memory",
      label: t("nav.groupMemory"),
      items: [
        { id: "graph", label: t("nav.graph"), icon: <GitGraph /> },
        { id: "memory", label: t("nav.memory"), icon: <ScrollText /> },
        { id: "timeline", label: t("nav.timeline"), icon: <Clock /> },
        { id: "recall", label: t("nav.recall"), icon: <Search /> },
        { id: "knowledge", label: t("nav.knowledge"), icon: <BookOpen /> },
        { id: "notes", label: t("nav.notes"), icon: <StickyNote /> },
      ],
    },
    {
      id: "insights",
      label: t("nav.groupInsights"),
      items: [
        { id: "intelligence", label: t("nav.intelligence"), icon: <BrainCircuit /> },
        { id: "learning", label: t("nav.learning"), icon: <Brain /> },
        { id: "jargon", label: t("nav.jargon"), icon: <MessageCircleCode /> },
      ],
    },
    {
      id: "relationships",
      label: t("nav.groupRelationships"),
      items: [
        { id: "profiles", label: t("nav.profiles"), icon: <UserRound /> },
        { id: "affection", label: t("nav.affection"), icon: <Heart /> },
        { id: "social", label: t("nav.social"), icon: <UsersRound /> },
      ],
    },
    {
      id: "system",
      label: t("nav.groupSystem"),
      items: [{ id: "system", label: t("nav.system"), icon: <BarChart3 /> }],
    },
  ];

  const handleTouchStart = useCallback((event: React.TouchEvent) => {
    touchStartX.current = event.touches[0].clientX;
    touchStartY.current = event.touches[0].clientY;
  }, []);

  const handleTouchEnd = useCallback((event: React.TouchEvent) => {
    const dx = event.changedTouches[0].clientX - touchStartX.current;
    const dy = Math.abs(event.changedTouches[0].clientY - touchStartY.current);
    if (dx < -SWIPE_THRESHOLD && Math.abs(dx) > dy) onCloseMobile?.();
  }, [onCloseMobile]);

  const renderItem = (item: NavItem) => (
    <button
      key={item.id}
      type="button"
      aria-label={item.label}
      aria-current={currentPage === item.id ? "page" : undefined}
      title={collapsed ? item.label : undefined}
      onClick={() => onNavigate(item.id)}
      className={cn(
        "flex h-9 w-full items-center gap-3 rounded-lg px-2.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
        collapsed && "justify-center px-0",
        currentPage === item.id
          ? "bg-sidebar-primary text-sidebar-primary-foreground"
          : "text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
      )}
    >
      <span className="flex size-4 shrink-0 items-center justify-center [&_svg]:size-4">{item.icon}</span>
      {!collapsed ? <span className="min-w-0 truncate">{item.label}</span> : null}
    </button>
  );

  return (
    <>
      {mobileOpen && onCloseMobile ? (
        <div
          className="fixed inset-0 z-30 bg-black/30 animate-fade-in md:hidden"
          onClick={onCloseMobile}
          aria-hidden="true"
        />
      ) : null}

      <aside
        data-collapsed={collapsed ? "true" : "false"}
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
        className={cn(
          "flex h-full w-[248px] shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width,transform] duration-200",
          collapsed && "w-[72px]",
          "max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-40 max-md:w-[min(86vw,320px)]",
          mobileOpen !== undefined && !mobileOpen ? "max-md:-translate-x-full" : "max-md:translate-x-0",
        )}
      >
        <div className={cn("flex h-14 shrink-0 items-center gap-2.5 border-b border-sidebar-border px-4", collapsed && "justify-center px-2")}>
          <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
            <Brain className="size-4" />
          </div>
          {!collapsed ? <span className="min-w-0 flex-1 truncate text-sm font-semibold">Memora</span> : null}
          {onCloseMobile ? (
            <Button aria-label="Close navigation" variant="ghost" size="icon" onClick={onCloseMobile} className="md:hidden">
              <X />
            </Button>
          ) : null}
          <Button
            aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
            title={collapsed ? "Expand navigation" : "Collapse navigation"}
            variant="ghost"
            size="icon-sm"
            onClick={() => setCollapsed((value) => !value)}
            className="max-md:hidden"
          >
            {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
          </Button>
        </div>

        <nav aria-label="Dashboard" className="min-h-0 flex-1 space-y-2 overflow-y-auto px-2 py-3">
          {groups.map((group) => {
            const open = openGroups[group.id];
            return (
              <div key={group.id} className="space-y-1">
                {!collapsed ? (
                  <button
                    type="button"
                    aria-expanded={open}
                    aria-controls={`nav-group-${group.id}`}
                    onClick={() => setOpenGroups((value) => ({ ...value, [group.id]: !value[group.id] }))}
                    className="flex h-7 w-full items-center justify-between rounded-md px-2 text-xs font-medium text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                  >
                    <span>{group.label}</span>
                    <ChevronDown className={cn("size-3.5 transition-transform", !open && "-rotate-90")} />
                  </button>
                ) : null}
                <div id={`nav-group-${group.id}`} className="space-y-1" hidden={!collapsed && !open}>
                  {group.items.map(renderItem)}
                </div>
              </div>
            );
          })}
        </nav>

        <div className="shrink-0 space-y-1 border-t border-sidebar-border p-2">
          {sseConnected !== undefined ? (
            <div className={cn("flex min-h-8 items-center gap-2 rounded-lg px-2 text-xs text-muted-foreground", collapsed && "justify-center px-0")}>
              <span className={cn("size-2 shrink-0 rounded-full", sseConnected ? "bg-emerald-500" : "bg-muted-foreground")} />
              {!collapsed ? <span className="truncate">{sseConnected ? t("status.realtime") : t("status.offline")}</span> : null}
              {unreadCount !== undefined && unreadCount > 0 ? (
                <button
                  type="button"
                  aria-label={t("status.markSeen")}
                  onClick={onMarkSeen}
                  className={cn("ml-auto rounded-md bg-primary px-1.5 py-0.5 text-[10px] font-semibold text-primary-foreground", collapsed && "absolute ml-5 -mt-5")}
                >
                  {unreadCount > 99 ? "99+" : unreadCount}
                </button>
              ) : null}
            </div>
          ) : null}
          {!collapsed && lastEvent && sseConnected ? (
            <p className="truncate px-2 pb-1 text-[10px] text-muted-foreground">
              {lastEvent.event}: {String(lastEvent.data?.content ?? lastEvent.data?.doc_id ?? "").slice(0, 30)}
            </p>
          ) : null}
          <button
            type="button"
            aria-label={t("header.theme")}
            title={collapsed ? t("header.theme") : undefined}
            onClick={onToggleTheme}
            className={cn("flex h-9 w-full items-center gap-3 rounded-lg px-2.5 text-sm text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground", collapsed && "justify-center px-0")}
          >
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            {!collapsed ? <span>{t("header.theme")}</span> : null}
          </button>
          <button
            type="button"
            aria-label={t("header.lang")}
            title={collapsed ? t("header.lang") : undefined}
            onClick={onCycleLanguage}
            className={cn("flex h-9 w-full items-center gap-3 rounded-lg px-2.5 text-sm text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground", collapsed && "justify-center px-0")}
          >
            <Languages className="size-4" />
            {!collapsed ? <><span className="flex-1 text-left">{t("header.lang")}</span><span className="font-mono text-xs">{langLabel}</span></> : null}
          </button>
        </div>
      </aside>
    </>
  );
}
