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
import { StatePanel } from "@/components/ui/StatePanel";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from "@/components/ui/select";
import { DataTable } from "@/components/data-table/DataTable";
import type { DataTableColumn, DataTableSort } from "@/components/data-table/table-types";
import { dashboardLocale, formatDashboardDateTime, formatDashboardNumber, formatDashboardPercent, translateEnum } from "@/lib/i18n";
import { ActionConfirmDialog } from "@/components/editing/ActionConfirmDialog";

interface LearningPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

interface LearningWeights {
  document_route_weight: number;
  graph_route_weight: number;
}

interface LearningCandidate {
  proposed_document_weight: number | null;
  proposed_graph_weight: number | null;
  delta_from_baseline: number | null;
  accepted_count: number | null;
  independent_window_count: number | null;
  decayed_support: number | null;
  status: "ready_for_review" | "rejected" | "published" | "invalid_state";
  reason_code: "candidate" | "insufficient_evidence" | "published" | "invalid_state";
}

interface LearningStats {
  enabled: boolean;
  available: boolean;
  candidate_count: number;
  ready_count: number;
  rejected_count: number;
  published_count: number;
  reasons: string[];
  current: LearningWeights;
  baseline: LearningWeights;
  candidates: LearningCandidate[];
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

/** 展示只读 shadow 学习候选、真实运行时权重与表达模式。 */
export function LearningPage({ showToast }: LearningPageProps) {
  const { t, currentLang } = useI18n();
  const { groups, groupId, setGroupId } = useGroups();
  const [stats, setStats] = useState<LearningStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [statsError, setStatsError] = useState<string | null>(null);
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
    setStatsError(null);
    try {
      const res = unwrapApiData(await apiRequest("learning/status"));
      if (statsRequestRef.current === requestId) {
        setStats(res as unknown as LearningStats);
      }
    } catch (e) {
      if (statsRequestRef.current === requestId) {
        setStatsError(e instanceof Error ? e.message : String(e));
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
      // 表达模式读取失败不阻断自主学习概览。
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
        {loading && !stats ? (
          <StatePanel state="loading" title={t("learning.loading")} />
        ) : null}
        {statsError && !stats ? (
          <StatePanel
            state="error"
            title={t("learning.loadError")}
            description={statsError}
            actionLabel={t("common.retry")}
            onAction={fetchStats}
          />
        ) : null}

        {stats ? (
          <>
            {statsError ? <p role="alert" className="text-sm text-destructive">{statsError}</p> : null}
            <MetricGrid minItemWidth="10rem">
              {[
                { label: t("learning.candidates"), value: stats.candidate_count },
                { label: t("learning.ready"), value: stats.ready_count },
                { label: t("learning.rejected"), value: stats.rejected_count },
                { label: t("learning.published"), value: stats.published_count },
              ].map((item) => (
                <Card key={item.label} size="sm">
                  <CardContent>
                    <div className="text-2xl font-bold tabular-nums text-foreground">
                      {formatDashboardNumber(item.value, locale)}
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">{item.label}</div>
                  </CardContent>
                </Card>
              ))}
            </MetricGrid>

            <div data-slot="learning-details" className="grid gap-6 xl:grid-cols-2">
              {[
                { title: t("learning.current"), weights: stats.current },
                { title: t("learning.baseline"), weights: stats.baseline },
              ].map(({ title, weights }) => (
                <Card key={title}>
                  <CardHeader><CardTitle><h2>{title}</h2></CardTitle></CardHeader>
                  <CardContent className="flex flex-col gap-4">
                    {[
                      [t("learning.documentRoute"), weights.document_route_weight],
                      [t("learning.graphRoute"), weights.graph_route_weight],
                    ].map(([label, value]) => (
                      <div key={String(label)} className="grid min-w-0 grid-cols-[minmax(7rem,10rem)_minmax(4rem,1fr)_4rem] items-center gap-3">
                        <span className="truncate text-xs text-muted-foreground">{label}</span>
                        <Progress aria-label={`${title} ${label}`} value={Number(value)} className="h-2" />
                        <span className="text-right text-xs tabular-nums text-muted-foreground">
                          {formatDashboardPercent(value, locale, { maximumFractionDigits: 1 })}
                        </span>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              ))}
            </div>

            <Card>
              <CardHeader className="flex-row items-center justify-between gap-3">
                <CardTitle><h2>{t("learning.shadowCandidates")}</h2></CardTitle>
                <Badge variant={stats.enabled ? "secondary" : "outline"}>
                  {stats.enabled ? t("learning.enabled") : t("learning.disabled")}
                </Badge>
              </CardHeader>
              <CardContent>
                {stats.candidates.length === 0 ? (
                  <StatePanel
                    state="empty"
                    title={t("learning.noCandidates")}
                    description={t("learning.noCandidatesDescription")}
                    className="min-h-40"
                  />
                ) : (
                  <div className="grid gap-3 xl:grid-cols-2">
                    {stats.candidates.map((candidate, index) => (
                      <div key={`${candidate.status}-${candidate.reason_code}-${index}`} className="min-w-0 rounded-lg border p-4">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <Badge variant={candidate.status === "ready_for_review" ? "default" : "secondary"}>
                            {translateEnum(t, "learning.status", candidate.status, candidate.status)}
                          </Badge>
                          <span className="text-xs text-muted-foreground">
                            {translateEnum(t, "learning.reason", candidate.reason_code, candidate.reason_code)}
                          </span>
                        </div>
                        <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                          <div><dt className="text-xs text-muted-foreground">{t("learning.documentRoute")}</dt><dd className="mt-1 tabular-nums">{formatDashboardPercent(candidate.proposed_document_weight, locale, { maximumFractionDigits: 1 })}</dd></div>
                          <div><dt className="text-xs text-muted-foreground">{t("learning.graphRoute")}</dt><dd className="mt-1 tabular-nums">{formatDashboardPercent(candidate.proposed_graph_weight, locale, { maximumFractionDigits: 1 })}</dd></div>
                          <div><dt className="text-xs text-muted-foreground">{t("learning.delta")}</dt><dd className="mt-1 tabular-nums">{formatDashboardPercent(candidate.delta_from_baseline, locale, { maximumFractionDigits: 1 })}</dd></div>
                          <div><dt className="text-xs text-muted-foreground">{t("learning.support")}</dt><dd className="mt-1 tabular-nums">{formatDashboardPercent(candidate.decayed_support, locale, { maximumFractionDigits: 1 })}</dd></div>
                          <div><dt className="text-xs text-muted-foreground">{t("learning.samples")}</dt><dd className="mt-1 tabular-nums">{formatDashboardNumber(candidate.accepted_count, locale)}</dd></div>
                          <div><dt className="text-xs text-muted-foreground">{t("learning.windows")}</dt><dd className="mt-1 tabular-nums">{formatDashboardNumber(candidate.independent_window_count, locale)}</dd></div>
                        </dl>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </>
        ) : null}

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
