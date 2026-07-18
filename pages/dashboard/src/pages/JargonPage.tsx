import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { MessageCircleCode, RefreshCw, Sparkles, Hash, Users, Zap, Loader2 } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { useGroups } from "@/hooks/useGroups";
import { apiPost, apiRequest, unwrapApiData } from "@/lib/bridge";
import { ApiRequestError, editingErrorDetails, type FieldErrors } from "@/types/editing";
import type { JargonCandidate, JargonDraft, JargonMeaning } from "@/types";
import { JargonForm } from "@/components/editing/forms/JargonForm";
import { EntityCreateDialog } from "@/components/editing/EntityCreateDialog";
import { EntityEditorSheet } from "@/components/editing/EntityEditorSheet";
import { DetailField, DetailGrid, DetailSection, DetailText } from "@/components/editing/EntityDetail";
import { DeleteConfirmDialog } from "@/components/editing/DeleteConfirmDialog";
import { EditConflictDialog } from "@/components/editing/EditConflictDialog";
import { UnsavedChangesDialog } from "@/components/editing/UnsavedChangesDialog";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/Select";
import { Button } from "@/components/ui/Button";
import { DataTable } from "@/components/data-table/DataTable";
import { actionsColumn, selectionColumn } from "@/components/data-table/data-table-columns";
import type { DataTableColumn, DataTableSort } from "@/components/data-table/table-types";
import { MetricGrid, PageContent, PageFrame, PageHeader, PageToolbar } from "@/components/layout/PageLayout";
import { dashboardLocale, formatDashboardDateTime, formatDashboardPercent } from "@/lib/i18n";

interface Props { showToast: (msg: string, isError?: boolean) => void; onDirtyChange?: (dirty: boolean) => void; }
type Entity = JargonMeaning & { context_examples?: string[]; revision?: string };
const JARGON_FORM_FIELDS = ["term", "group_id", "meaning", "confidence", "is_jargon", "is_confirmed", "is_global"] as const;
const DEFAULT_CANDIDATE_SORT: DataTableSort = { id: "score", desc: true };
const DEFAULT_MEANING_SORT: DataTableSort = { id: "updated_at", desc: true };
const draftOf = (e: Partial<Entity>, group_id: string): JargonDraft => ({ term: e.term ?? "", group_id: e.group_id ?? group_id, meaning: e.meaning ?? "", confidence: e.confidence ?? 0, is_jargon: e.is_jargon ?? true, is_confirmed: e.is_confirmed ?? true, is_global: e.is_global ?? false });
const identity = (e: Entity) => ({ term: e.term, group_id: e.group_id });
function validEntityEnvelope(value: unknown): { entity: Entity; revision: string } | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const envelope = value as Record<string, unknown>;
  const raw = envelope.entity;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const entity = raw as Record<string, unknown>;
  const revision = typeof envelope.revision === "string" && envelope.revision.trim() ? envelope.revision : entity.revision;
  if (typeof revision !== "string" || !revision.trim()) return null;
  if (typeof entity.term !== "string" || !entity.term.trim() || typeof entity.group_id !== "string" || !entity.group_id.trim()) return null;
  if (typeof entity.meaning !== "string" || !entity.meaning.trim()) return null;
  if (typeof entity.confidence !== "number" || !Number.isFinite(entity.confidence) || entity.confidence < 0 || entity.confidence > 1) return null;
  if (typeof entity.is_jargon !== "boolean" || typeof entity.is_confirmed !== "boolean" || typeof entity.is_global !== "boolean") return null;
  return { entity: { ...entity, revision } as Entity, revision };
}
function validDeleteIdentity(value: unknown): value is { term: string; group_id: string } {
  return Boolean(value && typeof value === "object" && !Array.isArray(value)
    && typeof (value as Record<string, unknown>).term === "string" && Boolean(((value as Record<string, unknown>).term as string).trim())
    && typeof (value as Record<string, unknown>).group_id === "string" && Boolean(((value as Record<string, unknown>).group_id as string).trim()));
}
function validateJargonBatch(value: unknown, items: Array<{ identity: { term: string; group_id: string } }>): Array<string> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid jargon batch response");
  const v = value as Record<string, unknown>;
  const counts = [v.total, v.succeeded_count, v.failed_count];
  if (!counts.every((n) => typeof n === "number" && Number.isInteger(n) && n >= 0)
    || v.total !== items.length || v.total !== (v.succeeded_count as number) + (v.failed_count as number)
    || !Array.isArray(v.succeeded_ids) || !Array.isArray(v.failures)
    || v.succeeded_ids.length !== v.succeeded_count || v.failures.length !== v.failed_count) throw new Error("Invalid jargon batch response");
  const allowed = new Set(items.map((item) => `${item.identity.term}:${item.identity.group_id}`));
  const failures = v.failures as unknown[];
  const succeeded = v.succeeded_ids.map((entry) => (entry && typeof entry === "object" ? (entry as Record<string, unknown>).identity ?? entry : null));
  const failed = failures.map((entry) => entry && typeof entry === "object" ? (entry as Record<string, unknown>).identity : null);
  const keys = [...succeeded, ...failed].map((entry) => {
    if (!validDeleteIdentity(entry)) throw new Error("Invalid jargon batch response");
    const key = `${entry.term}:${entry.group_id}`;
    if (!allowed.has(key)) throw new Error("Invalid jargon batch response");
    return key;
  });
  if (new Set(keys).size !== keys.length || failed.some((_, index) => {
    const failure = failures[index] as Record<string, unknown>;
    return typeof failure.code !== "string" || !failure.code.trim() || typeof failure.message !== "string" || !failure.message.trim();
  })) throw new Error("Invalid jargon batch response");
  return failed.map((entry) => `${(entry as { term: string }).term}:${(entry as { group_id: string }).group_id}`);
}
function ScoreBar({ score, locale }: { score: number; locale: string }) { return <div className="flex items-center gap-2"><div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.round(score * 100)}%` }} /></div><span className="text-xs tabular-nums text-muted-foreground">{formatDashboardPercent(score, locale, { maximumFractionDigits: 0 })}</span></div>; }

export function JargonPage({ showToast, onDirtyChange }: Props) {
  const { t, currentLang } = useI18n(); const locale = dashboardLocale(currentLang());
  const { groups, groupId, setGroupId } = useGroups();
  const [tab, setTab] = useState<"candidates" | "meanings">("candidates");
  const [candidates, setCandidates] = useState<JargonCandidate[]>([]); const [meanings, setMeanings] = useState<Entity[]>([]);
  const [candidateSort, setCandidateSort] = useState<DataTableSort>(DEFAULT_CANDIDATE_SORT);
  const [meaningSort, setMeaningSort] = useState<DataTableSort>(DEFAULT_MEANING_SORT);
  const [loading, setLoading] = useState(false); const [mining, setMining] = useState(false); const [selected, setSelected] = useState<string[]>([]);
  const [stats, setStats] = useState({ total_terms: 0, candidate_count: 0, store_confirmed: 0 });
  const [createOpen, setCreateOpen] = useState(false); const [createDraft, setCreateDraft] = useState<JargonDraft>(draftOf({}, groupId)); const [createError, setCreateError] = useState(""); const [createErrors, setCreateErrors] = useState<FieldErrors>({}); const [createPending, setCreatePending] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false); const [editorMode, setEditorMode] = useState<"view" | "edit">("view"); const [entity, setEntity] = useState<Entity | null>(null); const [editDraft, setEditDraft] = useState<JargonDraft>(draftOf({}, groupId)); const [editBase, setEditBase] = useState<JargonDraft>(editDraft); const [editError, setEditError] = useState(""); const [editErrors, setEditErrors] = useState<FieldErrors>({}); const [editPending, setEditPending] = useState(false);
  const [entityRevision, setEntityRevision] = useState<string | undefined>();
  const [deleteTarget, setDeleteTarget] = useState<Entity | null>(null); const [conflict, setConflict] = useState<{ remote: Entity; revision: string } | null>(null); const [unsaved, setUnsaved] = useState<"create" | "edit" | null>(null); const [batchPending, setBatchPending] = useState<string | null>(null);
  const [pendingBatchDeleteItems, setPendingBatchDeleteItems] = useState<Array<{ identity: { term: string; group_id: string }; expected_revision?: string }> | null>(null);
  const removeInFlight = useRef(false); const batchInFlight = useRef(false); const confirmInFlight = useRef(false); const listGeneration = useRef(0);
  const [confirmPending, setConfirmPending] = useState(false);
  const createDirty = createOpen && JSON.stringify(createDraft) !== JSON.stringify(draftOf({}, groupId)); const editDirty = editorOpen && editorMode === "edit" && JSON.stringify(editDraft) !== JSON.stringify(editBase);
  useEffect(() => onDirtyChange?.(createDirty || editDirty), [createDirty, editDirty, onDirtyChange]);
  const fetchCandidates = useCallback(async () => { if (!groupId) return; const request = ++listGeneration.current; setLoading(true); try { const query = new URLSearchParams({ group_id: groupId, limit: "50", sort_by: candidateSort.id, sort_order: candidateSort.desc ? "desc" : "asc" }); const r = unwrapApiData(await apiRequest(`jargon/candidates?${query.toString()}`)); if (request === listGeneration.current) setCandidates((r.candidates ?? []) as JargonCandidate[]); } catch (e) { if (request === listGeneration.current) showToast(String(e), true); } finally { if (request === listGeneration.current) setLoading(false); } }, [candidateSort, groupId, showToast]);
  const fetchMeanings = useCallback(async () => { if (!groupId) return; const request = ++listGeneration.current; setLoading(true); try { const query = new URLSearchParams({ group_id: groupId, confirmed_only: "false", sort_by: meaningSort.id, sort_order: meaningSort.desc ? "desc" : "asc" }); const r = unwrapApiData(await apiRequest(`jargon/meanings?${query.toString()}`)); if (request === listGeneration.current) setMeanings((r.meanings ?? []) as Entity[]); } catch (e) { if (request === listGeneration.current) showToast(String(e), true); } finally { if (request === listGeneration.current) setLoading(false); } }, [groupId, meaningSort, showToast]);
  const fetchStats = useCallback(async () => { if (!groupId) return; try { const r = unwrapApiData<Record<string, unknown>>(await apiRequest(`jargon/stats?group_id=${groupId}`)); setStats({ total_terms: Number(r.total_terms ?? 0), candidate_count: Number(r.candidate_count ?? 0), store_confirmed: Number(r.store_confirmed ?? 0) }); } catch { /* non-critical */ } }, [groupId]);
  const refresh = useCallback(() => { fetchStats(); tab === "candidates" ? fetchCandidates() : fetchMeanings(); }, [fetchStats, fetchCandidates, fetchMeanings, tab]);
  useEffect(() => { setSelected([]); refresh(); return () => { listGeneration.current += 1; }; }, [refresh]);
  useEffect(() => () => { listGeneration.current += 1; }, []);
  const changeCandidateSort = useCallback((next: DataTableSort | null) => { setSelected([]); setCandidateSort(next ?? DEFAULT_CANDIDATE_SORT); }, []);
  const changeMeaningSort = useCallback((next: DataTableSort | null) => { setSelected([]); setMeaningSort(next ?? DEFAULT_MEANING_SORT); }, []);
  const handleConfirm = async (term: string, confirmed: boolean) => { if (confirmInFlight.current) return; confirmInFlight.current = true; setConfirmPending(true); try { await apiPost("jargon/confirm", { term, group_id: groupId, confirmed }); showToast(t(confirmed ? "toast.jargonConfirmed" : "toast.jargonRejected", term)); refresh(); } catch (e) { showToast(String(e), true); } finally { confirmInFlight.current = false; setConfirmPending(false); } };
  const handleMine = async () => { setMining(true); try { const r = unwrapApiData<Record<string, unknown>>(await apiPost("jargon/mine", { group_id: groupId, limit: 5 })); showToast(t("toast.jargonMineStarted")); if (Number(r.inferred_count ?? 0) > 0) refresh(); } catch (e) { showToast(String(e), true); } finally { setMining(false); } };
  const openEntity = (e: Entity) => { setEntity(e); setEntityRevision(e.revision); setEditDraft(draftOf(e, groupId)); setEditBase(draftOf(e, groupId)); setEditorMode("view"); setEditError(""); setEditErrors({}); setEditorOpen(true); };
  const beginCreate = () => { setCreateDraft(draftOf({}, groupId)); setCreateError(""); setCreateErrors({}); setCreateOpen(true); };
  const create = async () => { if (createPending) return; setCreatePending(true); setCreateError(""); setCreateErrors({}); try { const r = validEntityEnvelope(unwrapApiData(await apiPost("jargon/create", createDraft))); if (!r) throw new Error("Invalid jargon entity response"); const next = r.entity; setMeanings((old) => [...old.filter((x) => x.term !== next.term || x.group_id !== next.group_id), next]); setCreateOpen(false); setCreateDraft(draftOf({}, groupId)); setTab("meanings"); window.setTimeout(() => openEntity(next), 0); } catch (e) { const details = editingErrorDetails(e, JARGON_FORM_FIELDS); setCreateError(details.formError ?? ""); setCreateErrors(details.fieldErrors); } finally { setCreatePending(false); } };
  const save = async (revision = entity?.revision, draft = editDraft, target = entity) => { if (!target || editPending) return; setEditPending(true); setEditError(""); setEditErrors({}); try { const { term: _term, group_id: _group, ...changes } = draft; const r = validEntityEnvelope(unwrapApiData(await apiPost("jargon/update", { identity: identity(target), changes, expected_revision: revision }))); if (!r) throw new Error("Invalid jargon entity response"); const next = r.entity; setEntity(next); setEntityRevision(r.revision); setEditDraft(draftOf(next, groupId)); setEditBase(draftOf(next, groupId)); setEditorMode("view"); setMeanings((old) => old.map((x) => x.term === target.term && x.group_id === target.group_id ? next : x)); } catch (e) { const apiError = e as Partial<ApiRequestError>; const data = (apiError.data ?? {}) as Record<string, unknown>; const currentRevision = typeof data.current_revision === "string" ? data.current_revision.trim() : ""; const remoteEnvelope = validEntityEnvelope({ entity: data.current_entity, revision: currentRevision }); if (apiError.code === "edit_conflict" && remoteEnvelope && currentRevision) { setEditError("Edit conflict"); setConflict({ remote: remoteEnvelope.entity, revision: currentRevision }); } else { const details = editingErrorDetails(e, JARGON_FORM_FIELDS); setEditError(details.formError ?? ""); setEditErrors(details.fieldErrors); } } finally { setEditPending(false); } };
  const remove = async () => { if (!deleteTarget || removeInFlight.current) return; const target = { identity: identity(deleteTarget), expected_revision: deleteTarget.revision }; removeInFlight.current = true; try { const result = unwrapApiData(await apiPost("jargon/delete", target)); const responseIdentity = result && typeof result === "object" ? (result as Record<string, unknown>).identity : null; if (!result || typeof result !== "object" || (result as Record<string, unknown>).deleted !== true || !responseIdentity || typeof responseIdentity !== "object" || (responseIdentity as Record<string, unknown>).term !== target.identity.term || (responseIdentity as Record<string, unknown>).group_id !== target.identity.group_id) throw new Error("Invalid jargon delete response"); setMeanings((old) => old.filter((x) => x.term !== target.identity.term || x.group_id !== target.identity.group_id)); setDeleteTarget(null); setEditorOpen(false); } catch (e) { showToast(String(e), true); } finally { removeInFlight.current = false; } };
  const batch = async (action: string, frozenItems?: Array<{ identity: { term: string; group_id: string }; expected_revision?: string }>) => { if (batchPending || batchInFlight.current) return; const items = frozenItems ?? meanings.filter((m) => selected.includes(`${m.term}:${m.group_id}`)).map((m) => ({ identity: identity(m), expected_revision: m.revision })); batchInFlight.current = true; setBatchPending(action); try { const r = unwrapApiData(await apiPost("jargon/batch", { action, items })); const failed = validateJargonBatch(r, items); setSelected(failed); refresh(); if (action === "delete" && frozenItems) { setPendingBatchDeleteItems(null); setDeleteTarget(null); } } catch (e) { showToast(String(e), true); } finally { batchInFlight.current = false; setBatchPending(null); } };
  const openBatchDelete = () => { const items = meanings.filter((m) => selected.includes(`${m.term}:${m.group_id}`)).map((m) => ({ identity: identity(m), expected_revision: m.revision })); setPendingBatchDeleteItems(items); setDeleteTarget(selectedEntities[0] ?? null); };
  const closeCreate = () => createDirty ? setUnsaved("create") : setCreateOpen(false); const closeEditor = () => editDirty ? setUnsaved("edit") : setEditorOpen(false);
  const tabKey = (e: KeyboardEvent<HTMLButtonElement>, current: "candidates" | "meanings") => { if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); const next = current === "candidates" ? "meanings" : "candidates"; setTab(next); document.getElementById(`jargon-${next}-tab`)?.focus(); } };
  const selectedEntities = useMemo(() => meanings.filter((m) => selected.includes(`${m.term}:${m.group_id}`)), [meanings, selected]);
  const selectedIds = useMemo(() => new Set(selected), [selected]);
  const groupItems = groups.map((g) => ({ value: g.group_id, label: `${g.group_id}${g.message_count ? ` (${g.message_count})` : ""}` }));
  const form = (value: JargonDraft, change: (v: JargonDraft) => void, mode: "create" | "edit", errors: FieldErrors, formError: string) => <JargonForm value={value} onChange={change} fieldErrors={errors} formErrors={formError ? [formError] : []} mode={mode} />;
  const deleteTitle = t("jargon.deleteTitle");
  const candidateColumns = useMemo<DataTableColumn<JargonCandidate>[]>(() => [
    {
      id: "term",
      accessorKey: "term",
      header: t("table.title"),
      meta: { label: t("table.title"), serverSortKey: "term", required: true, defaultPin: "left" },
    },
    {
      id: "score",
      accessorKey: "score",
      header: t("jargon.score"),
      meta: { label: t("jargon.score"), serverSortKey: "score" },
      cell: ({ row }) => <ScoreBar score={row.original.score} locale={locale} />,
    },
    {
      id: "frequency",
      accessorKey: "frequency",
      header: t("jargon.frequency"),
      meta: { label: t("jargon.frequency"), serverSortKey: "frequency", cellClassName: "tabular-nums" },
    },
    {
      id: "unique_users",
      accessorKey: "unique_users",
      header: t("jargon.users"),
      meta: { label: t("jargon.users"), serverSortKey: "unique_users", cellClassName: "tabular-nums" },
    },
    {
      id: "first_seen",
      accessorKey: "first_seen",
      header: t("table.created"),
      meta: { label: t("table.created"), serverSortKey: "first_seen" },
      cell: ({ row }) => row.original.first_seen ? formatDashboardDateTime(row.original.first_seen, locale) : "—",
    },
    actionsColumn({
      label: t("table.rowActions"),
      rowLabel: (candidate) => `${t("table.rowActions")} ${candidate.term}`,
      actions: (candidate) => [
        { id: "confirm", label: t("jargon.confirm"), disabled: confirmPending, onSelect: () => void handleConfirm(candidate.term, true) },
        { id: "reject", label: t("jargon.reject"), disabled: confirmPending, destructive: true, onSelect: () => void handleConfirm(candidate.term, false) },
      ],
    }),
  ], [confirmPending, groupId, locale, t]);
  const meaningColumns = useMemo<DataTableColumn<Entity>[]>(() => [
    selectionColumn({
      label: t("common.select"),
      rowLabel: (meaning) => t("jargon.selectTerm", meaning.term),
    }),
    {
      id: "term",
      accessorKey: "term",
      header: t("table.title"),
      meta: { label: t("table.title"), serverSortKey: "term", required: true, defaultPin: "left" },
      cell: ({ row }) => <div><Button variant="ghost" onClick={(event) => { event.stopPropagation(); openEntity(row.original); }} aria-label={`${t("detail.view").toLowerCase()} ${row.original.term}`}>{row.original.term}</Button><span className="sr-only">{row.original.revision}</span></div>,
    },
    {
      id: "meaning",
      accessorKey: "meaning",
      header: t("jargon.meaning"),
      enableSorting: false,
      meta: { label: t("jargon.meaning") },
      cell: ({ row }) => row.original.meaning || "—",
    },
    {
      id: "confidence",
      accessorKey: "confidence",
      header: t("jargon.confidence"),
      meta: { label: t("jargon.confidence"), serverSortKey: "confidence" },
      cell: ({ row }) => <ScoreBar score={row.original.confidence} locale={locale} />,
    },
    {
      id: "count",
      accessorKey: "count",
      header: t("jargon.count"),
      meta: { label: t("jargon.count"), serverSortKey: "count", cellClassName: "tabular-nums" },
    },
    {
      id: "updated_at",
      accessorKey: "updated_at",
      header: t("table.updated"),
      meta: { label: t("table.updated"), serverSortKey: "updated_at" },
      cell: ({ row }) => row.original.updated_at ? formatDashboardDateTime(row.original.updated_at, locale) : "—",
    },
    actionsColumn({
      label: t("table.rowActions"),
      rowLabel: (meaning) => `${t("table.rowActions")} ${meaning.term}`,
      actions: (meaning) => [
        { id: "view", label: t("detail.view"), onSelect: () => openEntity(meaning) },
        { id: "edit", label: t("detail.edit"), onSelect: () => { openEntity(meaning); setEditorMode("edit"); } },
        { id: "delete", label: t("common.delete"), destructive: true, disabled: !meaning.revision, onSelect: () => setDeleteTarget(meaning) },
      ],
    }),
  ], [groupId, locale, t]);
  return <PageFrame variant="dense" aria-label={t("jargon.title")}><PageHeader title={t("jargon.title")} icon={<MessageCircleCode />} actions={<><Select items={groupItems} value={groupId} onValueChange={(v) => { if (v) { setSelected([]); setGroupId(v); } }} disabled={groups.length === 0}><SelectTrigger className="w-36 text-xs"><SelectValue placeholder={t("jargon.allGroups")} /></SelectTrigger><SelectContent><SelectGroup>{groupItems.map((item) => <SelectItem key={item.value} value={item.value} onClick={() => { setSelected([]); setGroupId(item.value); }}>{item.label}</SelectItem>)}</SelectGroup></SelectContent></Select><Button variant="outline" size="sm" onClick={refresh}><RefreshCw data-icon="inline-start" /> {t("common.refresh")}</Button></>} />
    <div className="flex min-h-12 shrink-0 items-center border-b bg-muted/30 px-4 py-2"><MetricGrid minItemWidth="12rem" className="w-full gap-3 text-xs text-muted-foreground"><div><Hash /> {t("jargon.stats")}: <b>{stats.total_terms}</b> {t("jargon.meanings").toLowerCase()}</div><div><Users /> <b>{stats.candidate_count}</b> {t("jargon.candidates").toLowerCase()}</div><div><Zap /> <b>{stats.store_confirmed}</b> {t("jargon.confirm").toLowerCase()}</div></MetricGrid></div>
    <PageToolbar className="justify-between bg-background"><div className="flex gap-1" role="tablist" aria-label={t("jargon.views")}>{(["candidates", "meanings"] as const).map((key) => <Button key={key} variant={tab === key ? "secondary" : "ghost"} size="sm" role="tab" id={`jargon-${key}-tab`} aria-selected={tab === key} aria-controls={`jargon-${key}-panel`} tabIndex={tab === key ? 0 : -1} onClick={() => setTab(key)} onKeyDown={(e) => tabKey(e, key)}>{t(`jargon.${key}`)}</Button>)}</div><div className="flex gap-2">{tab === "meanings" && <><Button size="sm" onClick={beginCreate}>{t("jargon.newJargon")}</Button>{selectedEntities.length > 0 && <span className="self-center text-sm">{t("jargon.selected", String(selectedEntities.length))}</span>}{([['confirm','jargon.confirmSelected'],['unconfirm','jargon.unconfirmSelected'],['set_global','jargon.setGlobal'],['unset_global','jargon.unsetGlobal'],['delete','jargon.deleteSelected']] as const).map(([action,labelKey]) => selectedEntities.length > 0 && <Button key={action} aria-label={t(labelKey)} size="sm" variant={action === "delete" ? "destructive" : "outline"} disabled={Boolean(batchPending)} onClick={() => action === "delete" ? openBatchDelete() : batch(action)}>{t(labelKey)}</Button>)}</>}{tab === "candidates" && <Button size="sm" onClick={handleMine} disabled={mining}>{mining ? <Loader2 className="animate-spin" /> : <Sparkles />} {mining ? t("jargon.mining") : t("jargon.mine")}</Button>}</div></PageToolbar>
    <PageContent width="full" className="p-0"><div id="jargon-candidates-panel" role="tabpanel" aria-labelledby="jargon-candidates-tab" hidden={tab !== "candidates"}><DataTable tableId="jargon-candidates" data={candidates} columns={candidateColumns} getRowId={(candidate) => `${candidate.term}:${candidate.group_id}`} sort={candidateSort} onSortChange={changeCandidateSort} loading={loading} emptyLabel={t("jargon.noCandidates")} /></div>
      <div id="jargon-meanings-panel" role="tabpanel" aria-labelledby="jargon-meanings-tab" hidden={tab !== "meanings"}><DataTable tableId="jargon-meanings" data={meanings} columns={meaningColumns} getRowId={(meaning) => `${meaning.term}:${meaning.group_id}`} sort={meaningSort} onSortChange={changeMeaningSort} selectedRowIds={selectedIds} onSelectedRowIdsChange={(next) => setSelected(Array.from(next))} currentRowId={editorOpen && entity ? `${entity.term}:${entity.group_id}` : null} onRowActivate={openEntity} loading={loading} emptyLabel={t("jargon.noMeanings")} /></div></PageContent>
    <EntityCreateDialog open={createOpen} onOpenChange={(v) => v ? setCreateOpen(true) : closeCreate()} title={t("jargon.newJargon")} description={t("jargon.createDescription")} isDirty={createDirty} isSubmitting={createPending} canSubmit={Boolean(createDraft.term.trim() && createDraft.meaning.trim())} onCancel={() => { setCreateOpen(false); setCreateDraft(draftOf({}, groupId)); setUnsaved(null); onDirtyChange?.(editDirty); refresh(); }} onSubmit={create} form={form(createDraft, (next) => { setCreateDraft(next); setCreateErrors({}); setCreateError(""); }, "create", createErrors, createError)} labels={{ close:t("common.close"), cancel:t("common.cancel"), submit:t("detail.create"), submitting:t("common.saving") }} />
     <EntityEditorSheet key={entityRevision ?? entity?.revision ?? entity?.term} open={editorOpen} onOpenChange={(v) => v ? setEditorOpen(true) : closeEditor()} title={entity?.term ?? t("jargon.title")} description={t("jargon.meaning")} mode={editorMode} isDirty={editDirty} isSubmitting={editPending} canSave={Boolean(editDraft.meaning.trim())} onBeginEdit={() => { setEditorMode("edit"); setEditError(""); setEditErrors({}); }} onCancel={() => { setEditDraft(editBase); setEditorMode("view"); setEditError(""); setEditErrors({}); }} onSave={() => save()} status={editDirty ? t("detail.unsaved") : null} view={entity ? <div className="space-y-6"><DetailSection><DetailText>{entity.meaning}</DetailText></DetailSection>{entity.context_examples?.length ? <DetailSection title={t("jargon.contextExamples")}><div className="space-y-3">{entity.context_examples.map((example) => <DetailText key={example}>{example}</DetailText>)}</div></DetailSection> : null}<DetailGrid><DetailField label={t("detail.revision")}>{entityRevision ?? entity.revision ?? "--"}</DetailField><DetailField label={t("jargon.count")}>{entity.count ?? "--"}</DetailField><DetailField label={t("table.confidence")}>{entity.confidence ?? "--"}</DetailField></DetailGrid></div> : null} viewActions={entity ? <Button type="button" variant="destructive" size="sm" disabled={!entity.revision} onClick={() => setDeleteTarget(entity)}>{t("common.delete")}</Button> : null} form={form(editDraft, (next) => { setEditDraft(next); setEditErrors({}); setEditError(""); }, "edit", editErrors, editError)} labels={{ edit:t("detail.edit"), close:t("common.close"), cancel:t("common.cancel"), save:t("common.save"), saving:t("common.saving") }} />
    <DeleteConfirmDialog open={Boolean(deleteTarget)} title={deleteTitle} description={t("jargon.deleteDescription")} cancelLabel={t("common.cancel")} confirmLabel={t("common.delete")} onCancel={() => { setDeleteTarget(null); setPendingBatchDeleteItems(null); }} onConfirm={() => pendingBatchDeleteItems ? void batch("delete", pendingBatchDeleteItems) : void remove()} />
    <EditConflictDialog open={Boolean(conflict)} title={t("jargon.conflictTitle")} description={t("jargon.conflictDescription")} loadRemoteLabel={t("config.conflict.loadRemote")} reapplyLocalLabel={t("jargon.reapplyLocal")} onLoadRemote={() => { if (conflict) { setEntity(conflict.remote); setEditDraft(draftOf(conflict.remote, groupId)); setEditBase(draftOf(conflict.remote, groupId)); setEditorMode("view"); setConflict(null); } }} onReapplyLocal={() => { if (conflict) { const local = editDraft; setEntity(conflict.remote); setEditBase(draftOf(conflict.remote, groupId)); setEditDraft(local); setConflict(null); save(conflict.revision, local, conflict.remote); } }} />
    <UnsavedChangesDialog open={Boolean(unsaved)} title={t("config.unsaved.title")} description={t("config.unsaved.description")} keepEditingLabel={t("config.unsaved.keepEditing")} discardLabel={t("config.unsaved.discard")} onKeepEditing={() => setUnsaved(null)} onDiscard={() => { if (unsaved === "create") { setCreateOpen(false); setCreateDraft(draftOf({}, groupId)); } else { setEditorOpen(false); setEditorMode("view"); setEditDraft(editBase); } setUnsaved(null); }} />
  </PageFrame>;
}
