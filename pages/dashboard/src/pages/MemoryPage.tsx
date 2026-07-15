import { useState, useEffect, useCallback, useRef } from "react";
import { ScrollText, Archive, Trash2, X } from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData, normalizeImportance } from "@/lib/bridge";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { Badge } from "@/components/ui/Badge";
import { PageContent, PageFrame, PageHeader, PageToolbar } from "@/components/layout/PageLayout";
import { Checkbox } from "@/components/ui/checkbox";
import { selectionStateVariants } from "@/components/ui/selection-state";
import { Textarea } from "@/components/ui/textarea";
import { DeleteConfirmDialog } from "@/components/editing/DeleteConfirmDialog";
import { EntityEditorSheet } from "@/components/editing/EntityEditorSheet";
import { UnsavedChangesDialog } from "@/components/editing/UnsavedChangesDialog";
import { MemoryForm, type MemoryDraft } from "@/components/editing/forms/MemoryForm";
import { Field, FieldLabel } from "@/components/ui/field";
import { useEntityEditor } from "@/hooks/useEntityEditor";
import { BULK_CONFIRMATION_THRESHOLD } from "@/types/editing";
import { dashboardLocale, formatDashboardDate, formatDashboardNumber, translateEnum } from "@/lib/i18n";
import type { EntityNavigationTarget, MemoryItem } from "@/types";

interface MemoryPageProps {
  showToast: (msg: string, isError?: boolean) => void;
  navigationTarget?: EntityNavigationTarget | null;
  onDirtyChange?: (dirty: boolean) => void;
}

const ROW_HEIGHT = 56;
const SCROLL_BUFFER = 15;

const STATUS_LABELS: Record<string, string> = {};
const EDIT_FIELD_LABELS: Record<string, string> = {};

export function MemoryPage({ showToast, navigationTarget, onDirtyChange }: MemoryPageProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());

  // Init label maps with i18n
  STATUS_LABELS.all = t("filter.statusAll");
  STATUS_LABELS.active = t("filter.statusActive");
  STATUS_LABELS.archived = t("filter.statusArchived");
  STATUS_LABELS.deleted = t("filter.statusDeleted");
  EDIT_FIELD_LABELS.content = t("field.content");
  EDIT_FIELD_LABELS.importance = t("table.importance");
  EDIT_FIELD_LABELS.type = t("table.type");
  EDIT_FIELD_LABELS.status = t("table.status");
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [keyword, setKeyword] = useState("");
  const [session, setSession] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<MemoryItem | null>(null);
  const [editReason, setEditReason] = useState("");
  const [editDirty, setEditDirty] = useState(false);
  const [closeConfirmationOpen, setCloseConfirmationOpen] = useState(false);
  const [pendingSelection, setPendingSelection] = useState<string | null>(null);
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false);
  const detailRequestRef = useRef(0);

  const fetchMemories = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set("page", String(page));
      params.set("page_size", String(pageSize));
      if (keyword) params.set("keyword", keyword);
      if (session) params.set("session_id", session);
      if (statusFilter !== "all") params.set("status", statusFilter);

      const res = unwrapApiData(await apiRequest(`memories?${params.toString()}`));
      setItems((res.items ?? res.memories ?? []) as MemoryItem[]);
      setTotal(Number(res.total ?? 0));
    } catch (e) {
      showToast(String(e), true);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, keyword, session, statusFilter, showToast]);

  useEffect(() => { fetchMemories(); }, [fetchMemories]);

  const fetchDetail = useCallback(async (id: string) => {
    const requestId = ++detailRequestRef.current;
    try {
      const res = unwrapApiData(await apiRequest(`memory/detail?id=${id}`));
      if (requestId !== detailRequestRef.current) return;
      setDetail((res.memory ?? res) as MemoryItem);
    } catch (e) {
      if (requestId !== detailRequestRef.current) return;
      showToast(String(e), true);
    }
  }, [showToast]);

  useEffect(() => {
    if (!navigationTarget) return;
    if (editDirty && navigationTarget.id === detail?.id) return;
    void fetchDetail(navigationTarget.id);
  }, [fetchDetail, navigationTarget?.id, navigationTarget?.requestId]);

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selected.size === items.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(items.map((m) => m.id)));
    }
  };

  const executeBatchAction = async (action: "archive" | "delete") => {
    if (!selected.size) return;
    try {
      const body = { memory_ids: Array.from(selected), action };
      await apiRequest("memories/batch", { method: "POST", body });
      showToast(t(action === "archive" ? "toast.batchArchived" : "toast.batchDeleted", String(selected.size)));
      setSelected(new Set());
      fetchMemories();
    } catch (e) {
      showToast(String(e), true);
    }
  };

  const memoryDraft: MemoryDraft = {
    content: String(detail?.content ?? detail?.summary ?? detail?.text ?? ""),
    importance: Number(detail?.importance ?? 0),
    type: String(detail?.type ?? "fact"),
    status: String(detail?.status ?? "active"),
  };
  const editor = useEntityEditor<MemoryDraft>({
    entity: memoryDraft,
    onDirtyChange: setEditDirty,
    submit: async (draft) => {
      if (!detail) throw new Error("No memory selected");
      const response = unwrapApiData(await apiRequest("memory/update", {
        method: "POST",
        body: {
          memory_id: detail.id,
          changes: draft,
          reason: editReason,
        },
      }));
      const replacementId = typeof response.new_memory_id === "string" ? response.new_memory_id : null;
      if (replacementId) await fetchDetail(replacementId);
      showToast(t("edit.success"));
      void fetchMemories();
      return { entity: draft, revision: typeof response.revision === "string" ? response.revision : detail.id };
    },
  });

  useEffect(() => {
    onDirtyChange?.(editDirty);
  }, [editDirty, onDirtyChange]);

  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const requestDetail = (id: string) => {
    if (id === detail?.id) return;
    if (editor.isDirty) {
      setPendingSelection(id);
      setCloseConfirmationOpen(true);
      return;
    }
    void fetchDetail(id);
  };

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const scrollRef = useRef<HTMLDivElement>(null);

  const rowVirtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: SCROLL_BUFFER,
  });

  const statusVariant = (s: string) => {
    if (s === "active") return "default";
    if (s === "archived") return "secondary";
    if (s === "deleted") return "destructive";
    return "default";
  };

  const GRID_COLS = "40px 80px 1fr 96px 96px 96px 128px";

  const TableHeader = () => (
    <div
      className="sticky top-0 z-10 grid items-center border-b bg-background text-2xs font-medium uppercase tracking-wider text-muted-foreground"
      style={{ gridTemplateColumns: GRID_COLS }}
    >
      <div className="px-4 py-2.5">
        <Checkbox aria-label={selected.size === items.length && items.length > 0 ? t("memory.deselectAll") : t("memory.selectAll")} checked={selected.size === items.length && items.length > 0} onCheckedChange={toggleSelectAll} />
      </div>
      <div className="px-3 py-2.5">{t("table.id")}</div>
      <div className="px-3 py-2.5">{t("table.summary")}</div>
      <div className="px-3 py-2.5">{t("table.type")}</div>
      <div className="px-3 py-2.5">{t("table.importance")}</div>
      <div className="px-3 py-2.5">{t("table.status")}</div>
      <div className="px-3 py-2.5">{t("table.created")}</div>
    </div>
  );

  const VirtualRow = ({ index, size, start }: { index: number; size: number; start: number }) => {
    const m = items[index];
    return (
      <div
        style={{ position: "absolute", top: 0, left: 0, width: "100%", height: size, transform: `translateY(${start}px)` }}
      >
        <div
          data-state={selected.has(m.id) ? "selected" : undefined}
          className={cn(
            "grid cursor-pointer items-center border-b text-sm hover:bg-muted/50",
            selectionStateVariants({ kind: "row", selected: selected.has(m.id) }),
          )}
          style={{ gridTemplateColumns: GRID_COLS }}
          onClick={() => requestDetail(m.id)}
        >
          <div className="px-4 py-2.5" onClick={(e) => e.stopPropagation()}>
            <Checkbox aria-label={t("memory.selectItem", String(m.id))} checked={selected.has(m.id)} onCheckedChange={() => toggleSelect(m.id)} />
          </div>
          <div className="truncate px-3 py-2.5 font-mono text-xs text-muted-foreground">{String(m.id).slice(0, 8)}</div>
          <div className="truncate px-3 py-2.5">{String(m.summary ?? m.content ?? m.text ?? "")}</div>
          <div className="px-3 py-2.5"><Badge variant="secondary">{m.type ? translateEnum(t, "memory.type", m.type) : "--"}</Badge></div>
          <div className="px-3 py-2.5">
            <div className="flex items-center gap-2">
              <div className="h-1.5 flex-1 rounded-full bg-muted">
                <div className="h-1.5 rounded-full bg-primary transition-all"
                  style={{ width: `${normalizeImportance(m.importance ?? 0) * 10}%` }} />
              </div>
              <span className="text-xs tabular-nums text-muted-foreground">{formatDashboardNumber(normalizeImportance(m.importance ?? 0), locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</span>
            </div>
          </div>
          <div className="px-3 py-2.5"><Badge variant={statusVariant(String(m.status ?? "active"))}>{STATUS_LABELS[String(m.status ?? "active")] ?? m.status ?? t("filter.statusActive")}</Badge></div>
          <div className="px-3 py-2.5 text-xs text-muted-foreground">{formatDashboardDate(m.created_at, locale)}</div>
        </div>
      </div>
    );
  };

  return (
    <PageFrame variant="dense" aria-label={t("nav.memory")}>
      {/* Header */}
      <PageHeader title={t("nav.memory")} icon={<ScrollText size={18} />} />

      {/* Filters */}
      <PageToolbar>
        <Input
          placeholder={t("filter.keyword")}
          value={keyword}
          onChange={(e) => { setKeyword(e.target.value); setPage(1); }}
          className="max-w-xs"
        />
        <Input
          placeholder={t("filter.sessionId")}
          value={session}
          onChange={(e) => { setSession(e.target.value); setPage(1); }}
          className="max-w-[180px]"
        />
        <Select value={statusFilter} onValueChange={(v) => { setStatusFilter(v ?? "all"); setPage(1); }}>
          <SelectTrigger><span>{STATUS_LABELS[statusFilter] ?? statusFilter}</span></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t("filter.statusAll")}</SelectItem>
            <SelectItem value="active">{t("filter.statusActive")}</SelectItem>
            <SelectItem value="archived">{t("filter.statusArchived")}</SelectItem>
            <SelectItem value="deleted">{t("filter.statusDeleted")}</SelectItem>
          </SelectContent>
        </Select>
        <Select value={String(pageSize)} onValueChange={(v) => { if (v) { setPageSize(Number(v)); setPage(1); } }}>
          <SelectTrigger><span>{String(pageSize)}</span></SelectTrigger>
          <SelectContent>
            <SelectItem value="20">{t("common.perPage20")}</SelectItem>
            <SelectItem value="50">{t("common.perPage50")}</SelectItem>
            <SelectItem value="100">{t("common.perPage100")}</SelectItem>
          </SelectContent>
        </Select>
      </PageToolbar>

      <PageContent width="full" className="flex flex-col overflow-hidden p-0 sm:p-0 lg:p-0">

      {/* Table with virtual scroll */}
      <div ref={scrollRef} className="flex-1 overflow-auto">
        {loading && items.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">{t("table.loading")}</div>
        ) : !loading && items.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">{t("table.noData")}</div>
        ) : (
          <div className="flex flex-col">
            <TableHeader />
            <div style={{ height: rowVirtualizer.getTotalSize(), position: "relative", width: "100%" }}>
              {rowVirtualizer.getVirtualItems().map((vRow) => (
                <VirtualRow key={vRow.key} index={vRow.index} size={vRow.size} start={vRow.start} />
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between border-t px-6 py-2.5">
        <span className="text-xs text-muted-foreground">{t("detail.pageInfo").replace("{page}", String(page)).replace("{total}", String(totalPages)).replace("{totalItems}", String(total))}</span>
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>{t("pagination.prev")}</Button>
          <Button variant="secondary" size="sm" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>{t("pagination.next")}</Button>
        </div>
      </div>
      </PageContent>

      {/* Batch Bar */}
      {selected.size > 0 && (
        <div className="flex items-center gap-3 border-t bg-muted/50 px-6 py-2.5 animate-slide-up">
          <span className="text-sm font-medium text-foreground">{t("select.selected", String(selected.size))}</span>
          <Button variant="secondary" size="sm" onClick={() => executeBatchAction("archive")}><Archive size={14} />{t("edit.statusArchived")}</Button>
          <Button variant="destructive" size="sm" onClick={() => setBatchDeleteOpen(true)}><Trash2 size={14} />{t("filter.deleteSelected")}</Button>
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}><X size={14} />{t("common.clear")}</Button>
        </div>
      )}

      <EntityEditorSheet
        open={detail !== null}
        onOpenChange={(open) => {
          if (open || !detail) return;
          if (editor.isDirty) setCloseConfirmationOpen(true);
          else setDetail(null);
        }}
        title={t("detail.title")}
        description={detail ? formatDashboardDate(detail.created_at, locale) : ""}
        mode={editor.mode}
        isDirty={editor.isDirty}
        isSubmitting={editor.isSubmitting}
        canSave
        onBeginEdit={editor.beginEdit}
        onCancel={editor.cancel}
        onSave={() => void editor.save()}
        labels={{ edit: t("detail.edit"), close: t("common.close"), cancel: t("common.cancel"), save: t("common.save"), saving: t("common.saving") }}
        view={detail ? <div className="flex flex-col gap-4"><div><p className="text-xs text-muted-foreground">{t("table.id")}</p><p className="font-mono text-sm">{detail.id}</p></div><div><p className="text-xs text-muted-foreground">{t("detail.content")}</p><p className="whitespace-pre-wrap text-sm">{memoryDraft.content}</p></div><div className="grid grid-cols-2 gap-3 text-sm"><p>{t("table.type")}: {translateEnum(t, "memory.type", memoryDraft.type)}</p><p>{t("table.importance")}: {formatDashboardNumber(normalizeImportance(memoryDraft.importance), locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}</p><p>{t("table.status")}: {STATUS_LABELS[memoryDraft.status] ?? memoryDraft.status}</p><p>{t("table.created")}: {formatDashboardDate(detail.created_at, locale)}</p></div></div> : null}
        form={<><MemoryForm value={editor.draft} onChange={(draft) => { (Object.keys(draft) as (keyof MemoryDraft)[]).forEach((field) => editor.setField(field, draft[field])); }} fieldErrors={editor.fieldErrors} disabled={editor.isSubmitting} mode="edit" /><Field data-disabled={editor.isSubmitting}><FieldLabel htmlFor="memory-edit-reason">{t("edit.reason")}</FieldLabel><Input id="memory-edit-reason" placeholder={t("edit.reason")} disabled={editor.isSubmitting} value={editReason} onChange={(event) => setEditReason(event.currentTarget.value)} /></Field></>}
      />
      <UnsavedChangesDialog open={closeConfirmationOpen} title={t("config.unsaved.title")} description={t("config.unsaved.description")} keepEditingLabel={t("config.unsaved.keepEditing")} discardLabel={t("config.unsaved.discard")} onKeepEditing={() => { setCloseConfirmationOpen(false); setPendingSelection(null); }} onDiscard={() => { const next = pendingSelection; setCloseConfirmationOpen(false); setPendingSelection(null); editor.cancel(); if (next) void fetchDetail(next); else setDetail(null); }} />
      <DeleteConfirmDialog open={batchDeleteOpen} title={t("filter.deleteSelected")} description={t("config.unsaved.description")} cancelLabel={t("common.cancel")} confirmLabel={t("common.delete")} confirmationRequirement={selected.size >= BULK_CONFIRMATION_THRESHOLD ? { label: t("filter.deleteSelected"), expectedText: String(selected.size) } : undefined} onCancel={() => setBatchDeleteOpen(false)} onConfirm={() => { setBatchDeleteOpen(false); void executeBatchAction("delete"); }} />
    </PageFrame>
  );
}
