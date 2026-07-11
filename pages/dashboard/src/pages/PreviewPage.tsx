import { useState, useEffect, useCallback } from "react";
import {
  LayoutDashboard, Database, GitGraph, ScrollText,
  BookOpen, StickyNote, UserRound, Brain, RefreshCw
} from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent } from "@/components/ui/card";
import { PageContent, PageFrame, PageHeader, MetricGrid } from "@/components/layout/PageLayout";
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
    <MetricGrid>
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="h-28 rounded-lg border bg-muted skeleton-pulse" />
      ))}
    </MetricGrid>
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
    <PageFrame variant="standard">
      <PageHeader
        title={t("nav.preview")}
        icon={<LayoutDashboard size={18} />}
        actions={<Button variant="secondary" size="sm" onClick={fetchAll} disabled={loading}>
          <RefreshCw size={14} className={cn(loading && "animate-spin")} />
          {t("common.refresh")}
        </Button>}
      />

      <PageContent className="page-enter">
        {loading && !stats ? loadingSkeleton : (
          <>
            <MetricGrid className="stagger-children">
              {statCards.map((card, i) => (
                <Card
                  key={i}
                  className="card-hover"
                >
                  <CardContent>
                    <div className="flex items-center gap-3">
                      <div className="flex size-10 items-center justify-center rounded-lg bg-muted text-primary">
                        {card.icon}
                      </div>
                    </div>
                    <div className="mt-3">
                    <div className="text-2xl font-bold tabular-nums text-foreground">
                      {card.value ?? "--"}
                    </div>
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      {card.label}
                    </div>
                  </div>
                  </CardContent>
                </Card>
              ))}
            </MetricGrid>

            <div className="mt-8">
              <h2 className="mb-4 text-sm font-semibold text-foreground">
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
                    className="flex items-center gap-2.5 rounded-lg border px-4 py-3 text-sm font-medium text-muted-foreground transition-colors hover:border-primary/30 hover:bg-primary/5 hover:text-primary"
                  >
                    {link.icon}
                    {link.label}
                  </a>
                ))}
              </div>
            </div>

            {stats && (
              <Card className="mt-8">
                <CardContent>
                <h2 className="mb-3 text-sm font-semibold text-foreground">
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
                      <div className="text-lg font-bold tabular-nums text-foreground">
                        {item.value ?? "--"}
                      </div>
                      <div className="text-2xs text-muted-foreground">{item.label}</div>
                    </div>
                  ))}
                </div>
                </CardContent>
              </Card>
            )}
          </>
        )}
      </PageContent>
    </PageFrame>
  );
}
