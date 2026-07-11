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
import { Textarea } from "@/components/ui/textarea";
import type { MemoryItem } from "@/types";

interface MemoryPageProps {
  showToast: (msg: string, isError?: boolean) => void;
}

const ROW_HEIGHT = 56;
const SCROLL_BUFFER = 15;

const STATUS_LABELS: Record<string, string> = {};
const EDIT_FIELD_LABELS: Record<string, string> = {};

export function MemoryPage({ showToast }: MemoryPageProps) {
  const { t } = useI18n();

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
  const [editData, setEditData] = useState<Record<string, string>>({});
  const [editField, setEditField] = useState("content");
  const [editReason, setEditReason] = useState("");

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

  const fetchDetail = async (id: string) => {
    try {
      const res = unwrapApiData(await apiRequest(`memory/detail?id=${id}`));
      setDetail((res.memory ?? res) as MemoryItem);
    } catch (e) {
      showToast(String(e), true);
    }
  };

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

  const batchAction = async (action: "archive" | "delete") => {
    if (!selected.size) return;
    try {
      const body = { memory_ids: Array.from(selected), action };
      await apiRequest("memories/batch", { method: "POST", body });
      showToast(`${action}d ${selected.size} memories`);
      setSelected(new Set());
      fetchMemories();
    } catch (e) {
      showToast(String(e), true);
    }
  };

  const saveEdit = async () => {
    if (!detail) return;
    try {
      await apiRequest("memory/update", {
        method: "POST",
        body: {
          memory_id: detail.id,
          field: editField,
          value: editData[editField] ?? "",
          reason: editReason,
        },
      });
      showToast(t("edit.success"));
      setDetail(null);
      fetchMemories();
    } catch (e) {
      showToast(String(e), true);
    }
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
        <Checkbox aria-label="Select all memories" checked={selected.size === items.length && items.length > 0} onCheckedChange={toggleSelectAll} />
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
          className="grid cursor-pointer items-center border-b text-sm transition-colors hover:bg-muted/50"
          style={{ gridTemplateColumns: GRID_COLS }}
          onClick={() => fetchDetail(m.id)}
        >
          <div className="px-4 py-2.5" onClick={(e) => e.stopPropagation()}>
            <Checkbox aria-label={`Select memory ${m.id}`} checked={selected.has(m.id)} onCheckedChange={() => toggleSelect(m.id)} />
          </div>
          <div className="truncate px-3 py-2.5 font-mono text-xs text-muted-foreground">{String(m.id).slice(0, 8)}</div>
          <div className="truncate px-3 py-2.5">{String(m.summary ?? m.content ?? m.text ?? "")}</div>
          <div className="px-3 py-2.5"><Badge variant="secondary">{String(m.type ?? "other").toUpperCase()}</Badge></div>
          <div className="px-3 py-2.5">
            <div className="flex items-center gap-2">
              <div className="h-1.5 flex-1 rounded-full bg-muted">
                <div className="h-1.5 rounded-full bg-primary transition-all"
                  style={{ width: `${normalizeImportance(m.importance ?? 0) * 10}%` }} />
              </div>
              <span className="text-xs tabular-nums text-muted-foreground">{normalizeImportance(m.importance ?? 0).toFixed(1)}</span>
            </div>
          </div>
          <div className="px-3 py-2.5"><Badge variant={statusVariant(String(m.status ?? "active"))}>{m.status ?? "active"}</Badge></div>
          <div className="px-3 py-2.5 text-xs text-muted-foreground">{String(m.created_at ?? "").slice(0, 10)}</div>
        </div>
      </div>
    );
  };

  return (
    <PageFrame variant="dense">
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
          <span className="text-sm font-medium text-foreground">{selected.size} selected</span>
          <Button variant="secondary" size="sm" onClick={() => batchAction("archive")}><Archive size={14} />{t("edit.statusArchived")}</Button>
          <Button variant="destructive" size="sm" onClick={() => batchAction("delete")}><Trash2 size={14} />{t("filter.deleteSelected")}</Button>
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}><X size={14} />{t("common.clear")}</Button>
        </div>
      )}

      {/* Detail Panel */}
      {detail && (
        <div className="fixed inset-y-0 right-0 z-40 w-[420px] overflow-y-auto border-l bg-popover text-popover-foreground shadow-lg animate-slide-in-right">
          <div className="flex items-center justify-between border-b px-5 py-3">
            <h3 className="text-sm font-semibold">{t("detail.title")}</h3>
            <Button variant="ghost" size="sm" onClick={() => setDetail(null)}><X size={16} /></Button>
          </div>
          <div className="p-5 space-y-4">
            <div>
              <label className="text-xs font-medium text-muted-foreground">{t("table.id")}</label>
              <p className="font-mono text-sm">{detail.id}</p>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Content</label>
              <p className="text-sm whitespace-pre-wrap">{String(detail.content ?? detail.summary ?? detail.text ?? "")}</p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className="text-xs font-medium text-muted-foreground">{t("table.type")}</label><p className="text-sm">{String(detail.type ?? "")}</p></div>
              <div><label className="text-xs font-medium text-muted-foreground">{t("table.importance")}</label><p className="text-sm">{normalizeImportance(Number(detail.importance ?? 0)).toFixed(1)}</p></div>
              <div><label className="text-xs font-medium text-muted-foreground">{t("table.status")}</label><p className="text-sm">{String(detail.status ?? "")}</p></div>
              <div><label className="text-xs font-medium text-muted-foreground">{t("table.created")}</label><p className="text-sm">{String(detail.created_at ?? "")}</p></div>
            </div>

            {/* Edit Form */}
            <div className="space-y-3 border-t pt-4">
              <h4 className="text-sm font-semibold">{t("detail.edit")}</h4>
              <Select value={editField} onValueChange={(v) => v && setEditField(v)}>
                <SelectTrigger><span>{EDIT_FIELD_LABELS[editField] ?? editField}</span></SelectTrigger>
                <SelectContent>
                  <SelectItem value="content">{t("field.content")}</SelectItem>
                  <SelectItem value="importance">{t("table.importance")}</SelectItem>
                  <SelectItem value="type">{t("table.type")}</SelectItem>
                  <SelectItem value="status">{t("table.status")}</SelectItem>
                </SelectContent>
              </Select>
              {editField === "content" && (
                <Textarea
                  rows={4}
                  placeholder={t("edit.newContentPh")}
                  value={editData.content ?? ""}
                  onChange={(e) => setEditData({ ...editData, content: e.target.value })}
                />
              )}
              {editField === "importance" && (
                <Input type="number" min="0" max="10" step="0.1" value={editData.importance ?? "5"}
                  onChange={(e) => setEditData({ ...editData, importance: e.target.value })} />
              )}
              {editField === "type" && (
                <Input placeholder={t("edit.typePh")} value={editData.type ?? ""}
                  onChange={(e) => setEditData({ ...editData, type: e.target.value })} />
              )}
              {editField === "status" && (
                <Select value={editData.status ?? "active"} onValueChange={(v) => v && setEditData({ ...editData, status: v })}>
                  <SelectTrigger><span>{STATUS_LABELS[editData.status ?? "active"] ?? (editData.status ?? "active")}</span></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="active">{t("filter.statusActive")}</SelectItem>
                    <SelectItem value="archived">{t("filter.statusArchived")}</SelectItem>
                    <SelectItem value="deleted">{t("filter.statusDeleted")}</SelectItem>
                  </SelectContent>
                </Select>
              )}
              <Input placeholder={t("edit.reason")} value={editReason} onChange={(e) => setEditReason(e.target.value)} />
              <div className="flex gap-2">
                <Button size="sm" onClick={saveEdit}>{t("common.save")}</Button>
                <Button variant="secondary" size="sm" onClick={() => setDetail(null)}>{t("common.cancel")}</Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </PageFrame>
  );
}
