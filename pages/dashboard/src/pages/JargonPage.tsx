import { useState, useEffect, useCallback } from "react";
import type { KeyboardEvent } from "react";
import { useI18n } from "@/hooks/useI18n";
import { useGroups } from "@/hooks/useGroups";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { MessageCircleCode, Check, X, RefreshCw, Sparkles, Hash, Users, Zap, Globe, Loader2 } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { MetricGrid, PageContent, PageFrame, PageHeader, PageToolbar } from "@/components/layout/PageLayout";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/Table";
import { dashboardLocale, formatDashboardPercent } from "@/lib/i18n";
import type { JargonCandidate, JargonMeaning } from "@/types";

interface JargonPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

/** Pure score-bar renderer — no component closure dependencies. */
function ScoreBar({ score, locale }: { score: number; locale: string }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-all duration-300"
          style={{ width: `${Math.round(score * 100)}%` }}
        />
      </div>
      <span className="text-xs tabular-nums text-muted-foreground">{formatDashboardPercent(score, locale, { maximumFractionDigits: 0 })}</span>
    </div>
  );
}

export function JargonPage({ showToast }: JargonPageProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const { groups, groupId, setGroupId } = useGroups();
  const [tab, setTab] = useState<"candidates" | "meanings">("candidates");
  const [candidates, setCandidates] = useState<JargonCandidate[]>([]);
  const [meanings, setMeanings] = useState<JargonMeaning[]>([]);
  const [loading, setLoading] = useState(false);
  const [mining, setMining] = useState(false);
  const [stats, setStats] = useState({ total_terms: 0, candidate_count: 0, store_confirmed: 0 });

  // --- Data fetchers (each has a focused dependency set) ---

  const fetchCandidates = useCallback(async () => {
    if (!groupId) return;
    setLoading(true);
    try {
      const res = unwrapApiData(await apiRequest(`jargon/candidates?group_id=${groupId}&limit=50`));
      setCandidates((res.candidates ?? []) as JargonCandidate[]);
    } catch (e) { showToast(String(e), true); }
    finally { setLoading(false); }
  }, [groupId, showToast]);

  const fetchMeanings = useCallback(async () => {
    if (!groupId) return;
    setLoading(true);
    try {
      const res = unwrapApiData(await apiRequest(`jargon/meanings?group_id=${groupId}&confirmed_only=false`));
      setMeanings((res.meanings ?? []) as JargonMeaning[]);
    } catch (e) { showToast(String(e), true); }
    finally { setLoading(false); }
  }, [groupId, showToast]);

  const fetchStats = useCallback(async () => {
    if (!groupId) return;
    try {
      const res = unwrapApiData(await apiRequest(`jargon/stats?group_id=${groupId}`));
      setStats({
        total_terms: (res.total_terms as number) ?? 0,
        candidate_count: (res.candidate_count as number) ?? 0,
        store_confirmed: (res.store_confirmed as number) ?? 0,
      });
    } catch { /* stats are non-critical */ }
  }, [groupId]);

  // Reactive fetch on tab/groupId change
  useEffect(() => {
    fetchStats();
    if (tab === "candidates") fetchCandidates();
    else fetchMeanings();
  }, [tab, groupId]); // eslint-disable-line react-hooks/exhaustive-deps
  // ^ only tab & groupId are intentional triggers; the fetch functions
  //   are stable (useCallback with [groupId, showToast]).

  // --- Actions ---

  const handleConfirm = useCallback(async (term: string, confirmed: boolean) => {
    try {
      await apiRequest("jargon/confirm", { method: "POST", body: { term, group_id: groupId, confirmed } });
      showToast(t(confirmed ? "toast.jargonConfirmed" : "toast.jargonRejected", term));
      fetchCandidates();
      fetchMeanings();
      fetchStats();
    } catch (e) { showToast(String(e), true); }
  }, [groupId, showToast, fetchCandidates, fetchMeanings, fetchStats, t]);

  const handleMine = useCallback(async () => {
    setMining(true);
    try {
      const res = unwrapApiData(await apiRequest("jargon/mine", { method: "POST", body: { group_id: groupId, limit: 5 } }));
      showToast(t("toast.jargonMineStarted"));
      const count = (res.inferred_count as number) ?? 0;
      if (count > 0) { fetchCandidates(); fetchStats(); }
    } catch (e) { showToast(String(e), true); }
    finally { setMining(false); }
  }, [groupId, showToast, fetchCandidates, fetchStats, t]);

  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, current: "candidates" | "meanings") => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const next = current === "candidates" ? "meanings" : "candidates";
    setTab(next);
    document.getElementById(`jargon-${next}-tab`)?.focus();
  };

  return (
    <PageFrame variant="dense" aria-label={t("jargon.title")}>
      <PageHeader
        title={t("jargon.title")}
        icon={<MessageCircleCode />}
        actions={<>
          <Select value={groupId} onValueChange={(v) => v && setGroupId(v)} disabled={groups.length === 0}>
            <SelectTrigger className="w-36 text-xs"><span>{groupId || t("jargon.allGroups")}</span></SelectTrigger>
            <SelectContent>
              {groups.length > 0 ? groups.map((g) => (
                <SelectItem key={g.group_id} value={g.group_id}>{g.group_id}{g.message_count ? ` (${g.message_count})` : ""}</SelectItem>
              )) : (
                <SelectItem value="loading">—</SelectItem>
              )}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            onClick={() => { fetchStats(); if (tab === "candidates") fetchCandidates(); else fetchMeanings(); }}
          >
            <RefreshCw data-icon="inline-start" /> {t("common.refresh")}
          </Button>
        </>}
      />

      {/* Stats bar */}
      <div className="flex min-h-12 shrink-0 items-center border-b bg-muted/30 px-4 py-2 sm:px-5 lg:px-6">
        <MetricGrid minItemWidth="12rem" className="w-full gap-3 text-xs text-muted-foreground">
          <div className="flex items-center gap-1.5"><Hash /> {t("jargon.stats")}: <span className="font-semibold text-foreground">{stats.total_terms}</span> {t("jargon.meanings").toLowerCase()}</div>
          <div className="flex items-center gap-1.5"><Users /> <span className="font-semibold text-foreground">{stats.candidate_count}</span> {t("jargon.candidates").toLowerCase()}</div>
          <div className="flex items-center gap-1.5"><Zap /> <span className="font-semibold text-foreground">{stats.store_confirmed}</span> {t("jargon.confirm").toLowerCase()}</div>
        </MetricGrid>
      </div>

      {/* Tab bar */}
      <PageToolbar className="justify-between bg-background">
        <div className="flex gap-1" role="tablist" aria-label={t("jargon.views")}>
          {(["candidates", "meanings"] as const).map((tKey) => (
            <Button
              key={tKey}
              variant={tab === tKey ? "secondary" : "ghost"}
              size="sm"
              role="tab"
              id={`jargon-${tKey}-tab`}
              aria-selected={tab === tKey}
              aria-controls={`jargon-${tKey}-panel`}
              tabIndex={tab === tKey ? 0 : -1}
              onClick={() => setTab(tKey)}
              onKeyDown={(event) => handleTabKeyDown(event, tKey)}
            >
              {t(`jargon.${tKey}`)}
            </Button>
          ))}
        </div>
        <Button
          size="sm"
          onClick={handleMine}
          disabled={mining}
        >
          {mining ? <Loader2 data-icon="inline-start" className="animate-spin" /> : <Sparkles data-icon="inline-start" />}
          {mining ? t("jargon.mining") : t("jargon.mine")}
        </Button>
      </PageToolbar>

      {/* Content */}
      <PageContent width="full" className="p-0">
        <div id="jargon-candidates-panel" role="tabpanel" aria-labelledby="jargon-candidates-tab" hidden={tab !== "candidates"}>
          {loading ? (
            <p className="px-6 py-12 text-center text-sm text-muted-foreground">{t("table.loading")}</p>
          ) : candidates.length === 0 ? (
            <p className="px-6 py-12 text-center text-sm text-muted-foreground">{t("jargon.noCandidates")}</p>
          ) : (
            <Table>
              <TableHeader className="sticky top-0 bg-background">
                <TableRow className="text-xs text-muted-foreground">
                  <TableHead className="px-4">{t("table.title")}</TableHead>
                  <TableHead>{t("jargon.score")}</TableHead>
                  <TableHead>{t("jargon.frequency")}</TableHead>
                  <TableHead>{t("jargon.users")}</TableHead>
                  <TableHead>{t("detail.content")}</TableHead>
                  <TableHead className="text-right">{t("table.status")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {candidates.map((c) => (
                  <TableRow key={`${c.term}-${c.group_id}`}>
                    <TableCell className="px-4">
                      <span className="text-sm font-medium text-foreground">{c.term}</span>
                    </TableCell>
                    <TableCell><ScoreBar score={c.score} locale={locale} /></TableCell>
                    <TableCell className="text-xs tabular-nums text-muted-foreground">{c.frequency}</TableCell>
                    <TableCell className="text-xs tabular-nums text-muted-foreground">{c.unique_users}</TableCell>
                    <TableCell className="max-w-[300px] truncate text-xs text-muted-foreground">
                      {c.context_examples?.[0] ?? "—"}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-end gap-1">
                        <Button variant="ghost" size="icon-sm" onClick={() => handleConfirm(c.term, true)} title={t("jargon.confirm")} aria-label={t("jargon.confirm")}><Check /></Button>
                        <Button variant="ghost" size="icon-sm" onClick={() => handleConfirm(c.term, false)} title={t("jargon.reject")} aria-label={t("jargon.reject")}><X /></Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
        <div id="jargon-meanings-panel" role="tabpanel" aria-labelledby="jargon-meanings-tab" hidden={tab !== "meanings"}>
          {loading ? (
            <p className="px-6 py-12 text-center text-sm text-muted-foreground">{t("table.loading")}</p>
          ) : meanings.length === 0 ? (
            <p className="px-6 py-12 text-center text-sm text-muted-foreground">{t("jargon.noMeanings")}</p>
          ) : (
            <Table>
              <TableHeader className="sticky top-0 bg-background">
                <TableRow className="text-xs text-muted-foreground">
                  <TableHead className="px-4">{t("table.title")}</TableHead>
                  <TableHead>{t("jargon.meaning")}</TableHead>
                  <TableHead>{t("jargon.confidence")}</TableHead>
                  <TableHead>{t("jargon.global")}</TableHead>
                  <TableHead className="text-right">{t("table.status")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {meanings.map((m) => (
                  <TableRow key={`${m.term}-${m.group_id}`}>
                    <TableCell className="px-4">
                      <span className="text-sm font-medium text-foreground">{m.term}</span>
                    </TableCell>
                    <TableCell className="max-w-[320px] text-xs text-muted-foreground">{m.meaning || "—"}</TableCell>
                    <TableCell><ScoreBar score={m.confidence} locale={locale} /></TableCell>
                    <TableCell>
                      {m.is_global ? <Globe className="text-primary" /> : <span className="text-xs text-muted-foreground">—</span>}
                    </TableCell>
                    <TableCell className="text-right">
                      {m.is_confirmed ? (
                        <Badge>{t("jargon.confirm").toLowerCase()}</Badge>
                      ) : (
                        <Badge variant="secondary">{t("jargon.reject").toLowerCase()}</Badge>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </PageContent>
    </PageFrame>
  );
}
