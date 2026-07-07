import { useState, useEffect, useCallback } from "react";
import { StickyNote, Plus, Search, Tag, Trash2, Archive, Pencil, X } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { Badge } from "@/components/ui/Badge";

interface NotesPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

interface Note {
  note_id?: string;
  id?: string;
  title: string;
  content?: string;
  tags?: string[];
  status?: string;
  version?: number;
  updated_at?: string;
  created_at?: string;
}

const NOTE_STATUS_LABELS: Record<string, string> = {};
const EDIT_NOTE_LABELS: Record<string, string> = {};

export function NotesPage({ showToast }: NotesPageProps) {
  const { t } = useI18n();

  NOTE_STATUS_LABELS.active = t("status.active");
  NOTE_STATUS_LABELS.archived = t("status.archived");
  EDIT_NOTE_LABELS.title = t("field.title");
  EDIT_NOTE_LABELS.content = t("field.content");
  EDIT_NOTE_LABELS.tags = t("field.tags");
  const [notes, setNotes] = useState<Note[]>([]);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<Note | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newNote, setNewNote] = useState({ title: "", content: "", tags: "" });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editField, setEditField] = useState("title");
  const [editData, setEditData] = useState<Record<string, string>>({});

  const fetchNotes = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams(); params.set("limit", "100");
      if (statusFilter) params.set("status", statusFilter);
      const res = search
        ? unwrapApiData(await apiRequest(`notes/search?query=${encodeURIComponent(search)}`))
        : unwrapApiData(await apiRequest(`notes?${params.toString()}`));
      setNotes((res.notes ?? res.items ?? []) as Note[]);
    } catch (e) { showToast(String(e), true); } finally { setLoading(false); }
  }, [search, statusFilter, showToast]);

  useEffect(() => { fetchNotes(); }, [fetchNotes]);

  const fetchDetail = async (id: string) => {
    try {
      const res = unwrapApiData(await apiRequest(`notes/detail?note_id=${id}`));
      setDetail((res.note ?? res) as Note);
      setEditData({});
    } catch (e) { showToast(String(e), true); }
  };

  const createNote = async () => {
    try {
      await apiRequest("notes/create", {
        method: "POST",
        body: { ...newNote, tags: newNote.tags.split(",").map((t) => t.trim()).filter(Boolean) },
      });
      showToast(t("toast.noteCreated"));
      setShowCreate(false);
      setNewNote({ title: "", content: "", tags: "" });
      fetchNotes();
    } catch (e) { showToast(String(e), true); }
  };

  const deleteNote = async (id: string) => {
    try {
      await apiRequest("notes/delete", { method: "POST", body: { note_id: id } });
      showToast(t("toast.noteDeleted"));
      setDetail(null);
      fetchNotes();
    } catch (e) { showToast(String(e), true); }
  };

  const archiveNote = async (id: string) => {
    try {
      await apiRequest("notes/archive", { method: "POST", body: { note_id: id } });
      showToast(t("toast.noteArchived"));
      setDetail(null);
      fetchNotes();
    } catch (e) { showToast(String(e), true); }
  };

  const saveEdit = async () => {
    if (!detail) return;
    const noteId = detail.note_id ?? detail.id ?? "";
    try {
      await apiRequest("notes/update", {
        method: "POST",
        body: { note_id: noteId, field: editField, value: editData[editField] ?? "" },
      });
      showToast(t("toast.noteUpdated"));
      setDetail(null);
      fetchNotes();
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
    if (selected.size === notes.length) { setSelected(new Set()); }
    else { setSelected(new Set(notes.map((n) => n.note_id ?? n.id ?? ""))); }
  };

  const batchAction = async (action: "delete" | "archive") => {
    if (!selected.size) return;
    try {
      await apiRequest("notes/batch", { method: "POST", body: { note_ids: Array.from(selected), action } });
      showToast(`${action}d ${selected.size} notes`);
      setSelected(new Set());
      fetchNotes();
    } catch (e) { showToast(String(e), true); }
  };

  const getNoteId = (n: Note) => n.note_id ?? n.id ?? "";

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-3">
        <h1 className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]"><StickyNote size={18} />{t("nav.notes")}</h1>
        <div className="flex items-center gap-2">
          {notes.length > 0 && (
            <button onClick={toggleSelectAll} className="text-xs text-[var(--text-tertiary)] hover:text-[var(--color-accent)]">
              {selected.size === notes.length ? t("select.deselectAll") : t("select.selectAll")}
            </button>
          )}
          <Button size="sm" onClick={() => setShowCreate(true)}><Plus size={14} />{t("notes.newNote")}</Button>
        </div>
      </header>

      <div className="flex items-center gap-3 border-b border-[var(--color-border-light)] px-6 py-3">
        <div className="relative max-w-sm flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)]" />
          <Input placeholder={t("notes.searchPh")} value={search}
            onChange={(e) => setSearch(e.target.value)} className="pl-9" />
        </div>
        <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v ?? "")}>
          <SelectTrigger className="w-36"><span>{NOTE_STATUS_LABELS[statusFilter] || statusFilter || t("filter.statusAll")}</span></SelectTrigger>
          <SelectContent>
            <SelectItem value="">{t("filter.statusAll")}</SelectItem>
            <SelectItem value="active">{t("status.active")}</SelectItem>
            <SelectItem value="archived">{t("status.archived")}</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="flex-1 overflow-auto">
        {loading ? <p className="px-6 py-12 text-center text-sm text-[var(--text-tertiary)]">Loading...</p> :
         notes.length === 0 ? <p className="px-6 py-12 text-center text-sm text-[var(--text-tertiary)]">{t("table.noData")}</p> : (
          <div className="grid grid-cols-1 gap-4 p-6 sm:grid-cols-2 lg:grid-cols-3">
            {notes.map((n) => (
              <div key={getNoteId(n)} className={cn(
                "cursor-pointer rounded-xl border p-4 transition-shadow hover:shadow-elevated",
                selected.has(getNoteId(n)) ? "border-[var(--color-accent)] bg-[var(--color-accent)]/5" : "border-[var(--color-border)] bg-[var(--color-surface)]"
              )}>
                <div className="flex items-start gap-3">
                  <input type="checkbox" className="mt-1"
                    checked={selected.has(getNoteId(n))}
                    onChange={() => toggleSelect(getNoteId(n))}
                    onClick={(e) => e.stopPropagation()} />
                  <div className="flex-1 min-w-0" onClick={() => fetchDetail(getNoteId(n))}>
                    <h3 className="text-sm font-semibold truncate">{n.title}</h3>
                    <p className="mt-2 line-clamp-3 text-xs text-[var(--text-secondary)]">{n.content?.slice(0, 150) ?? ""}</p>
                    <div className="mt-3 flex items-center justify-between">
                      <div className="flex flex-wrap gap-1">
                        {(n.tags ?? []).slice(0, 2).map((t) => <Badge key={t}>{t}</Badge>)}
                      </div>
                      <div className="flex items-center gap-2 text-2xs text-[var(--text-tertiary)]">
                        <span>v{n.version ?? 1}</span>
                        <Badge variant={n.status === "active" ? "default" : "secondary"}>{n.status ?? "active"}</Badge>
                      </div>
                    </div>
                    {n.updated_at && <div className="mt-2 text-2xs text-[var(--text-tertiary)]">{String(n.updated_at).slice(0, 10)}</div>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Batch bar */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 border-t border-[var(--color-border)] bg-[var(--color-surface-secondary)] px-6 py-2.5 animate-slide-up">
          <span className="text-sm font-medium">{t("batch.selected", String(selected.size))}</span>
          <Button variant="secondary" size="sm" onClick={() => batchAction("archive")}><Archive size={14} />{t("common.archive")}</Button>
          <Button variant="destructive" size="sm" onClick={() => batchAction("delete")}><Trash2 size={14} />{t("common.delete")}</Button>
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
            <div className="flex items-center gap-2">
              <Badge variant={detail.status === "active" ? "default" : "secondary"}>{detail.status ?? "active"}</Badge>
              <span className="text-xs text-[var(--text-tertiary)]">v{detail.version ?? 1}</span>
            </div>
            {detail.tags && detail.tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {detail.tags.map((t) => <Badge key={t} variant="secondary"><Tag size={10} /> {t}</Badge>)}
              </div>
            )}
            {detail.content && (
              <div><label className="text-xs font-medium text-[var(--text-tertiary)]">{t("detail.content")}</label><p className="mt-1 whitespace-pre-wrap text-sm">{detail.content}</p></div>
            )}
            <div className="text-xs text-[var(--text-tertiary)]">{t("detail.updated")}: {String(detail.updated_at ?? detail.created_at ?? "")}</div>

            {/* Action buttons */}
            <div className="flex gap-2 border-t border-[var(--color-border-light)] pt-4">
              {detail.status !== "archived" && (
                <Button variant="secondary" size="sm" onClick={() => archiveNote(detail.note_id ?? detail.id ?? "")}><Archive size={14} />Archive</Button>
              )}
              <Button variant="destructive" size="sm" onClick={() => deleteNote(detail.note_id ?? detail.id ?? "")}><Trash2 size={14} />Delete</Button>
            </div>

            {/* Edit form */}
            <div className="border-t border-[var(--color-border-light)] pt-4 space-y-3">
              <h4 className="text-sm font-semibold">{t("detail.edit")}</h4>
              <Select value={editField} onValueChange={(v) => v && setEditField(v)}>
                <SelectTrigger><span>{EDIT_NOTE_LABELS[editField] ?? editField}</span></SelectTrigger>
                <SelectContent>
                  <SelectItem value="title">{t("field.title")}</SelectItem>
                  <SelectItem value="content">{t("field.content")}</SelectItem>
                  <SelectItem value="tags">{t("field.tags")}</SelectItem>
                </SelectContent>
              </Select>
              {editField === "title" && (
                <Input placeholder={t("placeholder.newTitle")} value={editData.title ?? ""}
                  onChange={(e) => setEditData({ ...editData, title: e.target.value })} />
              )}
              {editField === "content" && (
                <textarea className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm focus:border-[var(--color-accent)] focus:outline-none"
                  rows={6} placeholder={t("placeholder.newContent")} value={editData.content ?? ""}
                  onChange={(e) => setEditData({ ...editData, content: e.target.value })} />
              )}
              {editField === "tags" && (
                <Input placeholder={t("placeholder.tagsComma")} value={editData.tags ?? (detail.tags ?? []).join(", ")}
                  onChange={(e) => setEditData({ ...editData, tags: e.target.value })} />
              )}
              <div className="flex gap-2">
                <Button size="sm" onClick={saveEdit}><Pencil size={14} />{t("common.save")}</Button>
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
              <h2 className="text-sm font-semibold">{t("detail.newNote")}</h2>
              <button onClick={() => setShowCreate(false)} className="rounded-lg p-1 text-[var(--text-tertiary)] hover:bg-[var(--color-surface-secondary)]">{<X size={16} />}</button>
            </div>
            <div className="space-y-4 p-6">
              <Input placeholder={t("placeholder.title")} value={newNote.title} onChange={(e) => setNewNote({ ...newNote, title: e.target.value })} />
              <textarea className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-sm focus:border-[var(--color-accent)] focus:outline-none resize-none" rows={6}
                placeholder={t("placeholder.contentHint")} value={newNote.content} onChange={(e) => setNewNote({ ...newNote, content: e.target.value })} />
              <Input placeholder={t("placeholder.tagsComma")} value={newNote.tags} onChange={(e) => setNewNote({ ...newNote, tags: e.target.value })} />
              <div className="flex justify-end gap-2">
                <Button variant="secondary" size="sm" onClick={() => setShowCreate(false)}>{t("common.cancel")}</Button>
                <Button size="sm" onClick={createNote}>{t("detail.create")}</Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
