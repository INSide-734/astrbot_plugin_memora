import { useState, useEffect, useCallback } from "react";
import { useI18n } from "@/hooks/useI18n";
import { useGroups } from "@/hooks/useGroups";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { MessageCircleCode, Check, X, Search, RefreshCw, Sparkles, Hash, Users, Zap, Globe, Loader2 } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/Select";
import type { JargonCandidate, JargonMeaning } from "@/types";

interface JargonPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

/** Pure score-bar renderer — no component closure dependencies. */
function ScoreBar({ score }: { score: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 rounded-full bg-[var(--color-border-light)] overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{ width: `${Math.round(score * 100)}%`, background: score >= 0.8 ? "var(--color-success)" : score >= 0.6 ? "var(--color-accent)" : "var(--text-tertiary)" }}
        />
      </div>
      <span className="text-xs tabular-nums text-[var(--text-secondary)]">{(score * 100).toFixed(0)}%</span>
    </div>
  );
}

export function JargonPage({ showToast }: JargonPageProps) {
  const { t } = useI18n();
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
      showToast(t(confirmed ? "toast.jargonConfirmed" : "toast.jargonRejected").replace("{0}", term));
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

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-6 py-3 shrink-0">
        <div className="flex items-center gap-3">
          <MessageCircleCode size={18} className="text-[var(--color-accent)]" />
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">{t("jargon.title")}</h2>
        </div>
        <div className="flex items-center gap-2">
          <Select value={groupId} onValueChange={(v) => v && setGroupId(v)} disabled={groups.length === 0}>
            <SelectTrigger className="w-36 h-8 text-xs"><span>{groupId || t("jargon.allGroups")}</span></SelectTrigger>
            <SelectContent>
              {groups.length > 0 ? groups.map((g) => (
                <SelectItem key={g.group_id} value={g.group_id}>{g.group_id}{g.message_count ? ` (${g.message_count})` : ""}</SelectItem>
              )) : (
                <SelectItem value="loading">—</SelectItem>
              )}
            </SelectContent>
          </Select>
          <button
            onClick={() => { fetchStats(); if (tab === "candidates") fetchCandidates(); else fetchMeanings(); }}
            className="flex h-8 items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
          >
            <RefreshCw size={13} /> {t("common.refresh")}
          </button>
        </div>
      </header>

      {/* Stats bar */}
      <div className="flex gap-6 border-b border-[var(--color-border)] px-6 py-2.5 text-xs text-[var(--text-secondary)] shrink-0">
        <div className="flex items-center gap-1.5"><Hash size={13} /> {t("jargon.stats")}: <span className="font-semibold text-[var(--text-primary)]">{stats.total_terms}</span> {t("jargon.meanings").toLowerCase()}</div>
        <div className="flex items-center gap-1.5"><Users size={13} /> <span className="font-semibold text-[var(--text-primary)]">{stats.candidate_count}</span> {t("jargon.candidates").toLowerCase()}</div>
        <div className="flex items-center gap-1.5"><Zap size={13} /> <span className="font-semibold text-[var(--text-primary)]">{stats.store_confirmed}</span> {t("jargon.confirm").toLowerCase()}</div>
      </div>

      {/* Tab bar */}
      <div className="flex items-center justify-between border-b border-[var(--color-border)] px-6 shrink-0">
        <div className="flex gap-1">
          {(["candidates", "meanings"] as const).map((tKey) => (
            <button
              key={tKey}
              onClick={() => setTab(tKey)}
              className={`px-4 py-2.5 text-xs font-medium border-b-2 transition-colors ${
                tab === tKey ? "border-[var(--color-accent)] text-[var(--color-accent)]" : "border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
              }`}
            >
              {t(`jargon.${tKey}`)}
            </button>
          ))}
        </div>
        <button
          onClick={handleMine}
          disabled={mining}
          className="flex h-8 items-center gap-1.5 rounded-lg bg-[var(--color-accent)] px-3 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {mining ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
          {mining ? t("jargon.mining") : t("jargon.mine")}
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {loading ? (
          <p className="px-6 py-12 text-center text-sm text-[var(--text-tertiary)]">{t("table.loading")}</p>
        ) : tab === "candidates" ? (
          candidates.length === 0 ? (
            <p className="px-6 py-12 text-center text-sm text-[var(--text-tertiary)]">{t("jargon.noCandidates")}</p>
          ) : (
            <table className="w-full">
              <thead className="sticky top-0 bg-[var(--color-surface)]">
                <tr className="text-xs text-[var(--text-tertiary)]">
                  <th className="py-3 px-4 text-left font-medium">{t("table.title")}</th>
                  <th className="py-3 px-4 text-left font-medium">{t("jargon.score")}</th>
                  <th className="py-3 px-4 text-left font-medium">{t("jargon.frequency")}</th>
                  <th className="py-3 px-4 text-left font-medium">{t("jargon.users")}</th>
                  <th className="py-3 px-4 text-left font-medium">{t("detail.content")}</th>
                  <th className="py-3 px-4 text-right font-medium">{t("table.status")}</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((c) => (
                  <tr key={`${c.term}-${c.group_id}`} className="border-t border-[var(--color-border-light)] hover:bg-[var(--color-surface-secondary)] transition-colors">
                    <td className="py-2.5 px-4">
                      <span className="text-sm font-medium text-[var(--text-primary)]">{c.term}</span>
                    </td>
                    <td className="py-2.5 px-4"><ScoreBar score={c.score} /></td>
                    <td className="py-2.5 px-4 text-xs tabular-nums text-[var(--text-secondary)]">{c.frequency}</td>
                    <td className="py-2.5 px-4 text-xs tabular-nums text-[var(--text-secondary)]">{c.unique_users}</td>
                    <td className="py-2.5 px-4 text-xs text-[var(--text-tertiary)] max-w-[300px] truncate">
                      {c.context_examples?.[0] ?? "—"}
                    </td>
                    <td className="py-2.5 px-4">
                      <div className="flex items-center justify-end gap-1">
                        <button onClick={() => handleConfirm(c.term, true)} className="p-1.5 rounded-md hover:bg-[var(--color-success)]/10 text-[var(--text-tertiary)] hover:text-[var(--color-success)] transition-colors" title={t("jargon.confirm")}>
                          <Check size={15} />
                        </button>
                        <button onClick={() => handleConfirm(c.term, false)} className="p-1.5 rounded-md hover:bg-[var(--color-danger)]/10 text-[var(--text-tertiary)] hover:text-[var(--color-danger)] transition-colors" title={t("jargon.reject")}>
                          <X size={15} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : (
          meanings.length === 0 ? (
            <p className="px-6 py-12 text-center text-sm text-[var(--text-tertiary)]">{t("jargon.noMeanings")}</p>
          ) : (
            <table className="w-full">
              <thead className="sticky top-0 bg-[var(--color-surface)]">
                <tr className="text-xs text-[var(--text-tertiary)]">
                  <th className="py-3 px-4 text-left font-medium">{t("table.title")}</th>
                  <th className="py-3 px-4 text-left font-medium">{t("jargon.meaning")}</th>
                  <th className="py-3 px-4 text-left font-medium">{t("jargon.confidence")}</th>
                  <th className="py-3 px-4 text-left font-medium">{t("jargon.global")}</th>
                  <th className="py-3 px-4 text-right font-medium">{t("table.status")}</th>
                </tr>
              </thead>
              <tbody>
                {meanings.map((m) => (
                  <tr key={`${m.term}-${m.group_id}`} className="border-t border-[var(--color-border-light)] hover:bg-[var(--color-surface-secondary)] transition-colors">
                    <td className="py-2.5 px-4">
                      <span className="text-sm font-medium text-[var(--text-primary)]">{m.term}</span>
                    </td>
                    <td className="py-2.5 px-4 text-xs text-[var(--text-secondary)] max-w-[320px]">{m.meaning || "—"}</td>
                    <td className="py-2.5 px-4"><ScoreBar score={m.confidence} /></td>
                    <td className="py-2.5 px-4">
                      {m.is_global ? <Globe size={14} className="text-[var(--color-accent)]" /> : <span className="text-xs text-[var(--text-tertiary)]">—</span>}
                    </td>
                    <td className="py-2.5 px-4 text-right">
                      {m.is_confirmed ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-[var(--color-success)]/10 px-2 py-0.5 text-2xs font-medium text-[var(--color-success)]">{t("jargon.confirm").toLowerCase()}</span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-full bg-[var(--text-tertiary)]/10 px-2 py-0.5 text-2xs font-medium text-[var(--text-tertiary)]">{t("jargon.reject").toLowerCase()}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        )}
      </div>
    </div>
  );
}
