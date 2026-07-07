import { useState, useEffect, useCallback } from "react";
import { BookOpen, Plus, Search, Trash2, Pencil, X } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { Badge } from "@/components/ui/Badge";

interface KnowledgePageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

interface KnowledgeEntry {
  entry_id?: string;
  id?: string;
  title: string;
  content?: string;
  category?: string;
  confidence?: number;
  access_count?: number;
  updated_at?: string;
  created_at?: string;
}

const CAT_LABELS: Record<string, string> = {};
const EDIT_KB_LABELS: Record<string, string> = {};

export function KnowledgePage({ showToast }: KnowledgePageProps) {
  const { t } = useI18n();

  CAT_LABELS.fact = t("category.fact");
  CAT_LABELS.concept = t("category.concept");
  CAT_LABELS.rule = t("category.rule");
  CAT_LABELS.event = t("category.event");
  CAT_LABELS.procedure = t("category.procedure");
  EDIT_KB_LABELS.title = t("field.title");
  EDIT_KB_LABELS.content = t("field.content");
  EDIT_KB_LABELS.category = t("table.category");
  EDIT_KB_LABELS.confidence = t("table.confidence");
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<KnowledgeEntry | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newEntry, setNewEntry] = useState({ title: "", content: "", category: "fact" });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editField, setEditField] = useState("title");
  const [editData, setEditData] = useState<Record<string, string>>({});

  const fetchEntries = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set("limit", "100");
      if (category) params.set("category", category);
      if (search) {
        const res = unwrapApiData(await apiRequest(`knowledge/search?query=${encodeURIComponent(search)}`));
        setEntries((res.entries ?? res.items ?? []) as KnowledgeEntry[]);
      } else {
        const res = unwrapApiData(await apiRequest(`knowledge?${params.toString()}`));
        setEntries((res.entries ?? res.items ?? []) as KnowledgeEntry[]);
      }
    } catch (e) { showToast(String(e), true); } finally { setLoading(false); }
  }, [search, category, showToast]);

  useEffect(() => { fetchEntries(); }, [fetchEntries]);

  const fetchDetail = async (id: string) => {
    try {
      const res = unwrapApiData(await apiRequest(`knowledge/detail?entry_id=${id}`));
      setDetail((res.entry ?? res) as KnowledgeEntry);
      setEditData({});
    } catch (e) { showToast(String(e), true); }
  };

  const createEntry = async () => {
    try {
      await apiRequest("knowledge/create", { method: "POST", body: newEntry });
      showToast(t("toast.entryCreated"));
      setShowCreate(false);
      setNewEntry({ title: "", content: "", category: "fact" });
      fetchEntries();
    } catch (e) { showToast(String(e), true); }
  };

  const deleteEntry = async (id: string) => {
    try {
      await apiRequest("knowledge/delete", { method: "POST", body: { entry_id: id } });
      showToast(t("toast.entryDeleted"));
      setDetail(null);
      fetchEntries();
    } catch (e) { showToast(String(e), true); }
  };

  const saveEdit = async () => {
    if (!detail) return;
    try {
      await apiRequest("knowledge/update", {
        method: "POST",
        body: { entry_id: detail.entry_id ?? detail.id, field: editField, value: editData[editField] ?? "" },
      });
      showToast(t("toast.entryUpdated"));
      setDetail(null);
      fetchEntries();
    } catch (e) { showToast(String(e), true); }
  };

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selected.size === entries.length) { setSelected(new Set()); }
    else { setSelected(new Set(entries.map((e) => e.entry_id ?? e.id ?? ""))); }
  };

  const batchDelete = async () => {
    if (!selected.size) return;
    try {
      await apiRequest("knowledge/batch", { method: "POST", body: { entry_ids: Array.from(selected), action: "delete" } });
      showToast(`Deleted ${selected.size} entries`);
      setSelected(new Set());
      fetchEntries();
    } catch (e) { showToast(String(e), true); }
  };

  const getEntryId = (e: KnowledgeEntry) => e.entry_id ?? e.id ?? "";

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-3">
        <h1 className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]"><BookOpen size={18} />{t("nav.knowledge")}</h1>
        <div className="flex gap-2">
          <Button size="sm" onClick={() => setShowCreate(true)}><Plus size={14} />{t("kb.newEntry")}</Button>
          <Select value={category} onValueChange={(v) => setCategory(v ?? "")}>
            <SelectTrigger className="w-32"><span>{CAT_LABELS[category] ?? t("filter.categoryAll")}</span></SelectTrigger>
            <SelectContent>
              <SelectItem value="">{t("filter.categoryAll")}</SelectItem>
              <SelectItem value="fact">{t("category.fact")}</SelectItem>
              <SelectItem value="concept">{t("category.concept")}</SelectItem>
              <SelectItem value="rule">{t("category.rule")}</SelectItem>
              <SelectItem value="event">{t("category.event")}</SelectItem>
              <SelectItem value="procedure">{t("category.procedure")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </header>

      <div className="border-b border-[var(--color-border-light)] px-6 py-3">
        <div className="relative max-w-sm">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)]" />
          <Input placeholder={t("kb.searchPh")} value={search}
            onChange={(e) => setSearch(e.target.value)} className="pl-9" />
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        {loading ? <p className="px-6 py-12 text-center text-sm text-[var(--text-tertiary)]">Loading...</p> :
         entries.length === 0 ? <p className="px-6 py-12 text-center text-sm text-[var(--text-tertiary)]">{t("table.noData")}</p> : (
          <table className="w-full">
            <thead className="sticky top-0 bg-[var(--color-surface)]">
              <tr className="border-b border-[var(--color-border)] text-left text-2xs font-medium uppercase tracking-wider text-[var(--text-tertiary)]">
                <th className="w-10 px-4 py-2.5"><input type="checkbox" checked={selected.size === entries.length && entries.length > 0} onChange={toggleSelectAll} /></th>
                <th className="px-4 py-2.5">{t("table.title")}</th><th className="px-3 py-2.5">{t("table.category")}</th>
                <th className="px-3 py-2.5">{t("table.confidence")}</th><th className="px-3 py-2.5">{t("table.updated")}</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={getEntryId(e)} className="border-b border-[var(--color-border-light)] text-sm hover:bg-[var(--color-surface-secondary)] cursor-pointer"
                  onClick={() => fetchDetail(getEntryId(e))}>
                  <td className="px-4 py-2.5" onClick={(ev) => ev.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(getEntryId(e))} onChange={() => toggleSelect(getEntryId(e))} />
                  </td>
                  <td className="px-4 py-2.5 font-medium">{e.title}</td>
                  <td className="px-3 py-2.5"><Badge variant="secondary">{e.category ?? "fact"}</Badge></td>
                  <td className="px-3 py-2.5 text-xs tabular-nums">{Number(e.confidence ?? 0).toFixed(2)}</td>
                  <td className="px-3 py-2.5 text-xs text-[var(--text-tertiary)]">{String(e.updated_at ?? e.created_at ?? "").slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Batch bar */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 border-t border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-6 py-2.5 animate-slide-up">
          <span className="text-sm font-medium">{selected.size} selected</span>
          <Button variant="destructive" size="sm" onClick={batchDelete}><Trash2 size={14} />Delete</Button>
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}><X size={14} />{t("common.clear")}</Button>
        </div>
      )}

      {/* Detail panel */}
      {detail && (
        <div className="fixed inset-y-0 right-0 z-40 w-[420px] overflow-y-auto border-l border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-modal animate-slide-in-right">
          <div className="flex items-center justify-between border-b border-[var(--color-border)] px-5 py-3">
            <h3 className="text-sm font-semibold">{detail.title}</h3>
            <button onClick={() => setDetail(null)} className="rounded-lg p-1 text-[var(--text-tertiary)] hover:bg-[var(--color-surface-secondary)]">{<X size={16} />}</button>
          </div>
          <div className="p-5 space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div><label className="text-xs font-medium text-[var(--text-tertiary)]">{t("table.category")}</label><p className="text-sm">{detail.category ?? "fact"}</p></div>
              <div><label className="text-xs font-medium text-[var(--text-tertiary)]">{t("table.confidence")}</label><p className="text-sm">{Number(detail.confidence ?? 0).toFixed(2)}</p></div>
              <div><label className="text-xs font-medium text-[var(--text-tertiary)]">{t("table.accessCount")}</label><p className="text-sm">{detail.access_count ?? 0}</p></div>
            </div>
            {detail.content && (
              <div><label className="text-xs font-medium text-[var(--text-tertiary)]">Content</label><p className="mt-1 whitespace-pre-wrap text-sm">{detail.content}</p></div>
            )}

            {/* Edit form */}
            <div className="border-t border-[var(--color-border-light)] pt-4 space-y-3">
              <h4 className="text-sm font-semibold">{t("detail.edit")}</h4>
              <Select value={editField} onValueChange={(v) => v && setEditField(v)}>
                <SelectTrigger><span>{EDIT_KB_LABELS[editField] ?? editField}</span></SelectTrigger>
                <SelectContent>
                  <SelectItem value="title">{t("field.title")}</SelectItem>
                  <SelectItem value="content">{t("field.content")}</SelectItem>
                  <SelectItem value="category">{t("table.category")}</SelectItem>
                  <SelectItem value="confidence">{t("table.confidence")}</SelectItem>
                </SelectContent>
              </Select>
              {editField === "title" && (
                <Input placeholder={t("placeholder.newTitle")} value={editData.title ?? ""}
                  onChange={(e) => setEditData({ ...editData, title: e.target.value })} />
              )}
              {editField === "content" && (
                <textarea className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm focus:border-[var(--color-accent)] focus:outline-none"
                  rows={4} placeholder={t("placeholder.newContent")} value={editData.content ?? ""}
                  onChange={(e) => setEditData({ ...editData, content: e.target.value })} />
              )}
              {editField === "category" && (
                <Select value={editData.category ?? (detail.category ?? "fact")} onValueChange={(v) => v && setEditData({ ...editData, category: v })}>
                  <SelectTrigger><span>{CAT_LABELS[editData.category ?? detail.category ?? "fact"]}</span></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="fact">{t("category.fact")}</SelectItem>
                    <SelectItem value="concept">{t("category.concept")}</SelectItem>
                    <SelectItem value="rule">{t("category.rule")}</SelectItem>
                    <SelectItem value="event">{t("category.event")}</SelectItem>
                    <SelectItem value="procedure">{t("category.procedure")}</SelectItem>
                  </SelectContent>
                </Select>
              )}
              {editField === "confidence" && (
                <Input type="number" min="0" max="1" step="0.01" placeholder="0.00-1.00"
                  value={editData.confidence ?? String(detail.confidence ?? 0)}
                  onChange={(e) => setEditData({ ...editData, confidence: e.target.value })} />
              )}
              <div className="flex gap-2">
                <Button size="sm" onClick={saveEdit}><Pencil size={14} />{t("common.save")}</Button>
                <Button variant="destructive" size="sm" onClick={() => deleteEntry(detail.entry_id ?? detail.id ?? "")}><Trash2 size={14} />Delete</Button>
                <Button variant="secondary" size="sm" onClick={() => setDetail(null)}>{t("common.cancel")}</Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/40" onClick={() => setShowCreate(false)} />
          <div className="relative z-10 w-full max-w-lg rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-modal animate-scale-in">
            <div className="flex items-center justify-between border-b border-[var(--color-border-light)] px-6 py-3">
              <h2 className="text-sm font-semibold">{t("detail.newEntry")}</h2>
              <button onClick={() => setShowCreate(false)} className="rounded-lg p-1 text-[var(--text-tertiary)] hover:bg-[var(--color-surface-secondary)]">{<X size={16} />}</button>
            </div>
            <div className="space-y-4 p-6">
              <Input placeholder={t("placeholder.title")} value={newEntry.title} onChange={(e) => setNewEntry({ ...newEntry, title: e.target.value })} />
              <textarea className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm focus:border-[var(--color-accent)] focus:outline-none resize-none" rows={4}
                placeholder={t("placeholder.contentHint")} value={newEntry.content} onChange={(e) => setNewEntry({ ...newEntry, content: e.target.value })} />
              <Select value={newEntry.category} onValueChange={(v) => v && setNewEntry({ ...newEntry, category: v })}>
                <SelectTrigger><span>{CAT_LABELS[newEntry.category] ?? newEntry.category}</span></SelectTrigger>
                <SelectContent>
                  <SelectItem value="fact">{t("category.fact")}</SelectItem>
                  <SelectItem value="concept">{t("category.concept")}</SelectItem>
                  <SelectItem value="rule">{t("category.rule")}</SelectItem>
                  <SelectItem value="event">{t("category.event")}</SelectItem>
                  <SelectItem value="procedure">{t("category.procedure")}</SelectItem>
                </SelectContent>
              </Select>
              <div className="flex justify-end gap-2">
                <Button variant="secondary" size="sm" onClick={() => setShowCreate(false)}>{t("common.cancel")}</Button>
                <Button size="sm" onClick={createEntry}>{t("detail.create")}</Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
