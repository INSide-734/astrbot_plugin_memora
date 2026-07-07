import { useState, useEffect, useCallback } from "react";
import {
  LayoutDashboard, Database, GitGraph, ScrollText,
  BookOpen, StickyNote, UserRound, Brain, RefreshCw
} from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

interface PreviewPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

interface PreviewStats {
  memory?: { total: number; active: number; archived: number };
  graph?: { nodes: number; edges: number };
  profiles?: { count: number };
  knowledge?: { count: number };
  notes?: { count: number; active: number };
}

export function PreviewPage({ showToast }: PreviewPageProps) {
  const { t } = useI18n();
  const [stats, setStats] = useState<PreviewStats | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [statsRes, profilesRes, knowledgeRes, notesRes] = await Promise.allSettled([
        apiRequest("stats"),
        apiRequest("profiles?limit=1"),
        apiRequest("knowledge?limit=1"),
        apiRequest("notes?limit=1"),
      ]);

      const s = statsRes.status === "fulfilled" ? unwrapApiData(statsRes.value) : {};
      const p = profilesRes.status === "fulfilled" ? unwrapApiData(profilesRes.value) : {};
      const k = knowledgeRes.status === "fulfilled" ? unwrapApiData(knowledgeRes.value) : {};
      const n = notesRes.status === "fulfilled" ? unwrapApiData(notesRes.value) : {};

      setStats({
        memory: {
          total: Number(s.total_memories ?? 0),
          active: Number(s.active_count ?? 0),
          archived: Number(s.archived_count ?? 0),
        },
        graph: {
          nodes: Number(s.graph_nodes ?? 0),
          edges: Number(s.graph_edges ?? 0),
        },
        profiles: { count: Number(p.total ?? p.count ?? 0) },
        knowledge: { count: Number(k.total ?? k.count ?? 0) },
        notes: {
          count: Number(n.total ?? n.count ?? 0),
          active: Number(n.active_count ?? 0),
        },
      });
    } catch (e) {
      showToast(String(e), true);
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const loadingSkeleton = (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="h-28 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] skeleton-pulse" />
      ))}
    </div>
  );

  const statCards = stats ? [
    { icon: <ScrollText size={20} />, label: t("stats.total"), value: stats.memory?.total, color: "var(--color-accent)" },
    { icon: <GitGraph size={20} />, label: t("graph.nodes"), value: stats.graph?.nodes, color: "#7950f2" },
    { icon: <Database size={20} />, label: t("preview.activeMemories"), value: stats.memory?.active, color: "var(--color-success)" },
    { icon: <UserRound size={20} />, label: t("nav.profiles"), value: stats.profiles?.count, color: "#20c997" },
    { icon: <BookOpen size={20} />, label: t("nav.knowledge"), value: stats.knowledge?.count, color: "#fcc419" },
    { icon: <StickyNote size={20} />, label: t("nav.notes"), value: stats.notes?.count, color: "#f06595" },
    { icon: <GitGraph size={20} />, label: t("graph.edges"), value: stats.graph?.edges, color: "#909296" },
    { icon: <Brain size={20} />, label: t("nav.learning"), value: stats.memory?.archived, color: "var(--color-warning)" },
  ] : [];

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-3">
        <h1 className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
          <LayoutDashboard size={18} />
          {t("nav.preview")}
        </h1>
        <Button variant="secondary" size="sm" onClick={fetchAll} disabled={loading}>
          <RefreshCw size={14} className={cn(loading && "animate-spin")} />
          {t("common.refresh")}
        </Button>
      </header>

      <div className="flex-1 overflow-auto p-6 page-enter">
        {loading && !stats ? loadingSkeleton : (
          <>
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4 stagger-children">
              {statCards.map((card, i) => (
                <div
                  key={i}
                  className="card-hover rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5"
                >
                  <div className="flex items-center gap-3">
                    <div
                      className="flex h-10 w-10 items-center justify-center rounded-xl"
                      style={{ background: `${card.color}18`, color: card.color }}
                    >
                      {card.icon}
                    </div>
                  </div>
                  <div className="mt-3">
                    <div className="text-2xl font-bold tabular-nums text-[var(--text-primary)]">
                      {card.value ?? "--"}
                    </div>
                    <div className="text-xs text-[var(--text-tertiary)] mt-0.5">
                      {card.label}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-8">
              <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-4">
                {t("preview.quickActions")}
              </h2>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                {[
                  { page: "graph", label: t("nav.graph"), icon: <GitGraph size={16} /> },
                  { page: "memory", label: t("nav.memory"), icon: <ScrollText size={16} /> },
                  { page: "recall", label: t("nav.recall"), icon: <Brain size={16} /> },
                  { page: "system", label: t("nav.system"), icon: <Database size={16} /> },
                ].map((link) => (
                  <a
                    key={link.page}
                    href={`#/${link.page}`}
                    className="flex items-center gap-2.5 rounded-xl border border-[var(--color-border)] px-4 py-3 text-sm font-medium text-[var(--text-secondary)] transition-all duration-150 hover:border-[var(--color-accent)]/30 hover:bg-[var(--color-accent)]/5 hover:text-[var(--color-accent)]"
                  >
                    {link.icon}
                    {link.label}
                  </a>
                ))}
              </div>
            </div>

            {stats && (
              <div className="mt-8 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
                <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-3">
                  {t("preview.storageSummary")}
                </h2>
                <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
                  {[
                    { label: t("preview.memoryTotal"), value: stats.memory?.total },
                    { label: t("preview.active"), value: stats.memory?.active },
                    { label: t("preview.archived"), value: stats.memory?.archived },
                    { label: t("preview.graphNodes"), value: stats.graph?.nodes },
                    { label: t("preview.graphEdges"), value: stats.graph?.edges },
                  ].map((item, i) => (
                    <div key={i} className="text-center">
                      <div className="text-lg font-bold tabular-nums text-[var(--text-primary)]">
                        {item.value ?? "--"}
                      </div>
                      <div className="text-2xs text-[var(--text-tertiary)]">{item.label}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
