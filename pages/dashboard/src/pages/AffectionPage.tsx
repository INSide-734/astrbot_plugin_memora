import { useState, useEffect, useCallback } from "react";
import { useI18n } from "@/hooks/useI18n";
import { useGroups } from "@/hooks/useGroups";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { Heart, Smile, Users, Zap, RefreshCw, TrendingUp } from "lucide-react";
import { MetricGrid, PageContent, PageFrame, PageHeader } from "@/components/layout/PageLayout";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { AffectionStatus, AffectionUserEntry } from "@/types";
import { MOOD_TYPES } from "@/lib/constants";

interface AffectionPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

export function AffectionPage({ showToast }: AffectionPageProps) {
  const { t } = useI18n();
  const { groups, groupId, setGroupId } = useGroups();
  const [data, setData] = useState<AffectionStatus | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchData = useCallback(async () => {
    if (!groupId) return;
    setLoading(true);
    try {
      const res = unwrapApiData(await apiRequest(`affection/status?group_id=${groupId}`));
      setData(res as unknown as AffectionStatus);
    } catch (e) { showToast(String(e), true); }
    finally { setLoading(false); }
  }, [groupId, showToast]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const mood = data?.current_mood;
  const moodMeta = MOOD_TYPES.find((m) => m.type === mood?.mood_type);

  return (
    <PageFrame variant="standard" aria-label={t("affection.title")}>
      <PageHeader
        title={t("affection.title")}
        icon={<Heart />}
        actions={<>
          <Select value={groupId} onValueChange={(v) => v && setGroupId(v)} disabled={groups.length === 0}>
            <SelectTrigger className="w-36 text-xs"><span>{groupId || t("jargon.allGroups")}</span></SelectTrigger>
            <SelectContent>
              <SelectGroup>
              {groups.length > 0 ? groups.map((g) => (
                <SelectItem key={g.group_id} value={g.group_id}>{g.group_id}{g.message_count ? ` (${g.message_count})` : ""}</SelectItem>
              )) : (
                <SelectItem value="loading">—</SelectItem>
              )}
              </SelectGroup>
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={fetchData}><RefreshCw data-icon="inline-start" />{t("common.refresh")}</Button>
        </>}
      />
      <PageContent className="flex flex-col gap-6">
        {loading ? (
          <p className="py-12 text-center text-sm text-muted-foreground">{t("table.loading")}</p>
        ) : !data ? (
          <p className="py-12 text-center text-sm text-muted-foreground">{t("affection.noData")}</p>
        ) : (<>
          <MetricGrid minItemWidth="18rem">
            <Card>
              <CardHeader><CardTitle className="flex items-center gap-2"><Smile />{t("affection.mood")}</CardTitle></CardHeader>
              <CardContent className="flex flex-wrap items-center gap-6">
                <div className="flex min-w-0 items-center gap-3">
                <span className="text-4xl">{moodMeta?.emoji ?? "🤖"}</span>
                <div className="min-w-0">
                  <div className="text-lg font-semibold text-foreground">
                    {moodMeta?.label ?? mood?.mood_type ?? "—"}
                  </div>
                  <div className="mt-0.5 text-xs text-muted-foreground">{mood?.description ?? ""}</div>
                </div>
              </div>
              <div className="min-w-[10rem] flex-1">
                <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
                  <span>{t("affection.moodIntensity")}</span>
                  <span>{mood?.intensity != null ? `${Math.round(mood.intensity * 100)}%` : "—"}</span>
                </div>
                <div role="progressbar" aria-label={t("affection.moodIntensity")} aria-valuemin={0} aria-valuemax={1} aria-valuenow={mood?.intensity ?? 0} className="h-2 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary transition-all duration-500"
                    style={{ width: `${(mood?.intensity ?? 0) * 100}%` }}
                  />
                </div>
              </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="flex items-center gap-2"><Users />{t("affection.leaderboard")}</CardTitle></CardHeader>
              <CardContent className="grid grid-cols-2 gap-4">
                <div><div className="text-2xl font-semibold tabular-nums">{data.user_count}</div><div className="text-xs text-muted-foreground">{t("jargon.users")}</div></div>
                <div><div className="text-2xl font-semibold tabular-nums">{data.total_affection}/{data.max_total_affection}</div><div className="text-xs text-muted-foreground">{t("affection.score")}</div></div>
              </CardContent>
            </Card>
          </MetricGrid>

          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2"><Zap />{t("affection.emotions")}</CardTitle></CardHeader>
            <CardContent><MetricGrid minItemWidth="7rem" className="gap-3">
              {MOOD_TYPES.map((mt) => (
                <div
                  key={mt.type}
                  className={`flex flex-col items-center gap-1.5 rounded-md border p-3 transition-colors ${mood?.mood_type === mt.type ? "border-primary bg-primary/5" : "border-border"}`}
                >
                  <span className="text-xl">{mt.emoji}</span>
                  <span className="text-xs font-medium text-foreground">{mt.label}</span>
                  {mood?.mood_type === mt.type && (
                    <Badge>{t("status.active")}</Badge>
                  )}
                </div>
              ))}
            </MetricGrid></CardContent>
          </Card>

          <Card className="gap-0 py-0">
            <CardHeader className="border-b py-4"><CardTitle className="flex items-center gap-2"><TrendingUp />{t("affection.leaderboard")}</CardTitle></CardHeader>
            <Table>
              <TableHeader><TableRow>
                  <TableHead className="w-8">#</TableHead>
                  <TableHead>{t("TABLE.USERID")}</TableHead>
                  <TableHead>{t("affection.score")}</TableHead>
                  <TableHead>{t("affection.level")}</TableHead>
                  <TableHead className="text-right">{t("affection.interactions")}</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {data.top_users.map((u: AffectionUserEntry, i: number) => (
                  <TableRow key={u.user_id}>
                    <TableCell className="text-xs text-muted-foreground">{i + 1}</TableCell>
                    <TableCell className="text-xs font-medium">{u.user_id}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-3">
                        <div role="progressbar" aria-label={`${u.user_id} ${t("affection.score")}`} aria-valuemin={-100} aria-valuemax={100} aria-valuenow={u.affection_score} className="h-1.5 w-24 overflow-hidden rounded-full bg-muted">
                          <div
                            className="h-full rounded-full bg-primary transition-all"
                            style={{ width: `${Math.max(0, Math.min(100, ((u.affection_score + 100) / 200) * 100))}%` }}
                          />
                        </div>
                        <span className="text-xs font-medium tabular-nums">{u.affection_score}</span>
                      </div>
                    </TableCell>
                    <TableCell><Badge variant="secondary">{u.level_name}</Badge></TableCell>
                    <TableCell className="text-right text-xs tabular-nums text-muted-foreground">{u.interaction_count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        </>)}
      </PageContent>
    </PageFrame>
  );
}
