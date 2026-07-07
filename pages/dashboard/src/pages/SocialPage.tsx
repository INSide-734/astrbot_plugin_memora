import { useState, useEffect, useCallback } from "react";
import { useI18n } from "@/hooks/useI18n";
import { useGroups } from "@/hooks/useGroups";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { UsersRound, RefreshCw, ArrowRightLeft, Tag } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { RELATION_CATEGORIES } from "@/lib/constants";
import type { SocialRelationEntry } from "@/types";

interface SocialPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

export function SocialPage({ showToast }: SocialPageProps) {
  const { t } = useI18n();
  const { groups, groupId, setGroupId } = useGroups();
  const [relations, setRelations] = useState<SocialRelationEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [category, setCategory] = useState("all");

  const fetchRelations = useCallback(async () => {
    if (!groupId) return;
    setLoading(true);
    try {
      const params = [`group_id=${groupId}`];
      if (category !== "all") params.push(`category=${category}`);
      const res = unwrapApiData(await apiRequest(`social/relations?${params.join("&")}`));
      setRelations((res.relations ?? []) as SocialRelationEntry[]);
    } catch (e) { showToast(String(e), true); }
    finally { setLoading(false); }
  }, [groupId, category, showToast]);

  useEffect(() => { fetchRelations(); }, [fetchRelations]);

  const relationLabel = (type: string): string => {
    const key = `relation.${type}`;
    const translated = t(key);
    // fallback: if the key wasn't translated, return the raw type
    return translated !== key ? translated : type;
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-6 py-3 shrink-0">
        <div className="flex items-center gap-3">
          <UsersRound size={18} className="text-[var(--color-accent)]" />
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">{t("social.title")}</h2>
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
          <button onClick={fetchRelations} className="flex h-8 items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors">
            <RefreshCw size={13} /> {t("common.refresh")}
          </button>
        </div>
      </header>

      {/* Category filter */}
      <div className="flex items-center gap-1.5 border-b border-[var(--color-border)] px-6 py-2 shrink-0 overflow-x-auto">
        <button
          onClick={() => setCategory("all")}
          className={`shrink-0 rounded-full px-3 py-1 text-2xs font-medium transition-colors ${
            category === "all" ? "bg-[var(--color-accent)]/10 text-[var(--color-accent)]" : "text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
          }`}
        >
          {t("social.allCategories")}
        </button>
        {Object.entries(RELATION_CATEGORIES).map(([key, val]) => (
          <button
            key={key}
            onClick={() => setCategory(key)}
            className={`shrink-0 rounded-full px-3 py-1 text-2xs font-medium transition-colors ${
              category === key ? "bg-[var(--color-accent)]/10 text-[var(--color-accent)]" : "text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
            }`}
          >
            {val.label}
          </button>
        ))}
      </div>

      {/* Relations table */}
      <div className="flex-1 overflow-auto">
        {loading ? (
          <p className="px-6 py-12 text-center text-sm text-[var(--text-tertiary)]">{t("table.loading")}</p>
        ) : relations.length === 0 ? (
          <p className="px-6 py-12 text-center text-sm text-[var(--text-tertiary)]">{t("social.noData")}</p>
        ) : (
          <table className="w-full">
            <thead className="sticky top-0 bg-[var(--color-surface)]">
              <tr className="text-xs text-[var(--text-tertiary)]">
                <th className="py-3 px-4 text-left font-medium">{t("social.relations")}</th>
                <th className="py-3 px-4 text-left font-medium">{t("social.category")}</th>
                <th className="py-3 px-4 text-left font-medium">{t("social.strength")}</th>
                <th className="py-3 px-4 text-left font-medium">{t("social.frequency")}</th>
                <th className="py-3 px-4 text-left font-medium">{t("table.tags")}</th>
              </tr>
            </thead>
            <tbody>
              {relations.map((r) => (
                <tr key={`${r.from_user}-${r.to_user}-${r.relation_type}`} className="border-t border-[var(--color-border-light)] hover:bg-[var(--color-surface-secondary)] transition-colors">
                  <td className="py-2.5 px-4">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-[var(--text-primary)]">{r.from_user}</span>
                      <ArrowRightLeft size={12} className="text-[var(--text-tertiary)]" />
                      <span className="text-xs font-medium text-[var(--text-primary)]">{r.to_user}</span>
                    </div>
                    <div className="text-2xs text-[var(--text-tertiary)] mt-0.5">
                      {relationLabel(r.relation_type)}
                    </div>
                  </td>
                  <td className="py-2.5 px-4">
                    <span className="inline-flex items-center rounded-full bg-[var(--color-accent)]/10 px-2 py-0.5 text-2xs font-medium text-[var(--color-accent)]">
                      {RELATION_CATEGORIES[r.category]?.label ?? r.category}
                    </span>
                  </td>
                  <td className="py-2.5 px-4">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-20 rounded-full bg-[var(--color-border-light)] overflow-hidden">
                        <div
                          className="h-full rounded-full"
                          style={{ width: `${r.strength * 100}%`, background: r.strength >= 0.7 ? "var(--color-success)" : r.strength >= 0.4 ? "var(--color-accent)" : "var(--text-tertiary)" }}
                        />
                      </div>
                      <span className="text-xs tabular-nums text-[var(--text-secondary)]">{(r.strength * 100).toFixed(0)}%</span>
                    </div>
                  </td>
                  <td className="py-2.5 px-4 text-xs tabular-nums text-[var(--text-secondary)]">{r.frequency}</td>
                  <td className="py-2.5 px-4">
                    <div className="flex items-center gap-1 flex-wrap">
                      {r.tags.map((tag) => (
                        <span key={tag} className="inline-flex items-center gap-0.5 rounded-full bg-[var(--color-border-light)] px-1.5 py-0.5 text-2xs text-[var(--text-tertiary)]">
                          <Tag size={8} />{tag}
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
