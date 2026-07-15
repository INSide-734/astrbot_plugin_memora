import { useState, useEffect, useCallback, useRef } from "react";
import { Brain, RotateCw, MessageSquareText, ArrowRightLeft } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { useGroups } from "@/hooks/useGroups";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { MetricGrid, PageContent, PageFrame, PageHeader, PageToolbar } from "@/components/layout/PageLayout";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/Progress";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
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

export function LearningPage({ showToast }: LearningPageProps) {
  const { t, currentLang } = useI18n();
  const { groups, groupId, setGroupId } = useGroups();
  const [stats, setStats] = useState<LearningStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [expressionPatterns, setExpressionPatterns] = useState<Array<{ pattern_id: number; situation: string; expression: string; weight: number; usage_count: number; group_id: string }>>([]);
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
      const res = unwrapApiData(await apiRequest(`expression/patterns?group_id=${groupId}`));
      if (expressionRequestRef.current === requestId) {
        setExpressionPatterns((res.patterns ?? []) as Array<{ pattern_id: number; situation: string; expression: string; weight: number; usage_count: number; group_id: string }>);
      }
    } catch {
      // Expression errors remain non-blocking for the learning overview.
    } finally {
      if (expressionRequestRef.current === requestId) {
        setExprLoading(false);
      }
    }
  }, [groupId]);

  useEffect(() => { fetchStats(); fetchExpressions(); }, [fetchStats, fetchExpressions]);

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
            <Select value={groupId} onValueChange={(v) => v && setGroupId(v)} disabled={groups.length === 0}>
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
          {exprLoading ? (
            <p className="px-5 py-8 text-center text-xs text-muted-foreground">{t("table.loading")}</p>
          ) : expressionPatterns.length === 0 ? (
            <p className="px-5 py-8 text-center text-xs text-muted-foreground">{t("expression.noData")}</p>
          ) : (
            <Table>
              <TableHeader><TableRow>
                  <TableHead>{t("expression.situation")}</TableHead>
                  <TableHead>{t("expression.expression")}</TableHead>
                  <TableHead>{t("expression.weight")}</TableHead>
                  <TableHead className="text-right">{t("expression.usage")}</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {expressionPatterns.map((p) => (
                  <TableRow key={p.pattern_id}>
                    <TableCell className="text-xs font-medium">{p.situation}</TableCell>
                    <TableCell className="max-w-[20rem] truncate text-xs text-muted-foreground">
                      <ArrowRightLeft className="mr-1 inline" />
                      {p.expression}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Progress aria-label={`${p.situation} ${p.expression} ${t("expression.weight")} ${p.pattern_id}`} value={p.weight} className="h-1.5 w-16" />
                        <span className="text-xs tabular-nums text-muted-foreground">{formatDashboardPercent(p.weight, locale, { maximumFractionDigits: 0 })}</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-right text-xs tabular-nums text-muted-foreground">{p.usage_count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>
      </PageContent>
    </PageFrame>
  );
}
