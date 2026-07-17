import { useState, useEffect, useCallback, useRef } from "react";
import { BookOpen, Plus, Search, Trash2, Pencil, X } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { Badge } from "@/components/ui/Badge";
import { PageContent, PageFrame, PageHeader, PageToolbar } from "@/components/layout/PageLayout";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/Table";
import { Checkbox } from "@/components/ui/checkbox";
import { dashboardLocale, formatDashboardDate, formatDashboardNumber } from "@/lib/i18n";
import { Textarea } from "@/components/ui/textarea";
import { EntityCreateDialog } from "@/components/editing/EntityCreateDialog";
import { EntityEditorSheet } from "@/components/editing/EntityEditorSheet";
import { UnsavedChangesDialog } from "@/components/editing/UnsavedChangesDialog";
import { DeleteConfirmDialog } from "@/components/editing/DeleteConfirmDialog";
import { KnowledgeForm, type KnowledgeDraft } from "@/components/editing/forms/KnowledgeForm";
import { BULK_CONFIRMATION_THRESHOLD, editingErrorDetails, type FieldErrors } from "@/types/editing";
import type { EntityNavigationTarget } from "@/types";

interface KnowledgePageProps {
  showToast: (msg: string, isError?: boolean) => void;
  navigationTarget?: EntityNavigationTarget | null;
  onDirtyChange?: (dirty: boolean) => void;
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
const PAGE_SIZE = 100;
const KNOWLEDGE_FORM_FIELDS = ["title", "content", "category", "confidence", "tags"] as const;
const normalizeKnowledgeField = (name: string) => /^tags\.\d+$/.test(name) ? "tags" : name;

export function KnowledgePage({ showToast, navigationTarget, onDirtyChange }: KnowledgePageProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());

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
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<KnowledgeEntry | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newEntry, setNewEntry] = useState<KnowledgeDraft>({ title: "", content: "", category: "fact", confidence: 0, tags: [] });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [editDraft, setEditDraft] = useState<KnowledgeDraft>({ title: "", content: "", category: "fact", confidence: 0, tags: [] });
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

  const fetchEntries = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set("limit", String(PAGE_SIZE));
      params.set("offset", String(page * PAGE_SIZE));
      if (category) params.set("category", category);
      if (search) {
        const searchParams = new URLSearchParams({ query: search, limit: String(PAGE_SIZE) });
        if (category) searchParams.set("category", category);
        const res = unwrapApiData(await apiRequest(`knowledge/search?${searchParams.toString()}`));
        const nextEntries = (res.entries ?? res.items ?? []) as KnowledgeEntry[];
        setEntries(nextEntries);
        setTotal(Number(res.total ?? nextEntries.length));
      } else {
        const res = unwrapApiData(await apiRequest(`knowledge?${params.toString()}`));
        const nextEntries = (res.entries ?? res.items ?? []) as KnowledgeEntry[];
        const nextTotal = Number(res.total ?? nextEntries.length);
        if (page > 0 && nextEntries.length === 0 && nextTotal <= page * PAGE_SIZE) {
          setSelected(new Set());
          setTotal(nextTotal);
          setPage(Math.max(0, Math.ceil(nextTotal / PAGE_SIZE) - 1));
          return;
        }
        setEntries(nextEntries);
        setTotal(nextTotal);
      }
    } catch (e) { showToast(String(e), true); } finally { setLoading(false); }
  }, [search, category, page, showToast]);

  useEffect(() => { fetchEntries(); }, [fetchEntries]);

  const fetchDetail = useCallback(async (id: string) => {
    const requestId = ++detailRequestRef.current;
    try {
      const res = unwrapApiData(await apiRequest(`knowledge/detail?entry_id=${id}`));
      if (requestId !== detailRequestRef.current) return;
      const entry = (res.entry ?? res) as KnowledgeEntry;
      setDetail(entry);
      setEditDraft({ title: entry.title ?? "", content: entry.content ?? "", category: entry.category ?? "fact", confidence: Number(entry.confidence ?? 0), tags: (entry as KnowledgeEntry & { tags?: string[] }).tags ?? [] });
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
    if ((editDirty || createDirty) && navigationTarget.id === (detail?.entry_id ?? detail?.id)) return;
    void fetchDetail(navigationTarget.id);
  }, [fetchDetail, navigationTarget?.id, navigationTarget?.requestId]);

  const createEntry = async () => {
    if (createSubmitting) return;
    setCreateSubmitting(true);
    setCreateFieldErrors({});
    setCreateFormError(null);
    try {
      await unwrapApiData(await apiRequest("knowledge/create", { method: "POST", body: newEntry }));
      showToast(t("toast.entryCreated"));
      setShowCreate(false);
      setNewEntry({ title: "", content: "", category: "fact", confidence: 0, tags: [] });
      setCreateDirty(false);
      setCreateFieldErrors({});
      setCreateFormError(null);
      fetchEntries();
    } catch (error) {
      const next = editingErrorDetails(error, KNOWLEDGE_FORM_FIELDS, normalizeKnowledgeField);
      setCreateFieldErrors(next.fieldErrors);
      setCreateFormError(next.formError);
      throw error;
    } finally {
      setCreateSubmitting(false);
    }
  };

  const deleteEntry = async (id: string) => {
    try {
      await apiRequest("knowledge/delete", { method: "POST", body: { entry_id: id } });
      showToast(t("toast.entryDeleted"));
      setDeleteOpen(false);
      setDetail(null);
      fetchEntries();
    } catch (e) { showToast(String(e), true); }
  };

  const saveEdit = async () => {
    if (!detail) return;
    if (editSubmitting) return;
    setEditSubmitting(true);
    setEditFieldErrors({});
    setEditFormError(null);
    try {
      await unwrapApiData(await apiRequest("knowledge/update", {
        method: "POST",
        body: { entry_id: detail.entry_id ?? detail.id, changes: editDraft },
      }));
      showToast(t("toast.entryUpdated"));
      setEditDirty(false);
      setEditMode(false);
      setEditFieldErrors({});
      setEditFormError(null);
      fetchEntries();
    } catch (error) {
      const next = editingErrorDetails(error, KNOWLEDGE_FORM_FIELDS, normalizeKnowledgeField);
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

  const updateEditDraft = (next: KnowledgeDraft) => {
    setEditDraft(next);
    setEditFieldErrors({});
    setEditFormError(null);
    const baseline = detail ? { title: detail.title ?? "", content: detail.content ?? "", category: detail.category ?? "fact", confidence: Number(detail.confidence ?? 0), tags: (detail as KnowledgeEntry & { tags?: string[] }).tags ?? [] } : next;
    setEditDirty(JSON.stringify(next) !== JSON.stringify(baseline));
  };

  const updateNewEntry = (next: KnowledgeDraft) => {
    setNewEntry(next);
    setCreateFieldErrors({});
    setCreateFormError(null);
    setCreateDirty(JSON.stringify(next) !== JSON.stringify({ title: "", content: "", category: "fact", confidence: 0, tags: [] }));
  };

  const resetNewEntry = () => {
    setNewEntry({ title: "", content: "", category: "fact", confidence: 0, tags: [] });
    setCreateDirty(false);
    setCreateFieldErrors({});
    setCreateFormError(null);
  };

  const requestDetail = (id: string) => {
    if (id === (detail?.entry_id ?? detail?.id)) return;
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
    if (selected.size === entries.length) { setSelected(new Set()); }
    else { setSelected(new Set(entries.map((e) => e.entry_id ?? e.id ?? ""))); }
  };

  const executeBatchDelete = async () => {
    if (!selected.size) return;
    try {
      await apiRequest("knowledge/batch", { method: "POST", body: { entry_ids: Array.from(selected), action: "delete" } });
      showToast(t("toast.batchDeleted", String(selected.size)));
      setSelected(new Set());
      fetchEntries();
    } catch (e) { showToast(String(e), true); }
  };

  const batchDelete = () => {
    if (selected.size >= BULK_CONFIRMATION_THRESHOLD) {
      setBatchDeleteOpen(true);
      return;
    }
    void executeBatchDelete();
  };

  const getEntryId = (e: KnowledgeEntry) => e.entry_id ?? e.id ?? "";
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const changePage = (nextPage: number) => {
    setSelected(new Set());
    setPage(nextPage);
  };

  return (
    <PageFrame variant="dense" aria-label={t("nav.knowledge")}>
      <PageHeader
        title={t("nav.knowledge")}
        icon={<BookOpen />}
        actions={<Button size="sm" onClick={() => setShowCreate(true)}><Plus data-icon="inline-start" />{t("kb.newEntry")}</Button>}
      />

      <PageToolbar>
        <div className="relative min-w-0 max-w-sm flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <Input placeholder={t("kb.searchPh")} value={search}
            onChange={(e) => { setSelected(new Set()); setPage(0); setSearch(e.target.value); }} className="pl-9" />
        </div>
        <div className="ml-auto">
          <Select value={category} onValueChange={(v) => { setSelected(new Set()); setPage(0); setCategory(v ?? ""); }}>
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
      </PageToolbar>

      <PageContent width="full" className="p-0">
        {search ? <div className="border-b px-6 py-2 text-sm text-muted-foreground">{t("kb.searchResults", String(entries.length), String(total))}</div> : null}
        {loading ? <p className="px-6 py-12 text-center text-sm text-muted-foreground">{t("common.loading")}</p> :
         entries.length === 0 ? <p className="px-6 py-12 text-center text-sm text-muted-foreground">{t("table.noData")}</p> : (
          <Table>
            <TableHeader className="sticky top-0 bg-background">
              <TableRow className="text-left text-xs font-medium uppercase text-muted-foreground">
                <TableHead className="w-10 px-4"><Checkbox aria-label={selected.size === entries.length && entries.length > 0 ? t("kb.deselectAll") : t("kb.selectAll")} checked={selected.size === entries.length && entries.length > 0} onCheckedChange={toggleSelectAll} /></TableHead>
                <TableHead className="px-4">{t("table.title")}</TableHead><TableHead>{t("table.category")}</TableHead>
                <TableHead>{t("table.confidence")}</TableHead><TableHead>{t("table.updated")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((e) => (
                <TableRow key={getEntryId(e)} data-state={selected.has(getEntryId(e)) ? "selected" : undefined} className="cursor-pointer text-sm"
                  onClick={() => requestDetail(getEntryId(e))}>
                  <TableCell className="px-4" onClick={(ev) => ev.stopPropagation()}>
                    <Checkbox aria-label={t("kb.selectEntry", e.title)} checked={selected.has(getEntryId(e))} onCheckedChange={() => toggleSelect(getEntryId(e))} />
                  </TableCell>
                  <TableCell className="px-4 font-medium">
                    <Button variant="link" className="h-auto p-0 font-medium" aria-label={t("kb.openEntry", e.title)} onClick={(event) => { event.stopPropagation(); requestDetail(getEntryId(e)); }}>{e.title}</Button>
                  </TableCell>
                  <TableCell><Badge variant="secondary">{CAT_LABELS[e.category ?? "fact"] ?? e.category ?? t("category.fact")}</Badge></TableCell>
                  <TableCell className="text-xs tabular-nums">{formatDashboardNumber(e.confidence ?? 0, locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{formatDashboardDate(e.updated_at ?? e.created_at, locale)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </PageContent>

      {!search && <nav className="flex min-h-12 shrink-0 items-center justify-between border-t bg-background px-4 py-2 sm:px-5 lg:px-6" aria-label={t("kb.pagination")}>
        <Button variant="outline" size="sm" aria-label={t("pagination.previousPage")} disabled={Boolean(search) || page === 0} onClick={() => changePage(Math.max(0, page - 1))}>{t("pagination.prev")}</Button>
        <span className="text-sm text-muted-foreground">{t("pagination.pageOf", String(page + 1), String(totalPages))}</span>
        <Button variant="outline" size="sm" aria-label={t("pagination.nextPage")} disabled={Boolean(search) || page + 1 >= totalPages} onClick={() => changePage(page + 1)}>{t("pagination.next")}</Button>
      </nav>}

      {/* Batch bar */}
      {selected.size > 0 && (
        <PageToolbar className="border-b-0 border-t bg-muted/40 animate-slide-up">
          <span className="text-sm font-medium">{t("select.selected", String(selected.size))}</span>
          <Button variant="destructive" size="sm" onClick={batchDelete}><Trash2 data-icon="inline-start" />{t("common.delete")}</Button>
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}><X data-icon="inline-start" />{t("common.clear")}</Button>
        </PageToolbar>
      )}

      <EntityEditorSheet open={detail !== null} onOpenChange={(open) => { if (!open) { if (editDirty) setPendingClose("edit"); else setDetail(null); } }} title={detail?.title ?? ""} description={detail ? (CAT_LABELS[detail.category ?? "fact"] ?? detail.category ?? "") : ""} mode={editMode ? "edit" : "view"} isDirty={editDirty} isSubmitting={editSubmitting} canSave onBeginEdit={() => { setEditFieldErrors({}); setEditFormError(null); setEditMode(true); }} onCancel={() => { if (detail) setEditDraft({ title: detail.title ?? "", content: detail.content ?? "", category: detail.category ?? "fact", confidence: Number(detail.confidence ?? 0), tags: (detail as KnowledgeEntry & { tags?: string[] }).tags ?? [] }); setEditFieldErrors({}); setEditFormError(null); setEditMode(false); setEditDirty(false); }} onSave={saveEdit} labels={{ edit: t("detail.edit"), close: t("common.close"), cancel: t("common.cancel"), save: t("common.save"), saving: t("common.saving") }} view={detail ? <div className="flex flex-col gap-4 text-sm"><p>{detail.content}</p><p>{t("table.category")}: {CAT_LABELS[detail.category ?? "fact"] ?? detail.category}</p><p>{t("table.confidence")}: {formatDashboardNumber(detail.confidence ?? 0, locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</p><Button variant="destructive" size="sm" onClick={() => setDeleteOpen(true)}><Trash2 data-icon="inline-start" />{t("common.delete")}</Button></div> : null} form={<KnowledgeForm value={editDraft} onChange={updateEditDraft} fieldErrors={editFieldErrors} formErrors={editFormError ? [editFormError] : []} disabled={editSubmitting} mode="edit" />} />
      <EntityCreateDialog open={showCreate} onOpenChange={(open) => { if (!open) { if (createDirty) setPendingClose("create"); else { resetNewEntry(); setShowCreate(false); } } }} title={t("detail.newEntry")} description={t("detail.newEntry")} isDirty={createDirty} isSubmitting={createSubmitting} canSubmit={Boolean(newEntry.title.trim())} onCancel={() => { resetNewEntry(); setShowCreate(false); }} onSubmit={createEntry} labels={{ close: t("common.close"), cancel: t("common.cancel"), submit: t("detail.create"), submitting: t("common.saving") }} form={<KnowledgeForm value={newEntry} onChange={updateNewEntry} fieldErrors={createFieldErrors} formErrors={createFormError ? [createFormError] : []} disabled={createSubmitting} mode="create" />} />
      <UnsavedChangesDialog open={pendingClose !== null} title={t("config.unsaved.title")} description={t("config.unsaved.description")} keepEditingLabel={t("config.unsaved.keepEditing")} discardLabel={t("config.unsaved.discard")} onKeepEditing={() => { setPendingClose(null); setPendingSelection(null); }} onDiscard={() => { if (pendingClose === "selection" && pendingSelection) { setEditDirty(false); resetNewEntry(); setEditMode(false); setShowCreate(false); void fetchDetail(pendingSelection); } else if (pendingClose === "edit") { setEditDirty(false); setEditMode(false); setDetail(null); } else { resetNewEntry(); setShowCreate(false); } setPendingSelection(null); setPendingClose(null); }} />
      <DeleteConfirmDialog open={deleteOpen} title={t("common.delete")} description={detail?.title ?? ""} cancelLabel={t("common.cancel")} confirmLabel={t("common.delete")} onCancel={() => setDeleteOpen(false)} onConfirm={() => detail && void deleteEntry(detail.entry_id ?? detail.id ?? "")} />
      <DeleteConfirmDialog open={batchDeleteOpen} title={t("filter.deleteSelected")} description={t("filter.deleteSelected")} cancelLabel={t("common.cancel")} confirmLabel={t("common.delete")} confirmationRequirement={{ label: t("filter.deleteSelected"), expectedText: String(selected.size) }} onCancel={() => setBatchDeleteOpen(false)} onConfirm={() => { setBatchDeleteOpen(false); void executeBatchDelete(); }} />
    </PageFrame>
  );
}
