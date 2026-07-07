import { useState, useEffect, useCallback } from "react";
import { useI18n } from "@/hooks/useI18n";
import { useGroups } from "@/hooks/useGroups";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { Heart, Smile, Users, Zap, RefreshCw, TrendingUp } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/Select";
import type { AffectionStatus, AffectionUserEntry } from "@/types";
import { MOOD_TYPES } from "@/lib/constants";

interface AffectionPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

const AFFECTION_LEVEL_COLORS: Record<string, string> = {
  HOSTILE: "#dc2626", DISLIKED: "#f97316", COLD: "#6b7280",
  NEUTRAL: "#a8a29e", WARM: "#3b82f6", FRIENDLY: "#22c55e",
  CLOSE: "#8b5cf6", INTIMATE: "#ec4899",
};

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
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-6 py-3 shrink-0">
        <div className="flex items-center gap-3">
          <Heart size={18} className="text-[var(--color-accent)]" />
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">{t("affection.title")}</h2>
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
          <button onClick={fetchData} className="flex h-8 items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors">
            <RefreshCw size={13} /> {t("common.refresh")}
          </button>
        </div>
      </header>

      {loading ? (
        <p className="px-6 py-12 text-center text-sm text-[var(--text-tertiary)]">{t("table.loading")}</p>
      ) : !data ? (
        <p className="px-6 py-12 text-center text-sm text-[var(--text-tertiary)]">{t("affection.noData")}</p>
      ) : (
        <div className="flex-1 overflow-auto p-6 space-y-6">
          {/* Mood Card */}
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-secondary)] p-5">
            <div className="flex items-center gap-2 mb-4">
              <Smile size={16} className="text-[var(--color-accent)]" />
              <span className="text-xs font-semibold text-[var(--text-primary)]">{t("affection.mood")}</span>
            </div>
            <div className="flex items-center gap-6">
              <div className="flex items-center gap-3">
                <span className="text-4xl">{moodMeta?.emoji ?? "🤖"}</span>
                <div>
                  <div className="text-lg font-semibold text-[var(--text-primary)]" style={{ color: moodMeta?.color }}>
                    {moodMeta?.label ?? mood?.mood_type ?? "—"}
                  </div>
                  <div className="text-xs text-[var(--text-tertiary)] mt-0.5 max-w-[260px]">{mood?.description ?? ""}</div>
                </div>
              </div>
              <div className="flex-1 max-w-[200px]">
                <div className="flex items-center justify-between text-2xs text-[var(--text-tertiary)] mb-1">
                  <span>{t("affection.moodIntensity")}</span>
                  <span>{mood?.intensity != null ? `${Math.round(mood.intensity * 100)}%` : "—"}</span>
                </div>
                <div className="h-2 rounded-full bg-[var(--color-border-light)] overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{ width: `${(mood?.intensity ?? 0) * 100}%`, background: moodMeta?.color ?? "var(--color-accent)" }}
                  />
                </div>
              </div>
            </div>
          </div>

          {/* Emotions Grid */}
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-secondary)] p-5">
            <div className="flex items-center gap-2 mb-4">
              <Zap size={16} className="text-[var(--color-accent)]" />
              <span className="text-xs font-semibold text-[var(--text-primary)]">{t("affection.emotions")}</span>
            </div>
            <div className="grid grid-cols-5 gap-3">
              {MOOD_TYPES.map((mt) => (
                <div
                  key={mt.type}
                  className={`flex flex-col items-center gap-1.5 rounded-lg border p-3 transition-colors ${
                    mood?.mood_type === mt.type
                      ? "border-current bg-[var(--color-surface)]"
                      : "border-[var(--color-border-light)]"
                  }`}
                  style={mood?.mood_type === mt.type ? { borderColor: mt.color } : {}}
                >
                  <span className="text-xl">{mt.emoji}</span>
                  <span className="text-2xs font-medium text-[var(--text-secondary)]">{mt.label}</span>
                  {mood?.mood_type === mt.type && (
                    <span className="inline-flex items-center rounded-full px-1.5 py-px text-2xs font-medium text-white" style={{ background: mt.color }}>
                      {t("status.active")}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Affection Leaderboard */}
          <div className="rounded-xl border border-[var(--color-border)] overflow-hidden">
            <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-5 py-3">
              <div className="flex items-center gap-2">
                <TrendingUp size={16} className="text-[var(--color-accent)]" />
                <span className="text-xs font-semibold text-[var(--text-primary)]">{t("affection.leaderboard")}</span>
              </div>
              <div className="flex items-center gap-4 text-2xs text-[var(--text-tertiary)]">
                <span><Users size={12} className="inline mr-1" />{data.user_count} {t("jargon.users").toLowerCase()}</span>
                <span>{t("affection.score")}: {data.total_affection}/{data.max_total_affection}</span>
              </div>
            </div>
            <table className="w-full">
              <thead>
                <tr className="text-xs text-[var(--text-tertiary)] border-b border-[var(--color-border-light)]">
                  <th className="py-2.5 px-5 text-left font-medium w-8">#</th>
                  <th className="py-2.5 px-5 text-left font-medium">{t("TABLE.USERID")}</th>
                  <th className="py-2.5 px-5 text-left font-medium">{t("affection.score")}</th>
                  <th className="py-2.5 px-5 text-left font-medium">{t("affection.level")}</th>
                  <th className="py-2.5 px-5 text-right font-medium">{t("affection.interactions")}</th>
                </tr>
              </thead>
              <tbody>
                {data.top_users.map((u: AffectionUserEntry, i: number) => (
                  <tr key={u.user_id} className="border-b border-[var(--color-border-light)] hover:bg-[var(--color-surface-secondary)] transition-colors">
                    <td className="py-2.5 px-5 text-xs text-[var(--text-tertiary)]">{i + 1}</td>
                    <td className="py-2.5 px-5 text-xs font-medium text-[var(--text-primary)]">{u.user_id}</td>
                    <td className="py-2.5 px-5">
                      <div className="flex items-center gap-3">
                        <div className="h-1.5 w-24 rounded-full bg-[var(--color-border-light)] overflow-hidden">
                          <div
                            className="h-full rounded-full transition-all"
                            style={{
                              width: `${Math.max(0, Math.min(100, ((u.affection_score + 100) / 200) * 100))}%`,
                              background: AFFECTION_LEVEL_COLORS[u.affection_level] ?? "var(--color-accent)",
                            }}
                          />
                        </div>
                        <span className="text-xs tabular-nums font-medium text-[var(--text-primary)]">{u.affection_score}</span>
                      </div>
                    </td>
                    <td className="py-2.5 px-5">
                      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-2xs font-medium" style={{ background: `${AFFECTION_LEVEL_COLORS[u.affection_level] ?? "var(--color-accent)"}15`, color: AFFECTION_LEVEL_COLORS[u.affection_level] ?? "var(--color-accent)" }}>
                        {u.level_name}
                      </span>
                    </td>
                    <td className="py-2.5 px-5 text-right text-xs tabular-nums text-[var(--text-secondary)]">{u.interaction_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
