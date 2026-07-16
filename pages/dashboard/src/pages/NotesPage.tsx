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
import { EntityCreateDialog } from "@/components/editing/EntityCreateDialog";
import { EntityEditorSheet } from "@/components/editing/EntityEditorSheet";
import { UnsavedChangesDialog } from "@/components/editing/UnsavedChangesDialog";
import { DeleteConfirmDialog } from "@/components/editing/DeleteConfirmDialog";
import { NoteForm, type NoteDraft } from "@/components/editing/forms/NoteForm";
import { BULK_CONFIRMATION_THRESHOLD, editingErrorDetails, type FieldErrors } from "@/types/editing";
import type { EntityNavigationTarget } from "@/types";

interface NotesPageProps {
  showToast: (msg: string, isError?: boolean) => void;
  navigationTarget?: EntityNavigationTarget | null;
  onDirtyChange?: (dirty: boolean) => void;
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
const NOTE_FORM_FIELDS = ["title", "content", "tags", "status"] as const;
const normalizeNoteField = (name: string) => /^tags\.\d+$/.test(name) ? "tags" : name;

export function NotesPage({ showToast, navigationTarget, onDirtyChange }: NotesPageProps) {
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
  const [newNote, setNewNote] = useState<NoteDraft>({ title: "", content: "", tags: [], status: "active" });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editDraft, setEditDraft] = useState<NoteDraft>({ title: "", content: "", tags: [], status: "active" });
  const [editMode, setEditMode] = useState(false);
  const [editDirty, setEditDirty] = useState(false);
  const [createDirty, setCreateDirty] = useState(false);
  const [editSubmitting, setEditSubmitting] = useState(false);
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [editFieldErrors, setEditFieldErrors] = useState<FieldErrors>({});
  const [createFieldErrors, setCreateFieldErrors] = useState<FieldErrors>({});
  const [editFormError, setEditFormError] = useState<string | null>(null);
  const [createFormError, setCreateFormError] = useState<string | null>(null);
  const [pendingClose, setPendingClose] = useState<"edit" | "create" | "selection" | null>(null);
  const [pendingSelection, setPendingSelection] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false);
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
      const note = (res.note ?? res) as Note;
      setDetail(note);
      setEditDraft({ title: note.title ?? "", content: note.content ?? "", tags: note.tags ?? [], status: note.status ?? "active" });
      setEditMode(false);
      setEditDirty(false);
      setEditFieldErrors({});
      setEditFormError(null);
    } catch (e) {
      if (requestId !== detailRequestRef.current) return;
      showToast(String(e), true);
    }
  }, [showToast]);

  useEffect(() => {
    if (!navigationTarget) return;
    if ((editDirty || createDirty) && navigationTarget.id === (detail?.note_id ?? detail?.id)) return;
    void fetchDetail(navigationTarget.id);
  }, [fetchDetail, navigationTarget?.id, navigationTarget?.requestId]);

  const createNote = async () => {
    if (createSubmitting) return;
    setCreateSubmitting(true);
    setCreateFieldErrors({});
    setCreateFormError(null);
    try {
      await unwrapApiData(await apiRequest("notes/create", {
        method: "POST",
        body: newNote,
      }));
      showToast(t("toast.noteCreated"));
      setShowCreate(false);
      setNewNote({ title: "", content: "", tags: [], status: "active" });
      setCreateDirty(false);
      setCreateFieldErrors({});
      setCreateFormError(null);
      fetchNotes();
    } catch (error) {
      const next = editingErrorDetails(error, NOTE_FORM_FIELDS, normalizeNoteField);
      setCreateFieldErrors(next.fieldErrors);
      setCreateFormError(next.formError);
      throw error;
    } finally {
      setCreateSubmitting(false);
    }
  };

  const deleteNote = async (id: string) => {
    try {
      await apiRequest("notes/delete", { method: "POST", body: { note_id: id } });
      showToast(t("toast.noteDeleted"));
      setDeleteOpen(false);
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
    if (editSubmitting) return;
    setEditSubmitting(true);
    setEditFieldErrors({});
    setEditFormError(null);
    const noteId = detail.note_id ?? detail.id ?? "";
    try {
      await unwrapApiData(await apiRequest("notes/update", {
        method: "POST",
        body: { note_id: noteId, changes: editDraft },
      }));
      showToast(t("toast.noteUpdated"));
      setEditDirty(false);
      setEditMode(false);
      setEditFieldErrors({});
      setEditFormError(null);
      fetchNotes();
    } catch (error) {
      const next = editingErrorDetails(error, NOTE_FORM_FIELDS, normalizeNoteField);
      setEditFieldErrors(next.fieldErrors);
      setEditFormError(next.formError);
      throw error;
    } finally {
      setEditSubmitting(false);
    }
  };

  useEffect(() => {
    onDirtyChange?.(editDirty || createDirty);
  }, [createDirty, editDirty, onDirtyChange]);

  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const updateEditDraft = (next: NoteDraft) => {
    setEditDraft(next);
    setEditFieldErrors({});
    setEditFormError(null);
    const baseline = detail ? { title: detail.title ?? "", content: detail.content ?? "", tags: detail.tags ?? [], status: detail.status ?? "active" } : next;
    setEditDirty(JSON.stringify(next) !== JSON.stringify(baseline));
  };

  const updateNewNote = (next: NoteDraft) => {
    setNewNote(next);
    setCreateFieldErrors({});
    setCreateFormError(null);
    setCreateDirty(JSON.stringify(next) !== JSON.stringify({ title: "", content: "", tags: [], status: "active" }));
  };

  const resetNewNote = () => {
    setNewNote({ title: "", content: "", tags: [], status: "active" });
    setCreateDirty(false);
    setCreateFieldErrors({});
    setCreateFormError(null);
  };

  const requestDetail = (id: string) => {
    if (id === (detail?.note_id ?? detail?.id)) return;
    if (editDirty || createDirty) {
      setPendingSelection(id);
      setPendingClose("selection");
      return;
    }
    void fetchDetail(id);
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

  const executeBatchAction = async (action: "delete" | "archive") => {
    if (!selected.size) return;
    try {
      await apiRequest("notes/batch", { method: "POST", body: { note_ids: Array.from(selected), action } });
      showToast(t(action === "archive" ? "toast.batchArchived" : "toast.batchDeleted", String(selected.size)));
      setSelected(new Set());
      fetchNotes();
    } catch (e) { showToast(String(e), true); }
  };

  const batchAction = (action: "delete" | "archive") => {
    if (action === "delete" && selected.size >= BULK_CONFIRMATION_THRESHOLD) {
      setBatchDeleteOpen(true);
      return;
    }
    void executeBatchAction(action);
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
                    onClick={() => requestDetail(getNoteId(n))}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      event.preventDefault();
                      requestDetail(getNoteId(n));
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

      <EntityEditorSheet open={detail !== null} onOpenChange={(open) => { if (!open) { if (editDirty) setPendingClose("edit"); else setDetail(null); } }} title={detail?.title ?? ""} description={detail ? `${t("detail.updated")}: ${formatDashboardDate(detail.updated_at ?? detail.created_at, locale)}` : ""} mode={editMode ? "edit" : "view"} isDirty={editDirty} isSubmitting={editSubmitting} canSave onBeginEdit={() => { setEditFieldErrors({}); setEditFormError(null); setEditMode(true); }} onCancel={() => { if (detail) setEditDraft({ title: detail.title ?? "", content: detail.content ?? "", tags: detail.tags ?? [], status: detail.status ?? "active" }); setEditFieldErrors({}); setEditFormError(null); setEditMode(false); setEditDirty(false); }} onSave={saveEdit} labels={{ edit: t("detail.edit"), close: t("common.close"), cancel: t("common.cancel"), save: t("common.save"), saving: t("common.saving") }} view={detail ? <div className="flex flex-col gap-4 text-sm"><p className="whitespace-pre-wrap">{detail.content}</p><p>{(detail.tags ?? []).join(", ")}</p><p>v{detail.version ?? 1}</p>{detail.status !== "archived" ? <Button variant="secondary" size="sm" onClick={() => void archiveNote(detail.note_id ?? detail.id ?? "")}><Archive data-icon="inline-start" />{t("common.archive")}</Button> : null}<Button variant="destructive" size="sm" onClick={() => setDeleteOpen(true)}><Trash2 data-icon="inline-start" />{t("common.delete")}</Button></div> : null} form={<NoteForm value={editDraft} onChange={updateEditDraft} fieldErrors={editFieldErrors} formErrors={editFormError ? [editFormError] : []} disabled={editSubmitting} mode="edit" />} />

      <EntityCreateDialog open={showCreate} onOpenChange={(open) => { if (!open) { if (createDirty) setPendingClose("create"); else { resetNewNote(); setShowCreate(false); } } }} title={t("detail.newNote")} description={t("detail.newNote")} isDirty={createDirty} isSubmitting={createSubmitting} canSubmit={Boolean(newNote.title.trim())} onCancel={() => { resetNewNote(); setShowCreate(false); }} onSubmit={createNote} labels={{ close: t("common.close"), cancel: t("common.cancel"), submit: t("detail.create"), submitting: t("common.saving") }} form={<NoteForm value={newNote} onChange={updateNewNote} fieldErrors={createFieldErrors} formErrors={createFormError ? [createFormError] : []} disabled={createSubmitting} mode="create" />} />
      <UnsavedChangesDialog open={pendingClose !== null} title={t("config.unsaved.title")} description={t("config.unsaved.description")} keepEditingLabel={t("config.unsaved.keepEditing")} discardLabel={t("config.unsaved.discard")} onKeepEditing={() => { setPendingClose(null); setPendingSelection(null); }} onDiscard={() => { if (pendingClose === "selection" && pendingSelection) { setEditDirty(false); resetNewNote(); setEditMode(false); setShowCreate(false); void fetchDetail(pendingSelection); } else if (pendingClose === "edit") { setEditDirty(false); setEditMode(false); setDetail(null); } else { resetNewNote(); setShowCreate(false); } setPendingSelection(null); setPendingClose(null); }} />
      <DeleteConfirmDialog open={deleteOpen} title={t("common.delete")} description={detail?.title ?? ""} cancelLabel={t("common.cancel")} confirmLabel={t("common.delete")} onCancel={() => setDeleteOpen(false)} onConfirm={() => detail && void deleteNote(detail.note_id ?? detail.id ?? "")} />
      <DeleteConfirmDialog open={batchDeleteOpen} title={t("filter.deleteSelected")} description={t("filter.deleteSelected")} cancelLabel={t("common.cancel")} confirmLabel={t("common.delete")} confirmationRequirement={{ label: t("filter.deleteSelected"), expectedText: String(selected.size) }} onCancel={() => setBatchDeleteOpen(false)} onConfirm={() => { setBatchDeleteOpen(false); void executeBatchAction("delete"); }} />
    </PageFrame>
  );
}
