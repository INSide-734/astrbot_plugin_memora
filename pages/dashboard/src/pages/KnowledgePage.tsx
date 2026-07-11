import { useState, useEffect, useCallback } from "react";
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
import { Textarea } from "@/components/ui/textarea";

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
const PAGE_SIZE = 100;

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
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
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
        {search ? <div className="border-b px-6 py-2 text-sm text-muted-foreground">Showing {entries.length} of {total} search results</div> : null}
        {loading ? <p className="px-6 py-12 text-center text-sm text-muted-foreground">Loading...</p> :
         entries.length === 0 ? <p className="px-6 py-12 text-center text-sm text-muted-foreground">{t("table.noData")}</p> : (
          <Table>
            <TableHeader className="sticky top-0 bg-background">
              <TableRow className="text-left text-xs font-medium uppercase text-muted-foreground">
                <TableHead className="w-10 px-4"><Checkbox aria-label="Select all knowledge entries" checked={selected.size === entries.length && entries.length > 0} onCheckedChange={toggleSelectAll} /></TableHead>
                <TableHead className="px-4">{t("table.title")}</TableHead><TableHead>{t("table.category")}</TableHead>
                <TableHead>{t("table.confidence")}</TableHead><TableHead>{t("table.updated")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((e) => (
                <TableRow key={getEntryId(e)} className="cursor-pointer text-sm"
                  onClick={() => fetchDetail(getEntryId(e))}>
                  <TableCell className="px-4" onClick={(ev) => ev.stopPropagation()}>
                    <Checkbox aria-label={`Select knowledge entry ${e.title}`} checked={selected.has(getEntryId(e))} onCheckedChange={() => toggleSelect(getEntryId(e))} />
                  </TableCell>
                  <TableCell className="px-4 font-medium">
                    <Button variant="link" className="h-auto p-0 font-medium" aria-label={`Open knowledge entry ${e.title}`} onClick={(event) => { event.stopPropagation(); fetchDetail(getEntryId(e)); }}>{e.title}</Button>
                  </TableCell>
                  <TableCell><Badge variant="secondary">{e.category ?? "fact"}</Badge></TableCell>
                  <TableCell className="text-xs tabular-nums">{Number(e.confidence ?? 0).toFixed(2)}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{String(e.updated_at ?? e.created_at ?? "").slice(0, 10)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </PageContent>

      {!search && <nav className="flex min-h-12 shrink-0 items-center justify-between border-t bg-background px-4 py-2 sm:px-5 lg:px-6" aria-label="Knowledge pagination">
        <Button variant="outline" size="sm" aria-label="Previous page" disabled={Boolean(search) || page === 0} onClick={() => changePage(Math.max(0, page - 1))}>Previous</Button>
        <span className="text-sm text-muted-foreground">Page {page + 1} of {totalPages}</span>
        <Button variant="outline" size="sm" aria-label="Next page" disabled={Boolean(search) || page + 1 >= totalPages} onClick={() => changePage(page + 1)}>Next</Button>
      </nav>}

      {/* Batch bar */}
      {selected.size > 0 && (
        <PageToolbar className="border-b-0 border-t bg-muted/40 animate-slide-up">
          <span className="text-sm font-medium">{selected.size} selected</span>
          <Button variant="destructive" size="sm" onClick={batchDelete}><Trash2 data-icon="inline-start" />Delete</Button>
          <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}><X data-icon="inline-start" />{t("common.clear")}</Button>
        </PageToolbar>
      )}

      {/* Detail panel */}
      <Sheet open={detail !== null} onOpenChange={(open) => { if (!open) setDetail(null); }}>
        {detail && (
        <SheetContent>
          <SheetHeader>
            <SheetTitle>{detail.title}</SheetTitle>
            <SheetDescription>{detail.category ?? "fact"}</SheetDescription>
          </SheetHeader>
          <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-5">
            <div className="grid grid-cols-2 gap-3">
              <div><label className="text-xs font-medium text-muted-foreground">{t("table.category")}</label><p className="text-sm">{detail.category ?? "fact"}</p></div>
              <div><label className="text-xs font-medium text-muted-foreground">{t("table.confidence")}</label><p className="text-sm">{Number(detail.confidence ?? 0).toFixed(2)}</p></div>
              <div><label className="text-xs font-medium text-muted-foreground">{t("table.accessCount")}</label><p className="text-sm">{detail.access_count ?? 0}</p></div>
            </div>
            {detail.content && (
              <div><label className="text-xs font-medium text-muted-foreground">Content</label><p className="mt-1 whitespace-pre-wrap text-sm">{detail.content}</p></div>
            )}

            {/* Edit form */}
            <div className="flex flex-col gap-3 border-t pt-4">
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
                <Textarea
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
            </div>
          </div>
          <SheetFooter>
            <Button variant="secondary" size="sm" onClick={() => setDetail(null)}>{t("common.cancel")}</Button>
            <Button variant="destructive" size="sm" onClick={() => deleteEntry(detail.entry_id ?? detail.id ?? "")}><Trash2 data-icon="inline-start" />Delete</Button>
            <Button size="sm" onClick={saveEdit}><Pencil data-icon="inline-start" />{t("common.save")}</Button>
          </SheetFooter>
        </SheetContent>
        )}
      </Sheet>

      {/* Create modal */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="sm:max-w-lg" showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>{t("detail.newEntry")}</DialogTitle>
          </DialogHeader>
            <div className="flex flex-col gap-4">
              <Input placeholder={t("placeholder.title")} value={newEntry.title} onChange={(e) => setNewEntry({ ...newEntry, title: e.target.value })} />
              <Textarea className="resize-none" rows={4}
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
            </div>
              <DialogFooter>
                <Button variant="secondary" size="sm" onClick={() => setShowCreate(false)}>{t("common.cancel")}</Button>
                <Button size="sm" onClick={createEntry}>{t("detail.create")}</Button>
              </DialogFooter>
        </DialogContent>
      </Dialog>
    </PageFrame>
  );
}
