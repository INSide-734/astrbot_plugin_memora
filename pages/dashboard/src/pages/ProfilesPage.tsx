import { useState, useEffect, useCallback } from "react";
import { UserRound, Tag, Trash2, X } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";

interface ProfilesPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

interface Profile {
  user_id: string;
  display_name?: string;
  tag_count?: number;
  tags?: Array<{ name: string; confidence: number; category?: string }>;
  top_interests?: string[];
  preferences?: Record<string, unknown>;
  last_seen?: string;
  message_count?: number;
}

export function ProfilesPage({ showToast }: ProfilesPageProps) {
  const { t } = useI18n();
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [total, setTotal] = useState(0);
  const [detail, setDetail] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const fetchProfiles = useCallback(async () => {
    setLoading(true);
    try {
      const res = unwrapApiData(await apiRequest("profiles?limit=100"));
      setProfiles((res.profiles ?? res.items ?? []) as Profile[]);
      setTotal(Number(res.total ?? 0));
    } catch (e) { showToast(String(e), true); } finally { setLoading(false); }
  }, [showToast]);

  useEffect(() => { fetchProfiles(); }, [fetchProfiles]);

  const fetchDetail = async (userId: string) => {
    try {
      const res = unwrapApiData(await apiRequest(`profiles/detail?user_id=${userId}`));
      setDetail((res.profile ?? res) as Profile);
    } catch (e) { showToast(String(e), true); }
  };

  const deleteProfile = async (userId: string) => {
    try {
      await apiRequest("profiles/delete", { method: "POST", body: { user_id: userId } });
      showToast(t("toast.profileDeleted"));
      setDetail(null);
      fetchProfiles();
    } catch (e) { showToast(String(e), true); }
  };

  const toggleSelect = (id: string) => {
    setSelected((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  };

  const toggleSelectAll = () => {
    setSelected(selected.size === profiles.length ? new Set() : new Set(profiles.map((p) => p.user_id)));
  };

  const batchDelete = async () => {
    if (!selected.size) return;
    try {
      await apiRequest("profiles/batch", { method: "POST", body: { user_ids: Array.from(selected), action: "delete" } });
      showToast(`Deleted ${selected.size} profiles`);
      setSelected(new Set());
      fetchProfiles();
    } catch (e) { showToast(String(e), true); }
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center border-b border-[var(--color-border)] px-6 py-3">
        <h1 className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]"><UserRound size={18} /> {t("nav.profiles")}</h1>
      </header>

      <div className="flex gap-4 border-b border-[var(--color-border-light)] px-6 py-3">
        <div className="text-center"><div className="text-lg font-bold tabular-nums">{total}</div><div className="text-2xs text-[var(--text-tertiary)]">{t("stats.profiles")}</div></div>
        <div className="text-center"><div className="text-lg font-bold tabular-nums">{profiles.reduce((s, p) => s + (p.tag_count ?? p.tags?.length ?? 0), 0)}</div><div className="text-2xs text-[var(--text-tertiary)]">{t("table.tags")}</div></div>
      </div>

      <div className="flex-1 overflow-auto">
        {loading ? <p className="px-6 py-12 text-center text-sm text-[var(--text-tertiary)]">{t("common.loading")}</p>
         : profiles.length === 0 ? <p className="px-6 py-12 text-center text-sm text-[var(--text-tertiary)]">{t("table.noData")}</p>
         : (
          <table className="w-full">
            <thead className="sticky top-0 bg-[var(--color-surface)]">
              <tr className="border-b border-[var(--color-border)] text-left text-2xs font-medium uppercase tracking-wider text-[var(--text-tertiary)]">
                <th className="w-10 px-4 py-2.5"><input type="checkbox" checked={selected.size === profiles.length && profiles.length > 0} onChange={toggleSelectAll} /></th>
                <th className="px-4 py-2.5">{t("table.userId")}</th>
                <th className="px-3 py-2.5">{t("table.name")}</th>
                <th className="px-3 py-2.5">{t("table.tags")}</th>
                <th className="px-3 py-2.5">Interests</th>
                <th className="px-3 py-2.5">Last Seen</th>
              </tr>
            </thead>
            <tbody>
              {profiles.map((p) => (
                <tr key={p.user_id} className="border-b border-[var(--color-border-light)] text-sm hover:bg-[var(--color-surface-secondary)] cursor-pointer"
                  onClick={() => fetchDetail(p.user_id)}>
                  <td className="px-4 py-2.5" onClick={(ev) => ev.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(p.user_id)} onChange={() => toggleSelect(p.user_id)} />
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-[var(--text-secondary)]">{p.user_id}</td>
                  <td className="px-3 py-2.5 font-medium">{p.display_name ?? "--"}</td>
                  <td className="px-3 py-2.5"><Badge>{p.tag_count ?? p.tags?.length ?? 0}</Badge></td>
                  <td className="max-w-xs px-3 py-2.5"><div className="flex flex-wrap gap-1">{(p.top_interests ?? []).slice(0, 3).map((t) => <Badge key={t} variant="secondary">{t}</Badge>)}</div></td>
                  <td className="px-3 py-2.5 text-xs text-[var(--text-tertiary)]">{String(p.last_seen ?? "").slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {selected.size > 0 && (
        <div className="flex items-center gap-3 border-t border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-6 py-2.5 animate-slide-up">
          <span className="text-sm font-medium">{selected.size} selected</span>
          <Button variant="destructive" size="sm" onClick={batchDelete}><Trash2 size={14} />Delete</Button>
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}><X size={14} />{t("common.clear")}</Button>
        </div>
      )}

      {detail && (
        <div className="fixed inset-y-0 right-0 z-40 w-[420px] overflow-y-auto border-l border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-modal animate-slide-in-right">
          <div className="flex items-center justify-between border-b border-[var(--color-border)] px-5 py-3">
            <h3 className="text-sm font-semibold">Profile: {detail.display_name ?? detail.user_id}</h3>
            <button onClick={() => setDetail(null)} className="rounded-lg p-1 text-[var(--text-tertiary)] hover:bg-[var(--color-surface-secondary)]">{<X size={16} />}</button>
          </div>
          <div className="p-5 space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div><label className="text-xs font-medium text-[var(--text-tertiary)]">User ID</label><p className="font-mono text-sm">{detail.user_id}</p></div>
              <div><label className="text-xs font-medium text-[var(--text-tertiary)]">Messages</label><p className="text-sm">{detail.message_count ?? "--"}</p></div>
            </div>
            {detail.tags && detail.tags.length > 0 && (
              <div><h4 className="text-xs font-semibold mb-2 flex items-center gap-1.5"><Tag size={12} /> Tags</h4>
                <div className="space-y-1.5">
                  {detail.tags.map((t, i) => (
                    <div key={i} className="flex items-center justify-between rounded-lg bg-[var(--color-surface-secondary)] px-3 py-1.5 text-sm">
                      <span>{t.name}</span>
                      <div className="flex items-center gap-2">
                        <div className="h-1 w-16 rounded-full bg-[var(--color-border)]"><div className="h-1 rounded-full bg-[var(--color-accent)]" style={{ width: `${(t.confidence ?? 0) * 100}%` }} /></div>
                        <span className="text-xs tabular-nums text-[var(--text-tertiary)]">{((t.confidence ?? 0) * 100).toFixed(0)}%</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="border-t border-[var(--color-border-light)] pt-4">
              <Button variant="destructive" size="sm" onClick={() => deleteProfile(detail.user_id)}><Trash2 size={14} />{t("detail.deleteProfile")}</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
