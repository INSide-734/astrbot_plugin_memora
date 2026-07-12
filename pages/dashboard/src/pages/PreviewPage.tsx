import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  BookOpen,
  Brain,
  ChevronDown,
  Database,
  GitGraph,
  LayoutDashboard,
  RefreshCw,
  ScrollText,
  StickyNote,
  UserRound,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { PageContent, PageFrame, PageHeader } from "@/components/layout/PageLayout";
import {
  GrowthTrendChart,
  ImportanceDistribution,
  RankedBars,
  StatusComposition,
  type DailyMemoryCount,
  type NamedCount,
} from "@/components/preview/OverviewCharts";
import { Button, buttonVariants } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatePanel } from "@/components/ui/StatePanel";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { dashboardLocale, formatDashboardNumber, formatDashboardPercent, translateEnum, type Translate } from "@/lib/i18n";
import { cn } from "@/lib/utils";

interface PreviewPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

interface RecentSession {
  session_id: string;
  message_count: number;
}

interface PreviewStats {
  total_memories: number;
  active_count: number;
  archived_count: number;
  deleted_count: number;
  graph_nodes: number;
  graph_edges: number;
  graph_entries: number;
  atom_count: number;
  avg_importance: number;
  status_breakdown: Record<string, number>;
  atom_breakdown: Record<string, number>;
  importance_distribution: Record<string, number>;
  recent_sessions: RecentSession[];
  daily_memory_counts: DailyMemoryCount[];
}

interface ModuleCounts {
  profiles: number | null;
  knowledge: number | null;
  notes: number | null;
}

type TrendRange = 7 | 30 | 90;

const EMPTY_MODULES: ModuleCounts = { profiles: null, knowledge: null, notes: null };

function numberValue(value: unknown) {
  const result = Number(value);
  return Number.isFinite(result) ? result : 0;
}

function normalizeStats(raw: Record<string, unknown>): PreviewStats {
  const status = (raw.status_breakdown && typeof raw.status_breakdown === "object" ? raw.status_breakdown : {}) as Record<string, unknown>;
  const atoms = (raw.atom_breakdown && typeof raw.atom_breakdown === "object" ? raw.atom_breakdown : {}) as Record<string, unknown>;
  const importance = (raw.importance_distribution && typeof raw.importance_distribution === "object" ? raw.importance_distribution : {}) as Record<string, unknown>;
  const daily = Array.isArray(raw.daily_memory_counts) ? raw.daily_memory_counts : [];
  const sessions = Array.isArray(raw.recent_sessions) ? raw.recent_sessions : [];

  return {
    total_memories: numberValue(raw.total_memories),
    active_count: numberValue(raw.active_count ?? status.active),
    archived_count: numberValue(raw.archived_count ?? status.archived),
    deleted_count: numberValue(raw.deleted_count ?? status.deleted),
    graph_nodes: numberValue(raw.graph_nodes),
    graph_edges: numberValue(raw.graph_edges),
    graph_entries: numberValue(raw.graph_entries),
    atom_count: numberValue(raw.atom_count),
    avg_importance: numberValue(raw.avg_importance),
    status_breakdown: Object.fromEntries(Object.entries(status).map(([key, value]) => [key, numberValue(value)])),
    atom_breakdown: Object.fromEntries(Object.entries(atoms).map(([key, value]) => [key, numberValue(value)])),
    importance_distribution: Object.fromEntries(Object.entries(importance).map(([key, value]) => [key, numberValue(value)])),
    recent_sessions: sessions
      .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
      .map((item) => ({ session_id: String(item.session_id ?? ""), message_count: numberValue(item.message_count) }))
      .filter((item) => item.session_id)
      .slice(0, 5),
    daily_memory_counts: daily
      .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
      .map((item) => ({ date: String(item.date ?? ""), count: numberValue(item.count) }))
      .filter((item) => item.date)
      .slice(-90),
  };
}

function topAtomTypes(values: Record<string, number>, otherLabel: string, t: Translate): NamedCount[] {
  const sorted = Object.entries(values)
    .map(([name, count]) => ({ name: translateEnum(t, "memory.type", name), count }))
    .filter((item) => item.count > 0)
    .sort((left, right) => right.count - left.count);
  const head = sorted.slice(0, 5);
  const remaining = sorted.slice(5).reduce((sum, item) => sum + item.count, 0);
  return remaining > 0 ? [...head, { name: otherLabel, count: remaining }] : head;
}

function formatUtcDate(value: string, locale: string): string {
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(locale, { timeZone: "UTC" });
}

function OverviewSkeleton({ ariaLabel }: { ariaLabel: string }) {
  return (
    <div className="space-y-4" aria-label={ariaLabel}>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6">
        {Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-28" />)}
      </div>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(20rem,1fr)]">
        <Skeleton className="h-[24rem]" />
        <Skeleton className="h-[24rem]" />
      </div>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(20rem,2fr)]">
        <Skeleton className="h-64" />
        <Skeleton className="h-64" />
      </div>
    </div>
  );
}

export function PreviewPage({ showToast }: PreviewPageProps) {
  const { t, currentLang } = useI18n();
  const [stats, setStats] = useState<PreviewStats | null>(null);
  const [modules, setModules] = useState<ModuleCounts>(EMPTY_MODULES);
  const [loading, setLoading] = useState(false);
  const [statsError, setStatsError] = useState(false);
  const [partialFailure, setPartialFailure] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [clock, setClock] = useState(Date.now());
  const [range, setRange] = useState<TrendRange>(30);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    const [statsResult, profilesResult, knowledgeResult, notesResult] = await Promise.allSettled([
      apiRequest("stats"),
      apiRequest("profiles?limit=1"),
      apiRequest("knowledge?limit=1"),
      apiRequest("notes?limit=1"),
    ]);

    let nextStatsSucceeded = false;
    if (statsResult.status === "fulfilled") {
      try {
        setStats(normalizeStats(unwrapApiData(statsResult.value) as Record<string, unknown>));
        setStatsError(false);
        nextStatsSucceeded = true;
      } catch (error) {
        setStatsError(true);
        showToast(String(error), true);
      }
    } else {
      setStatsError(true);
      showToast(String(statsResult.reason), true);
    }

    const secondaryResults = [profilesResult, knowledgeResult, notesResult] as const;
    const secondaryKeys: Array<keyof ModuleCounts> = ["profiles", "knowledge", "notes"];
    const parsedSecondary = secondaryResults.map((result) => {
      if (result.status !== "fulfilled") return { succeeded: false, count: null };
      try {
        const data = unwrapApiData(result.value) as Record<string, unknown>;
        return { succeeded: true, count: numberValue(data.total ?? data.count) };
      } catch {
        return { succeeded: false, count: null };
      }
    });
    setModules((previous) => {
      const next = { ...previous };
      parsedSecondary.forEach((result, index) => {
        if (!result.succeeded) {
          if (!stats) next[secondaryKeys[index]] = null;
          return;
        }
        next[secondaryKeys[index]] = result.count;
      });
      return next;
    });
    setPartialFailure(parsedSecondary.some((result) => !result.succeeded));
    if (nextStatsSucceeded) {
      const updatedAt = new Date();
      setLastUpdated(updatedAt);
      setClock(updatedAt.getTime());
    }
    setLoading(false);
  }, [showToast, stats]);

  useEffect(() => {
    void fetchAll();
  }, []); // Fetch once on mount; range changes are local.

  useEffect(() => {
    if (!lastUpdated) return undefined;
    const timer = window.setInterval(() => setClock(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, [lastUpdated]);

  const trendData = useMemo(() => stats?.daily_memory_counts.slice(-range) ?? [], [range, stats]);
  const trendTotal = trendData.reduce((sum, item) => sum + item.count, 0);
  const trendAverage = trendData.length > 0 ? trendTotal / trendData.length : 0;
  const peak = trendData.reduce<DailyMemoryCount | null>((best, item) => (!best || item.count > best.count ? item : best), null);
  const activeRate = stats && stats.total_memories > 0 ? stats.active_count / stats.total_memories : 0;
  const importanceScore = Math.max(0, Math.min(10, (stats?.avg_importance ?? 0) * 10));
  const knowledgeTotal = modules.profiles == null || modules.knowledge == null || modules.notes == null
    ? null
    : modules.profiles + modules.knowledge + modules.notes;
  const locale = dashboardLocale(currentLang());
  const formatNumber = (value: number): string => value.toLocaleString(locale);
  const atomTypes = topAtomTypes(stats?.atom_breakdown ?? {}, t("preview.other"), t);
  const importanceItems = Array.from({ length: 10 }, (_, index) => ({
    name: `${index}-${index + 1}`,
    count: stats?.importance_distribution[`${index}-${index + 1}`] ?? 0,
  }));
  const quickLinks: Array<[string, string, LucideIcon]> = [
    ["graph", t("nav.graph"), GitGraph],
    ["memory", t("nav.memory"), ScrollText],
    ["recall", t("nav.recall"), Brain],
    ["system", t("nav.system"), Database],
  ];
  const moduleAssets: Array<[string, number | null, LucideIcon]> = [
    [t("nav.profiles"), modules.profiles, UserRound],
    [t("nav.knowledge"), modules.knowledge, BookOpen],
    [t("nav.notes"), modules.notes, StickyNote],
    [t("preview.memoryAtoms"), stats?.atom_count ?? 0, Brain],
    [t("preview.graphEntries"), stats?.graph_entries ?? 0, GitGraph],
  ];
  const updatedMinutes = lastUpdated ? Math.max(0, Math.floor((clock - lastUpdated.getTime()) / 60_000)) : 0;
  const updatedText = updatedMinutes < 1
    ? t("preview.justNow")
    : t("preview.minutesAgo").replace("{0}", String(updatedMinutes));

  const kpis = stats ? [
    { label: t("preview.totalMemories"), value: formatNumber(stats.total_memories), detail: t("preview.allStoredMemories"), icon: ScrollText },
    { label: t("preview.rangeAdded"), value: formatNumber(trendTotal), detail: t("preview.rangeDays").replace("{0}", String(range)), icon: Activity },
    { label: t("preview.activeRate"), value: formatDashboardPercent(activeRate, locale, { maximumFractionDigits: 0 }), detail: `${formatNumber(stats.active_count)} / ${formatNumber(stats.total_memories)}`, icon: Database },
    { label: t("preview.averageImportance"), value: formatDashboardNumber(importanceScore, locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 }), detail: `${formatDashboardNumber(importanceScore, locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} / 10`, icon: Brain },
    { label: t("preview.graphScale"), value: formatNumber(stats.graph_nodes), detail: `${formatNumber(stats.graph_edges)} ${t("preview.edges")}`, icon: GitGraph },
    { label: t("preview.knowledgeAssets"), value: knowledgeTotal == null ? "--" : formatNumber(knowledgeTotal), detail: knowledgeTotal == null ? t("preview.unavailable") : `${formatNumber(modules.profiles ?? 0)} / ${formatNumber(modules.knowledge ?? 0)} / ${formatNumber(modules.notes ?? 0)}`, icon: BookOpen },
  ] : [];

  return (
    <PageFrame variant="standard" aria-label={t("nav.preview")}>
      <PageHeader
        title={t("nav.preview")}
        description={lastUpdated ? `${t("preview.updated")} ${updatedText}` : t("preview.operationalSummary")}
        icon={<LayoutDashboard className="size-5" />}
        actions={<>
          <DropdownMenu>
            <DropdownMenuTrigger className={buttonVariants({ variant: "outline", size: "sm" })}>
              {t("preview.quickLinks")}<ChevronDown />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-44">
              {quickLinks.map(([page, label, Icon]) => (
                <DropdownMenuItem key={String(page)} render={<a href={`#/${page}`} />}>
                  <Icon className="size-4" />{label}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          <Button variant="secondary" size="sm" onClick={() => void fetchAll()} disabled={loading}>
            <RefreshCw className={cn("size-4", loading && "animate-spin")} />
            {t("common.refresh")}
          </Button>
        </>}
      />

      <PageContent className="page-enter">
        {!stats && loading ? <OverviewSkeleton ariaLabel={t("preview.loadingOverview")} /> : null}
        {!stats && !loading && statsError ? (
          <StatePanel
            state="error"
            title={t("preview.unavailableTitle")}
            description={t("preview.unavailableDescription")}
            actionLabel={t("common.retry")}
            onAction={() => void fetchAll()}
          />
        ) : null}

        {stats ? (
          <div className="space-y-4">
            {partialFailure ? (
              <div role="status" className="rounded-lg border border-border bg-muted px-3 py-2 text-sm text-foreground">
                {t("preview.partialFailure")}
              </div>
            ) : null}

            <div data-slot="preview-metrics" className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6">
              {kpis.map(({ label, value, detail, icon: Icon }) => (
                <Card key={label} data-slot="overview-kpi" size="sm" className="min-h-28">
                  <CardContent className="flex h-full flex-col justify-between gap-3">
                    <div className="flex items-center justify-between gap-2 text-muted-foreground">
                      <span className="text-xs font-medium">{label}</span>
                      <Icon className="size-4 shrink-0" aria-hidden="true" />
                    </div>
                    <div>
                      <div data-kpi-value className="text-2xl font-semibold tabular-nums text-foreground">{value}</div>
                      <div className="mt-0.5 truncate text-xs text-muted-foreground" title={detail}>{detail}</div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>

            <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(20rem,1fr)]">
              <Card data-slot="growth-panel" className="min-w-0">
                <CardHeader className="flex-row items-start justify-between gap-4">
                  <div><CardTitle><h2>{t("preview.memoryGrowth")}</h2></CardTitle><p className="mt-1 text-xs text-muted-foreground">{t("preview.growthDescription")}</p></div>
                  <div role="group" aria-label={t("preview.trendRange")} className="flex shrink-0 rounded-lg bg-muted p-0.5">
                    {([7, 30, 90] as TrendRange[]).map((days) => (
                      <Button key={days} size="sm" variant={range === days ? "default" : "ghost"} aria-pressed={range === days} onClick={() => setRange(days)} className="h-7 px-2.5">
                        {t("preview.days").replace("{0}", String(days))}
                      </Button>
                    ))}
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="mb-3 grid grid-cols-3 gap-3 border-b pb-3">
                    <div><div className="text-xs text-muted-foreground">{t("preview.periodTotal")}</div><div className="mt-0.5 font-semibold tabular-nums">{formatNumber(trendTotal)}</div></div>
                    <div><div className="text-xs text-muted-foreground">{t("preview.dailyAverage")}</div><div className="mt-0.5 font-semibold tabular-nums">{formatDashboardNumber(trendAverage, locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</div></div>
                    <div><div className="text-xs text-muted-foreground">{t("preview.peakDate")}</div><div className="mt-0.5 truncate font-semibold tabular-nums">{peak && peak.count > 0 ? formatUtcDate(peak.date, locale) : "--"}</div></div>
                  </div>
                  <GrowthTrendChart
                    data={trendData}
                    ariaLabel={t("preview.growthChartLabel")}
                    valueLabel={t("preview.memories")}
                    locale={locale}
                  />
                  {trendTotal === 0 ? <p className="text-center text-xs text-muted-foreground">{t("preview.noGrowth")}</p> : null}
                </CardContent>
              </Card>

              <Card data-slot="composition-panel" className="min-w-0">
                <CardHeader><CardTitle><h2>{t("preview.composition")}</h2></CardTitle></CardHeader>
                <CardContent className="space-y-5">
                  <section className="space-y-2"><h3 className="text-xs font-medium text-muted-foreground">{t("preview.statusComposition")}</h3><StatusComposition ariaLabel={t("preview.statusChartLabel")} locale={locale} items={[
                    { name: t("preview.active"), count: stats.active_count, colorClass: "bg-primary" },
                    { name: t("preview.archived"), count: stats.archived_count, colorClass: "bg-accent-success" },
                    { name: t("preview.deleted"), count: stats.deleted_count, colorClass: "bg-accent-danger" },
                  ]} /></section>
                  <section className="space-y-2 border-t pt-4"><h3 className="text-xs font-medium text-muted-foreground">{t("preview.atomTypes")}</h3>{atomTypes.length > 0 ? <RankedBars items={atomTypes} ariaLabel={t("preview.atomChartLabel")} locale={locale} /> : <p className="text-xs text-muted-foreground">{t("preview.noAtomData")}</p>}</section>
                  <section className="space-y-2 border-t pt-4"><h3 className="text-xs font-medium text-muted-foreground">{t("preview.importanceDistribution")}</h3><ImportanceDistribution items={importanceItems} ariaLabel={t("preview.importanceChartLabel")} valueLabel={t("preview.memories")} /></section>
                </CardContent>
              </Card>
            </div>

            <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(20rem,2fr)]">
              <Card data-slot="module-assets-panel">
                <CardHeader><CardTitle><h2>{t("preview.moduleAssets")}</h2></CardTitle></CardHeader>
                <CardContent className="grid gap-2 sm:grid-cols-2">
                  {moduleAssets.map(([label, value, Icon]) => (
                    <div key={String(label)} className="flex min-w-0 items-center justify-between gap-3 rounded-md border px-3 py-2.5">
                      <div className="flex min-w-0 items-center gap-2"><Icon className="size-4 shrink-0 text-muted-foreground" /><span className="truncate text-sm">{label}</span></div>
                      <div className="text-right"><span className="font-semibold tabular-nums">{value == null ? "--" : formatNumber(Number(value))}</span>{value == null ? <div className="text-2xs text-muted-foreground">{t("preview.unavailable")}</div> : null}</div>
                    </div>
                  ))}
                </CardContent>
              </Card>

              <Card data-slot="active-sessions-panel">
                <CardHeader><CardTitle><h2>{t("preview.activeSessions")}</h2></CardTitle></CardHeader>
                <CardContent>
                  {stats.recent_sessions.length > 0 ? (
                    <RankedBars items={stats.recent_sessions.map((item) => ({ name: item.session_id, count: item.message_count }))} ariaLabel={t("preview.sessionsChartLabel")} locale={locale} />
                  ) : (
                    <StatePanel state="empty" title={t("preview.noSessions")} className="min-h-36 p-4" />
                  )}
                </CardContent>
              </Card>
            </div>
          </div>
        ) : null}
      </PageContent>
    </PageFrame>
  );
}
