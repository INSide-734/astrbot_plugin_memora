import { useState, useEffect, useCallback, useRef } from "react";
import { StickyNote, Plus, Search, Tag, Trash2, Archive, Pencil, X } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { Badge } from "@/components/ui/Badge";
import { PageContent, PageFrame, PageHeader, PageToolbar } from "@/components/layout/PageLayout";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Checkbox } from "@/components/ui/checkbox";
import { selectionStateVariants } from "@/components/ui/selection-state";
import { dashboardLocale, formatDashboardDate } from "@/lib/i18n";
import { Textarea } from "@/components/ui/textarea";
import type { EntityNavigationTarget } from "@/types";

interface NotesPageProps {
  showToast: (msg: string, isError?: boolean) => void;
  navigationTarget?: EntityNavigationTarget | null;
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

export function NotesPage({ showToast, navigationTarget }: NotesPageProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());

  NOTE_STATUS_LABELS.active = t("status.active");
  NOTE_STATUS_LABELS.archived = t("status.archived");
  NOTE_STATUS_LABELS.deleted = t("status.deleted");
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
  const detailRequestRef = useRef(0);

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

  const fetchDetail = useCallback(async (id: string) => {
    const requestId = ++detailRequestRef.current;
    try {
      const res = unwrapApiData(await apiRequest(`notes/detail?note_id=${id}`));
      if (requestId !== detailRequestRef.current) return;
      setDetail((res.note ?? res) as Note);
      setEditData({});
    } catch (e) {
      if (requestId !== detailRequestRef.current) return;
      showToast(String(e), true);
    }
  }, [showToast]);

  useEffect(() => {
    if (!navigationTarget) return;
    void fetchDetail(navigationTarget.id);
  }, [fetchDetail, navigationTarget?.id, navigationTarget?.requestId]);

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
      showToast(t(action === "archive" ? "toast.batchArchived" : "toast.batchDeleted", String(selected.size)));
      setSelected(new Set());
      fetchNotes();
    } catch (e) { showToast(String(e), true); }
  };

  const getNoteId = (n: Note) => n.note_id ?? n.id ?? "";

  return (
    <PageFrame variant="standard" aria-label={t("nav.notes")}>
      <PageHeader
        title={t("nav.notes")}
        icon={<StickyNote />}
        actions={<div className="flex items-center gap-2">
          {notes.length > 0 && (
            <Button variant="ghost" size="sm" aria-label={selected.size === notes.length ? t("notes.deselectAll") : t("notes.selectAll")} onClick={toggleSelectAll}>
              {selected.size === notes.length ? t("select.deselectAll") : t("select.selectAll")}
            </Button>
          )}
          <Button size="sm" onClick={() => setShowCreate(true)}><Plus data-icon="inline-start" />{t("notes.newNote")}</Button>
        </div>}
      />

      <PageToolbar>
        <div className="relative min-w-0 max-w-sm flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input placeholder={t("notes.searchPh")} value={search}
            onChange={(e) => { setSelected(new Set()); setSearch(e.target.value); }} className="pl-9" />
        </div>
        <Select value={statusFilter} onValueChange={(v) => { setSelected(new Set()); setStatusFilter(v ?? ""); }}>
          <SelectTrigger className="w-36"><span>{NOTE_STATUS_LABELS[statusFilter] || statusFilter || t("filter.statusAll")}</span></SelectTrigger>
          <SelectContent>
            <SelectItem value="">{t("filter.statusAll")}</SelectItem>
            <SelectItem value="active">{t("status.active")}</SelectItem>
            <SelectItem value="archived">{t("status.archived")}</SelectItem>
          </SelectContent>
        </Select>
      </PageToolbar>

      <PageContent>
        {loading ? <p className="py-12 text-center text-sm text-muted-foreground">{t("common.loading")}</p> :
         notes.length === 0 ? <p className="py-12 text-center text-sm text-muted-foreground">{t("table.noData")}</p> : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {notes.map((n) => (
              <div key={getNoteId(n)} data-state={selected.has(getNoteId(n)) ? "selected" : undefined} className={cn(
                "cursor-pointer rounded-lg border bg-card p-4 text-card-foreground transition-colors hover:bg-muted/30",
                selectionStateVariants({ kind: "row", selected: selected.has(getNoteId(n)) }),
              )}>
                <div className="flex items-start gap-3">
                  <Checkbox className="mt-1" aria-label={t("notes.selectNote", n.title)}
                    checked={selected.has(getNoteId(n))}
                    onCheckedChange={() => toggleSelect(getNoteId(n))}
                    onClick={(e) => e.stopPropagation()} />
                  <div
                    className="min-w-0 flex-1 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    role="button"
                    tabIndex={0}
                    aria-label={t("notes.openNote", n.title)}
                    onClick={() => fetchDetail(getNoteId(n))}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      event.preventDefault();
                      fetchDetail(getNoteId(n));
                    }}
                  >
                    <h3 className="text-sm font-semibold truncate">{n.title}</h3>
                    <p className="mt-2 line-clamp-3 text-xs text-muted-foreground">{n.content?.slice(0, 150) ?? ""}</p>
                    <div className="mt-3 flex items-center justify-between">
                      <div className="flex flex-wrap gap-1">
                        {(n.tags ?? []).slice(0, 2).map((t) => <Badge key={t}>{t}</Badge>)}
                      </div>
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <span>v{n.version ?? 1}</span>
                        <Badge variant={n.status === "active" ? "default" : "secondary"}>{NOTE_STATUS_LABELS[n.status ?? "active"] ?? n.status ?? t("status.active")}</Badge>
                      </div>
                    </div>
                    {n.updated_at && <div className="mt-2 text-xs text-muted-foreground">{formatDashboardDate(n.updated_at, locale)}</div>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </PageContent>

      {/* Batch bar */}
      {selected.size > 0 && (
        <PageToolbar className="border-b-0 border-t bg-muted/40 animate-slide-up">
          <span className="text-sm font-medium">{t("batch.selected", String(selected.size))}</span>
          <Button variant="secondary" size="sm" onClick={() => batchAction("archive")}><Archive data-icon="inline-start" />{t("common.archive")}</Button>
          <Button variant="destructive" size="sm" onClick={() => batchAction("delete")}><Trash2 data-icon="inline-start" />{t("common.delete")}</Button>
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}><X data-icon="inline-start" />{t("common.clear")}</Button>
        </PageToolbar>
      )}

      {/* Detail panel */}
      <Sheet open={detail !== null} onOpenChange={(open) => { if (!open) setDetail(null); }}>
        {detail && (
        <SheetContent>
          <SheetHeader>
            <SheetTitle>{detail.title}</SheetTitle>
            <SheetDescription>{t("detail.updated")}: {formatDashboardDate(detail.updated_at ?? detail.created_at, locale)}</SheetDescription>
          </SheetHeader>
          <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-5">
            <div className="flex items-center gap-2">
              <Badge variant={detail.status === "active" ? "default" : "secondary"}>{NOTE_STATUS_LABELS[detail.status ?? "active"] ?? detail.status ?? t("status.active")}</Badge>
              <span className="text-xs text-muted-foreground">v{detail.version ?? 1}</span>
            </div>
            {detail.tags && detail.tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {detail.tags.map((t) => <Badge key={t} variant="secondary"><Tag size={10} /> {t}</Badge>)}
              </div>
            )}
            {detail.content && (
              <div><label className="text-xs font-medium text-muted-foreground">{t("detail.content")}</label><p className="mt-1 whitespace-pre-wrap text-sm">{detail.content}</p></div>
            )}

            {/* Edit form */}
            <div className="flex flex-col gap-3 border-t pt-4">
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
                <Textarea
                  rows={6} placeholder={t("placeholder.newContent")} value={editData.content ?? ""}
                  onChange={(e) => setEditData({ ...editData, content: e.target.value })} />
              )}
              {editField === "tags" && (
                <Input placeholder={t("placeholder.tagsComma")} value={editData.tags ?? (detail.tags ?? []).join(", ")}
                  onChange={(e) => setEditData({ ...editData, tags: e.target.value })} />
              )}
            </div>
          </div>
          <SheetFooter>
            <Button variant="destructive" size="sm" onClick={() => deleteNote(detail.note_id ?? detail.id ?? "")}><Trash2 data-icon="inline-start" />{t("common.delete")}</Button>
            {detail.status !== "archived" && (
              <Button variant="secondary" size="sm" onClick={() => archiveNote(detail.note_id ?? detail.id ?? "")}><Archive data-icon="inline-start" />{t("common.archive")}</Button>
            )}
            <Button size="sm" onClick={saveEdit}><Pencil data-icon="inline-start" />{t("common.save")}</Button>
          </SheetFooter>
        </SheetContent>
        )}
      </Sheet>

      {/* Create modal */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="sm:max-w-lg" showCloseButton={false}>
          <DialogHeader><DialogTitle>{t("detail.newNote")}</DialogTitle></DialogHeader>
            <div className="flex flex-col gap-4">
              <Input placeholder={t("placeholder.title")} value={newNote.title} onChange={(e) => setNewNote({ ...newNote, title: e.target.value })} />
              <Textarea className="resize-none" rows={6}
                placeholder={t("placeholder.contentHint")} value={newNote.content} onChange={(e) => setNewNote({ ...newNote, content: e.target.value })} />
              <Input placeholder={t("placeholder.tagsComma")} value={newNote.tags} onChange={(e) => setNewNote({ ...newNote, tags: e.target.value })} />
            </div>
              <DialogFooter>
                <Button variant="secondary" size="sm" onClick={() => setShowCreate(false)}>{t("common.cancel")}</Button>
                <Button size="sm" onClick={createNote}>{t("detail.create")}</Button>
              </DialogFooter>
        </DialogContent>
      </Dialog>
    </PageFrame>
  );
}
