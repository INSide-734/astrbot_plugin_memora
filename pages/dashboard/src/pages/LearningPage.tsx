import { useState, useEffect, useCallback } from "react";
import { Brain, RotateCw, MessageSquareText, ArrowRightLeft } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { useGroups } from "@/hooks/useGroups";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { MetricGrid, PageContent, PageFrame, PageHeader, PageToolbar } from "@/components/layout/PageLayout";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

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
  const { t } = useI18n();
  const { groups, groupId, setGroupId } = useGroups();
  const [stats, setStats] = useState<LearningStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [expressionPatterns, setExpressionPatterns] = useState<Array<{ pattern_id: number; situation: string; expression: string; weight: number; usage_count: number; group_id: string }>>([]);
  const [exprLoading, setExprLoading] = useState(false);

  const fetchStats = useCallback(async () => {
    setLoading(true);
    try {
      const res = unwrapApiData(await apiRequest("learning/status"));
      setStats(res as LearningStats);
    } catch (e) { showToast(String(e), true); } finally { setLoading(false); }
  }, [showToast]);

  const fetchExpressions = useCallback(async () => {
    if (!groupId) return;
    setExprLoading(true);
    try {
      const res = unwrapApiData(await apiRequest(`expression/patterns?group_id=${groupId}`));
      setExpressionPatterns((res.patterns ?? []) as Array<{ pattern_id: number; situation: string; expression: string; weight: number; usage_count: number; group_id: string }>);
    } catch { /* silent */ }
    finally { setExprLoading(false); }
  }, [groupId]);

  useEffect(() => { fetchStats(); fetchExpressions(); }, [fetchStats, fetchExpressions]);

  const resetLearning = async () => {
    if (!confirm(t("learning.resetConfirm"))) return;
    try {
      await apiRequest("learning/reset", { method: "POST" });
      showToast(t("learning.resetDone"));
      fetchStats();
    } catch (e) { showToast(String(e), true); }
  };

  const s = stats ?? {};

  return (
    <PageFrame variant="standard" aria-label={t("nav.learning")}>
      <PageHeader
        title={t("nav.learning")}
        icon={<Brain />}
        actions={<Button variant="secondary" size="sm" onClick={resetLearning}><RotateCw data-icon="inline-start" />{t("learning.reset")}</Button>}
      />
      <PageContent className="flex flex-col gap-6">
        {loading && !stats && <p className="text-center text-sm text-muted-foreground">Loading...</p>}

        <MetricGrid minItemWidth="10rem">
          {[{ label: t("learning.hitRate"), value: s.hit_rate, fmt: (v: number) => `${(v * 100).toFixed(1)}%` },
            { label: t("learning.avgQuality"), value: s.avg_quality, fmt: (v: number) => v.toFixed(3) },
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

        {s.parameters && Object.keys(s.parameters).length > 0 && (
          <Card>
            <CardHeader><CardTitle><h2>{t("learning.params")}</h2></CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-3">
              {Object.entries(s.parameters).map(([key, value]) => (
                <div key={key} className="grid min-w-0 grid-cols-[minmax(6rem,10rem)_minmax(4rem,1fr)_3.5rem] items-center gap-3">
                  <span className="truncate text-xs text-muted-foreground">{key}</span>
                  <div role="progressbar" aria-label={key} aria-valuemin={0} aria-valuemax={1} aria-valuenow={Number(value)} className="h-2 overflow-hidden rounded-full bg-muted">
                    <div className="h-full rounded-full bg-primary transition-all duration-500"
                      style={{ width: `${Math.min(100, Number(value) * 100)}%` }} />
                  </div>
                  <span className="text-right text-xs tabular-nums text-muted-foreground">{Number(value).toFixed(2)}</span>
                </div>
              ))}
            </CardContent>
          </Card>
        )}

        {s.history && s.history.length > 0 && (
          <Card>
            <CardHeader><CardTitle><h2>{t("learning.history")}</h2></CardTitle></CardHeader>
            <CardContent className="flex flex-col gap-1">
              {s.history.map((h, i) => (
                <div key={i} className="flex min-w-0 items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-muted/50">
                  <span className="w-24 shrink-0 text-xs text-muted-foreground">{String(h.timestamp ?? "").slice(0, 16)}</span>
                  <Badge variant="secondary">{h.action}</Badge>
                  <span className="truncate text-xs text-foreground">{h.detail}</span>
                </div>
              ))}
            </CardContent>
          </Card>
        )}

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
                        <div role="progressbar" aria-label={`${p.situation} ${p.expression} ${t("expression.weight")} ${p.pattern_id}`} aria-valuemin={0} aria-valuemax={1} aria-valuenow={p.weight} className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                          <div className="h-full rounded-full bg-primary" style={{ width: `${p.weight * 100}%` }} />
                        </div>
                        <span className="text-xs tabular-nums text-muted-foreground">{(p.weight * 100).toFixed(0)}%</span>
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
