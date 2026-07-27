import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Brain, RotateCw, MessageSquareText, ArrowRightLeft } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { useGroups } from "@/hooks/useGroups";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { MetricGrid, PageContent, PageFrame, PageHeader, PageToolbar } from "@/components/layout/PageLayout";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/Progress";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from "@/components/ui/select";
import { DataTable } from "@/components/data-table/DataTable";
import type { DataTableColumn, DataTableSort } from "@/components/data-table/table-types";
import { dashboardLocale, formatDashboardDateTime, formatDashboardNumber, formatDashboardPercent, translateEnum } from "@/lib/i18n";
import { ActionConfirmDialog } from "@/components/editing/ActionConfirmDialog";

interface LearningPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

interface LearningStats {
  hit_rate?: number;
  avg_quality?: number;
  total_trials?: number;
  total_corrections?: number;
  parameters?: Record<string, number>;
  history?: Array<{ timestamp: string; action: string; detail: string }>;
}

interface ExpressionPatternRow {
  pattern_id: number;
  situation: string;
  expression: string;
  weight: number;
  usage_count: number;
  created_at: number;
  last_used_at: number;
  group_id: string;
}

const DEFAULT_EXPRESSION_SORT: DataTableSort = { id: "weight", desc: true };

export function LearningPage({ showToast }: LearningPageProps) {
  const { t, currentLang } = useI18n();
  const { groups, groupId, setGroupId } = useGroups();
  const [stats, setStats] = useState<LearningStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [expressionPatterns, setExpressionPatterns] = useState<ExpressionPatternRow[]>([]);
  const [expressionSort, setExpressionSort] = useState<DataTableSort>(DEFAULT_EXPRESSION_SORT);
  const [exprLoading, setExprLoading] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetPending, setResetPending] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);
  const resetPendingRef = useRef(false);
  const statsRequestRef = useRef(0);
  const expressionRequestRef = useRef(0);

  const fetchStats = useCallback(async () => {
    const requestId = ++statsRequestRef.current;
    setLoading(true);
    try {
      const res = unwrapApiData(await apiRequest("learning/status"));
      if (statsRequestRef.current === requestId) {
        setStats(res as LearningStats);
      }
    } catch (e) {
      if (statsRequestRef.current === requestId) {
        showToast(String(e), true);
      }
    } finally {
      if (statsRequestRef.current === requestId) {
        setLoading(false);
      }
    }
  }, [showToast]);

  const fetchExpressions = useCallback(async () => {
    const requestId = ++expressionRequestRef.current;
    if (!groupId) {
      setExpressionPatterns([]);
      setExprLoading(false);
      return;
    }
    setExprLoading(true);
    try {
      const query = new URLSearchParams({
        group_id: groupId,
        sort_by: expressionSort.id,
        sort_order: expressionSort.desc ? "desc" : "asc",
      });
      const res = unwrapApiData(await apiRequest(`expression/patterns?${query.toString()}`));
      if (expressionRequestRef.current === requestId) {
        setExpressionPatterns((res.patterns ?? []) as ExpressionPatternRow[]);
      }
    } catch {
      // Expression errors remain non-blocking for the learning overview.
    } finally {
      if (expressionRequestRef.current === requestId) {
        setExprLoading(false);
      }
    }
  }, [expressionSort, groupId]);

  useEffect(() => { fetchStats(); }, [fetchStats]);
  useEffect(() => { fetchExpressions(); }, [fetchExpressions]);

  const resetLearning = async () => {
    if (resetPendingRef.current) return;
    resetPendingRef.current = true;
    setResetPending(true);
    setResetError(null);
    try {
      unwrapApiData(await apiRequest("learning/reset", { method: "POST" }));
      showToast(t("learning.resetDone"));
      setResetOpen(false);
      await fetchStats();
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setResetError(message);
      showToast(message, true);
    } finally {
      resetPendingRef.current = false;
      setResetPending(false);
    }
  };

  const s = stats ?? {};
  const locale = dashboardLocale(currentLang());
  const changeExpressionSort = useCallback((next: DataTableSort | null) => {
    if (next?.id === "usage_count" && expressionSort.id !== "usage_count" && !next.desc) {
      setExpressionSort({ id: "usage_count", desc: true });
      return;
    }
    setExpressionSort(next ?? DEFAULT_EXPRESSION_SORT);
  }, [expressionSort.id]);
  const expressionColumns = useMemo<DataTableColumn<ExpressionPatternRow>[]>(() => [
    {
      id: "situation",
      accessorKey: "situation",
      header: t("expression.situation"),
      meta: {
        label: t("expression.situation"),
        serverSortKey: "situation",
        required: true,
        defaultPin: "left",
        cellClassName: "text-xs font-medium",
      },
    },
    {
      id: "expression",
      accessorKey: "expression",
      header: t("expression.expression"),
      meta: {
        label: t("expression.expression"),
        serverSortKey: "expression",
        cellClassName: "max-w-[20rem] text-xs text-muted-foreground",
      },
      cell: ({ row }) => <span className="block truncate"><ArrowRightLeft className="mr-1 inline" />{row.original.expression}</span>,
    },
    {
      id: "weight",
      accessorKey: "weight",
      header: t("expression.weight"),
      meta: { label: t("expression.weight"), serverSortKey: "weight" },
      cell: ({ row }) => <div className="flex items-center gap-2"><Progress aria-label={`${row.original.situation} ${row.original.expression} ${t("expression.weight")} ${row.original.pattern_id}`} value={row.original.weight} className="h-1.5 w-16" /><span className="text-xs tabular-nums text-muted-foreground">{formatDashboardPercent(row.original.weight, locale, { maximumFractionDigits: 0 })}</span></div>,
    },
    {
      id: "usage_count",
      accessorKey: "usage_count",
      header: t("expression.usage"),
      sortDescFirst: true,
      meta: { label: t("expression.usage"), serverSortKey: "usage_count", cellClassName: "text-right text-xs tabular-nums text-muted-foreground" },
    },
    {
      id: "created_at",
      accessorKey: "created_at",
      header: t("table.created"),
      meta: { label: t("table.created"), serverSortKey: "created_at" },
      cell: ({ row }) => row.original.created_at ? formatDashboardDateTime(row.original.created_at, locale) : "—",
    },
    {
      id: "last_used_at",
      accessorKey: "last_used_at",
      header: t("table.updated"),
      meta: { label: t("table.updated"), serverSortKey: "last_used_at" },
      cell: ({ row }) => row.original.last_used_at ? formatDashboardDateTime(row.original.last_used_at, locale) : "—",
    },
  ], [locale, t]);

  return (
    <PageFrame variant="standard" aria-label={t("nav.learning")}>
      <PageHeader
        title={t("nav.learning")}
        icon={<Brain />}
        actions={<Button variant="secondary" size="sm" onClick={() => { setResetError(null); setResetOpen(true); }} disabled={resetPending}><RotateCw data-icon="inline-start" />{resetPending ? `${t("learning.reset")}…` : t("learning.reset")}</Button>}
      />
      <ActionConfirmDialog
        open={resetOpen}
        title={t("learning.reset")}
        description={t("learning.resetConfirm")}
        cancelLabel={t("common.cancel")}
        actionLabel={t("learning.reset")}
        pendingLabel={`${t("learning.reset")}…`}
        destructive
        pending={resetPending}
        error={resetError}
        onCancel={() => setResetOpen(false)}
        onConfirm={resetLearning}
      />
      <PageContent className="flex flex-col gap-6">
        {loading && !stats && <p className="text-center text-sm text-muted-foreground">{t("common.loading")}</p>}

        <MetricGrid minItemWidth="10rem">
          {[{ label: t("learning.hitRate"), value: s.hit_rate, fmt: (v: number) => formatDashboardPercent(v, locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) },
            { label: t("learning.avgQuality"), value: s.avg_quality, fmt: (v: number) => formatDashboardNumber(v, locale, { minimumFractionDigits: 3, maximumFractionDigits: 3 }) },
            { label: t("learning.trials"), value: s.total_trials, fmt: (v: number) => String(v) },
            { label: t("learning.corrections"), value: s.total_corrections, fmt: (v: number) => String(v) }].map((item) => (
            <Card key={item.label} size="sm">
              <CardContent>
              <div className="text-2xl font-bold tabular-nums text-foreground">
                {item.value !== undefined && item.value !== null ? item.fmt(item.value) : "--"}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">{item.label}</div>
              </CardContent>
            </Card>
          ))}
        </MetricGrid>

        <div data-slot="learning-details" className="grid gap-6 xl:grid-cols-2">
          {s.parameters && Object.keys(s.parameters).length > 0 && (
          <Card>
            <CardHeader><CardTitle><h2>{t("learning.params")}</h2></CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-3">
              {Object.entries(s.parameters).map(([key, value]) => (
                <div key={key} className="grid min-w-0 grid-cols-[minmax(6rem,10rem)_minmax(4rem,1fr)_3.5rem] items-center gap-3">
                  <span className="truncate text-xs text-muted-foreground">{key}</span>
                  <Progress aria-label={key} value={Number(value)} className="h-2" />
                  <span className="text-right text-xs tabular-nums text-muted-foreground">{formatDashboardNumber(value, locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                </div>
              ))}
            </CardContent>
          </Card>
          )}

          {s.history && s.history.length > 0 && (
          <Card>
            <CardHeader><CardTitle><h2>{t("learning.history")}</h2></CardTitle></CardHeader>
            <CardContent className="flex max-h-80 flex-col gap-1 overflow-auto">
              {s.history.map((h, i) => (
                <div key={i} className="flex min-w-0 items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-muted/50">
                  <span className="w-36 shrink-0 text-xs text-muted-foreground">{formatDashboardDateTime(h.timestamp, locale)}</span>
                  <Badge variant="secondary">{translateEnum(t, "learning.historyAction", h.action, h.action)}</Badge>
                  <span className="truncate text-xs text-foreground">{h.detail}</span>
                </div>
              ))}
            </CardContent>
          </Card>
          )}
        </div>

        <Card className="gap-0 py-0">
          <PageToolbar className="justify-between rounded-t-lg border-b bg-muted/30">
            <div className="flex items-center gap-2">
              <MessageSquareText />
              <span className="text-sm font-semibold text-foreground">{t("expression.title")}</span>
              <span className="text-xs text-muted-foreground">({expressionPatterns.length} {t("expression.patterns").toLowerCase()})</span>
            </div>
            <Select value={groupId} onValueChange={(v) => { if (v) { setExpressionSort(DEFAULT_EXPRESSION_SORT); setGroupId(v); } }} disabled={groups.length === 0}>
              <SelectTrigger size="sm" className="w-36 text-xs"><span>{groupId || t("jargon.allGroups")}</span></SelectTrigger>
              <SelectContent>
                <SelectGroup>
                {groups.length > 0 ? groups.map((g) => (
                  <SelectItem key={g.group_id} value={g.group_id}>{g.group_id}</SelectItem>
                )) : (
                  <SelectItem value="loading">—</SelectItem>
                )}
                </SelectGroup>
              </SelectContent>
            </Select>
          </PageToolbar>
          <DataTable
            tableId="expression-patterns"
            data={expressionPatterns}
            columns={expressionColumns}
            getRowId={(pattern) => String(pattern.pattern_id)}
            sort={expressionSort}
            onSortChange={changeExpressionSort}
            loading={exprLoading}
            emptyLabel={t("expression.noData")}
          />
        </Card>
      </PageContent>
    </PageFrame>
  );
}
