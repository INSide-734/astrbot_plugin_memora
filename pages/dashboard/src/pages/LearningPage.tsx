import { useState, useEffect, useCallback } from "react";
import { Brain, RotateCw, MessageSquareText, ArrowRightLeft } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { useGroups } from "@/hooks/useGroups";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { cn } from "@/lib/utils";

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
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-3">
        <h1 className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]"><Brain size={18} />{t("nav.learning")}</h1>
        <Button variant="secondary" size="sm" onClick={resetLearning}><RotateCw size={14} />{t("learning.reset")}</Button>
      </header>

      <div className="flex-1 overflow-auto p-6 space-y-6">
        {loading && !stats && <p className="text-center text-sm text-[var(--text-tertiary)]">Loading...</p>}

        {/* Stat cards */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {[{ label: t("learning.hitRate"), value: s.hit_rate, fmt: (v: number) => `${(v * 100).toFixed(1)}%` },
            { label: t("learning.avgQuality"), value: s.avg_quality, fmt: (v: number) => v.toFixed(3) },
            { label: t("learning.trials"), value: s.total_trials, fmt: (v: number) => String(v) },
            { label: t("learning.corrections"), value: s.total_corrections, fmt: (v: number) => String(v) }].map((item) => (
            <div key={item.label} className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
              <div className="text-2xl font-bold tabular-nums text-[var(--text-primary)]">
                {item.value !== undefined && item.value !== null ? item.fmt(item.value) : "--"}
              </div>
              <div className="text-xs text-[var(--text-tertiary)] mt-1">{item.label}</div>
            </div>
          ))}
        </div>

        {/* Parameters */}
        {s.parameters && Object.keys(s.parameters).length > 0 && (
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <h3 className="text-sm font-semibold mb-4">{t("learning.params")}</h3>
            <div className="space-y-2">
              {Object.entries(s.parameters).map(([key, value]) => (
                <div key={key} className="flex items-center gap-3">
                  <span className="w-40 text-xs text-[var(--text-secondary)] truncate">{key}</span>
                  <div className="h-5 flex-1 rounded-md bg-[var(--color-surface-secondary)]">
                    <div className={cn("h-5 rounded-md transition-all duration-500", Number(value) > 0.7 ? "bg-[var(--color-success)]" : Number(value) > 0.4 ? "bg-[var(--color-accent)]" : "bg-[var(--color-border)]")}
                      style={{ width: `${Math.min(100, Number(value) * 100)}%` }} />
                  </div>
                  <span className="w-14 text-xs tabular-nums text-right text-[var(--text-tertiary)]">{Number(value).toFixed(2)}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* History */}
        {s.history && s.history.length > 0 && (
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
            <h3 className="text-sm font-semibold mb-3">{t("learning.history")}</h3>
            <div className="space-y-1">
              {s.history.map((h, i) => (
                <div key={i} className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm hover:bg-[var(--color-surface-secondary)]">
                  <span className="text-xs text-[var(--text-tertiary)] w-24 shrink-0">{String(h.timestamp ?? "").slice(0, 16)}</span>
                  <Badge variant="secondary">{h.action}</Badge>
                  <span className="text-xs text-[var(--text-secondary)] truncate">{h.detail}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Expression Patterns (v1.0.0+) */}
        <div className="rounded-xl border border-[var(--color-border)] overflow-hidden">
          <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-5 py-3">
            <div className="flex items-center gap-2">
              <MessageSquareText size={16} className="text-[var(--color-accent)]" />
              <span className="text-xs font-semibold text-[var(--text-primary)]">{t("expression.title")}</span>
              <span className="text-xs text-[var(--text-tertiary)]">({expressionPatterns.length} {t("expression.patterns").toLowerCase()})</span>
            </div>
            <Select value={groupId} onValueChange={(v) => v && setGroupId(v)} disabled={groups.length === 0}>
              <SelectTrigger className="w-36 h-7 text-2xs"><span>{groupId || t("jargon.allGroups")}</span></SelectTrigger>
              <SelectContent>
                {groups.length > 0 ? groups.map((g) => (
                  <SelectItem key={g.group_id} value={g.group_id}>{g.group_id}</SelectItem>
                )) : (
                  <SelectItem value="loading">—</SelectItem>
                )}
              </SelectContent>
            </Select>
          </div>
          {exprLoading ? (
            <p className="px-5 py-8 text-center text-xs text-[var(--text-tertiary)]">{t("table.loading")}</p>
          ) : expressionPatterns.length === 0 ? (
            <p className="px-5 py-8 text-center text-xs text-[var(--text-tertiary)]">{t("expression.noData")}</p>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="text-xs text-[var(--text-tertiary)] border-b border-[var(--color-border-light)]">
                  <th className="py-2.5 px-5 text-left font-medium">{t("expression.situation")}</th>
                  <th className="py-2.5 px-5 text-left font-medium">{t("expression.expression")}</th>
                  <th className="py-2.5 px-5 text-left font-medium">{t("expression.weight")}</th>
                  <th className="py-2.5 px-5 text-right font-medium">{t("expression.usage")}</th>
                </tr>
              </thead>
              <tbody>
                {expressionPatterns.map((p) => (
                  <tr key={p.pattern_id} className="border-t border-[var(--color-border-light)] hover:bg-[var(--color-surface-secondary)] transition-colors">
                    <td className="py-2.5 px-5 text-xs font-medium text-[var(--text-primary)]">{p.situation}</td>
                    <td className="py-2.5 px-5 text-xs text-[var(--text-secondary)] max-w-[320px] truncate">
                      <ArrowRightLeft size={10} className="inline mr-1 text-[var(--text-tertiary)]" />
                      {p.expression}
                    </td>
                    <td className="py-2.5 px-5">
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-16 rounded-full bg-[var(--color-border-light)] overflow-hidden">
                          <div className="h-full rounded-full bg-[var(--color-accent)]" style={{ width: `${p.weight * 100}%` }} />
                        </div>
                        <span className="text-xs tabular-nums text-[var(--text-secondary)]">{(p.weight * 100).toFixed(0)}%</span>
                      </div>
                    </td>
                    <td className="py-2.5 px-5 text-xs tabular-nums text-right text-[var(--text-secondary)]">{p.usage_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
