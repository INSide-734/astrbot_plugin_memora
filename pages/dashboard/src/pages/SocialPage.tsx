import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRightLeft, RefreshCw, Tag, Trash2, UsersRound, X } from "lucide-react";

import { useI18n } from "@/hooks/useI18n";
import { useGroups } from "@/hooks/useGroups";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { Field, FieldLabel } from "@/components/ui/field";
import { Progress } from "@/components/ui/Progress";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DeleteConfirmDialog } from "@/components/editing/DeleteConfirmDialog";
import { EditConflictDialog } from "@/components/editing/EditConflictDialog";
import { EntityCreateDialog } from "@/components/editing/EntityCreateDialog";
import { EntityEditorSheet } from "@/components/editing/EntityEditorSheet";
import { TagEditor } from "@/components/editing/TagEditor";
import { SocialRelationForm } from "@/components/editing/forms/SocialRelationForm";
import { UnsavedChangesDialog } from "@/components/editing/UnsavedChangesDialog";
import { PageContent, PageFrame, PageHeader, PageToolbar } from "@/components/layout/PageLayout";
import { RELATION_CATEGORIES } from "@/lib/constants";
import { dashboardLocale, formatDashboardPercent } from "@/lib/i18n";
import { ApiRequestError, type BatchResult, type EntityEnvelope, type FieldErrors } from "@/types/editing";
import type { SocialRelationDraft, SocialRelationEntry } from "@/types";

interface SocialPageProps {
  showToast: (msg: string, isError?: boolean) => void;
  onDirtyChange?: (dirty: boolean) => void;
}

interface SocialRelation extends SocialRelationEntry {
  revision?: string;
}

interface BatchTagDraft {
  operation: "add_tags" | "remove_tags";
  tags: string[];
}

type SocialIdentity = Pick<SocialRelation, "from_user" | "to_user" | "group_id" | "relation_type">;
type SocialEntityEnvelope = EntityEnvelope<SocialRelation>;
type SocialListResponse = { relations: SocialRelation[] };
type SocialDeleteResponse = { deleted?: boolean; identity?: SocialIdentity };
type SocialBatchFailure = { identity: SocialIdentity; code: string; message: string };
type SocialBatchResponse = Omit<BatchResult<SocialIdentity>, "succeeded_count" | "failed_count" | "failures"> & {
  succeeded_count?: number;
  failed_count?: number;
  failures: SocialBatchFailure[];
};

const EMPTY_RELATION_DRAFT: SocialRelationDraft = {
  from_user: "",
  to_user: "",
  group_id: "",
  relation_type: "stranger",
  strength: 0.5,
  tags: [],
};
const EMPTY_BATCH_TAG: BatchTagDraft = { operation: "add_tags", tags: [] };

function cloneDraft(draft: SocialRelationDraft): SocialRelationDraft {
  return { ...draft, tags: [...draft.tags] };
}

function relationDraft(relation: SocialRelation): SocialRelationDraft {
  return {
    from_user: relation.from_user,
    to_user: relation.to_user,
    group_id: relation.group_id,
    relation_type: relation.relation_type,
    strength: relation.strength,
    tags: [...(relation.tags ?? [])],
  };
}

function relationFromEnvelope(entity: SocialRelation, revision?: string): SocialRelation {
  return { ...entity, tags: [...(entity.tags ?? [])], revision: revision ?? entity.revision };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isSocialRelation(value: unknown): value is SocialRelation {
  return isRecord(value)
    && typeof value.from_user === "string"
    && typeof value.to_user === "string"
    && typeof value.group_id === "string"
    && typeof value.relation_type === "string"
    && typeof value.strength === "number"
    && (value.tags === undefined || Array.isArray(value.tags) && value.tags.every((tag) => typeof tag === "string"));
}

function isSocialIdentity(value: unknown): value is SocialIdentity {
  return isRecord(value)
    && typeof value.from_user === "string"
    && typeof value.to_user === "string"
    && typeof value.group_id === "string"
    && typeof value.relation_type === "string";
}

function isSocialBatchFailure(value: unknown): value is SocialBatchFailure {
  return isRecord(value) && isSocialIdentity(value.identity) && typeof value.code === "string" && typeof value.message === "string";
}

function socialEntityEnvelope(value: unknown, message: string): SocialEntityEnvelope {
  if (!isRecord(value) || !isSocialRelation(value.entity) || typeof value.revision !== "string" || value.revision.length === 0) throw new Error(message);
  return { entity: value.entity, revision: value.revision };
}

function socialListResponse(value: unknown, message: string): SocialListResponse {
  if (!isRecord(value) || !Array.isArray(value.relations) || !value.relations.every(isSocialRelation)) throw new Error(message);
  return { relations: value.relations };
}

function socialBatchResponse(value: unknown, message: string): SocialBatchResponse {
  if (!isRecord(value) || typeof value.total !== "number" || !Number.isInteger(value.total) || value.total < 0 || !Array.isArray(value.succeeded_ids) || !value.succeeded_ids.every(isSocialIdentity) || !Array.isArray(value.failures) || !value.failures.every(isSocialBatchFailure)) throw new Error(message);
  if (value.succeeded_count !== undefined && (typeof value.succeeded_count !== "number" || !Number.isInteger(value.succeeded_count))) throw new Error(message);
  if (value.failed_count !== undefined && (typeof value.failed_count !== "number" || !Number.isInteger(value.failed_count))) throw new Error(message);
  return {
    total: value.total,
    succeeded_count: typeof value.succeeded_count === "number" ? value.succeeded_count : undefined,
    failed_count: typeof value.failed_count === "number" ? value.failed_count : undefined,
    succeeded_ids: value.succeeded_ids,
    failures: value.failures,
  };
}

function relationIdentity(relation: Pick<SocialRelation, "from_user" | "to_user" | "group_id" | "relation_type">) {
  return {
    from_user: relation.from_user,
    to_user: relation.to_user,
    group_id: relation.group_id,
    relation_type: relation.relation_type,
  };
}

function relationKey(relation: Pick<SocialRelation, "from_user" | "to_user" | "group_id" | "relation_type">): string {
  const identity = relationIdentity(relation);
  return [identity.from_user, identity.to_user, identity.group_id, identity.relation_type].join("\u0000");
}

function errorDetails(error: unknown): { fieldErrors: FieldErrors; message: string } {
  if (error instanceof ApiRequestError) return { fieldErrors: error.fieldErrors, message: error.message };
  return { fieldErrors: {}, message: error instanceof Error ? error.message : String(error) };
}

function hasRevision(relation: SocialRelation | null | undefined): relation is SocialRelation & { revision: string } {
  return typeof relation?.revision === "string" && relation.revision.length > 0;
}

export function SocialPage({ showToast, onDirtyChange }: SocialPageProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const { groups, groupId, setGroupId } = useGroups();
  const label = (key: string, fallback: string, ...args: string[]) => {
    const translated = t(key, ...args);
    return translated === key ? fallback : translated;
  };
  const [relations, setRelations] = useState<SocialRelation[]>([]);
  const [loading, setLoading] = useState(false);
  const [category, setCategory] = useState("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [detail, setDetail] = useState<SocialRelation | null>(null);
  const [editDraft, setEditDraft] = useState<SocialRelationDraft>(cloneDraft(EMPTY_RELATION_DRAFT));
  const [editBaseline, setEditBaseline] = useState<SocialRelationDraft>(cloneDraft(EMPTY_RELATION_DRAFT));
  const [editMode, setEditMode] = useState(false);
  const [editSubmitting, setEditSubmitting] = useState(false);
  const [editFieldErrors, setEditFieldErrors] = useState<FieldErrors>({});
  const [editFormError, setEditFormError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createDraft, setCreateDraft] = useState<SocialRelationDraft>(cloneDraft(EMPTY_RELATION_DRAFT));
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createFieldErrors, setCreateFieldErrors] = useState<FieldErrors>({});
  const [createFormError, setCreateFormError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [batchDeleteSubmitting, setBatchDeleteSubmitting] = useState(false);
  const [batchTagOpen, setBatchTagOpen] = useState(false);
  const [batchTagDraft, setBatchTagDraft] = useState<BatchTagDraft>({ ...EMPTY_BATCH_TAG });
  const [batchTagSubmitting, setBatchTagSubmitting] = useState(false);
  const [batchTagError, setBatchTagError] = useState<string | null>(null);
  const [batchTagTypingDirty, setBatchTagTypingDirty] = useState(false);
  const [conflict, setConflict] = useState<{ entity: SocialRelation; revision: string } | null>(null);
  const [pendingDiscard, setPendingDiscard] = useState<"create" | "edit" | "batch" | null>(null);
  const relationsLoadGeneration = useRef(0);
  const batchDeleteSubmittingRef = useRef(false);

  const createDirty = JSON.stringify(createDraft) !== JSON.stringify(EMPTY_RELATION_DRAFT);
  const editDirty = JSON.stringify(editDraft) !== JSON.stringify(editBaseline);
  const batchTagDirty = batchTagTypingDirty || JSON.stringify(batchTagDraft) !== JSON.stringify(EMPTY_BATCH_TAG);

  useEffect(() => {
    onDirtyChange?.(createDirty || editDirty || batchTagDirty);
  }, [batchTagDirty, createDirty, editDirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const matchesCurrentView = useCallback((relation: SocialRelation) => (
    relation.group_id === groupId && (category === "all" || relation.category === category)
  ), [category, groupId]);

  const loadRelations = useCallback(async () => {
    const generation = ++relationsLoadGeneration.current;
    if (!groupId) {
      setRelations([]);
      return;
    }
    setLoading(true);
    try {
      const query = new URLSearchParams({ group_id: groupId });
      if (category !== "all") query.set("category", category);
      const response = socialListResponse(unwrapApiData(
        await apiRequest(`social/relations?${query.toString()}`)
      ), label("social.invalidList", "Invalid social relation list response"));
      if (generation !== relationsLoadGeneration.current) return;
      const nextRelations = (response.relations ?? []).map((relation) => relationFromEnvelope(relation));
      setRelations(nextRelations);
      setSelected((previous) => new Set([...previous].filter((key) => nextRelations.some((item) => relationKey(item) === key))));
    } catch (error) {
      if (generation === relationsLoadGeneration.current) showToast(String(error), true);
    } finally {
      if (generation === relationsLoadGeneration.current) setLoading(false);
    }
  }, [category, groupId, showToast]);

  useEffect(() => { void loadRelations(); }, [loadRelations]);

  const openCreate = () => {
    setCreateDraft(cloneDraft(EMPTY_RELATION_DRAFT));
    setCreateFieldErrors({});
    setCreateFormError(null);
    setCreateOpen(true);
  };

  const resetCreate = () => {
    setCreateDraft(cloneDraft(EMPTY_RELATION_DRAFT));
    setCreateFieldErrors({});
    setCreateFormError(null);
  };

  const requestCloseCreate = () => {
    if (createSubmitting) return;
    if (createDirty) setPendingDiscard("create");
    else {
      resetCreate();
      setCreateOpen(false);
    }
  };

  const openDetail = (relation: SocialRelation) => {
    const baseline = relationDraft(relation);
    setDetail(relation);
    setEditBaseline(baseline);
    setEditDraft(cloneDraft(baseline));
    setEditMode(false);
    setEditFieldErrors({});
    setEditFormError(null);
  };

  const beginEdit = () => {
    if (detail) {
      const baseline = relationDraft(detail);
      setEditBaseline(baseline);
      setEditDraft(cloneDraft(baseline));
    }
    setEditFieldErrors({});
    setEditFormError(null);
    setEditMode(true);
  };

  const cancelEdit = () => {
    if (editSubmitting) return;
    setEditDraft(cloneDraft(editBaseline));
    setEditMode(false);
    setEditFieldErrors({});
    setEditFormError(null);
  };

  const requestCloseDetail = () => {
    if (editSubmitting) return;
    if (editDirty) setPendingDiscard("edit");
    else setDetail(null);
  };

  const createRelation = async () => {
    if (createSubmitting || !createDirty || !createDraft.from_user.trim() || !createDraft.to_user.trim() || !createDraft.relation_type.trim()) return;
    setCreateSubmitting(true);
    setCreateFieldErrors({});
    setCreateFormError(null);
    try {
      const response = socialEntityEnvelope(unwrapApiData(
        await apiRequest("social/create", { method: "POST", body: createDraft })
      ), label("social.invalidEntity", "Invalid social relation entity response"));
      const created = relationFromEnvelope(response.entity, response.revision);
      resetCreate();
      setCreateOpen(false);
      if (matchesCurrentView(created)) {
        setRelations((previous) => [created, ...previous.filter((relation) => relationKey(relation) !== relationKey(created))]);
        openDetail(created);
      } else {
        showToast(label("social.createdOutsideView", "Created relation is outside the current view"));
      }
    } catch (error) {
      const details = errorDetails(error);
      setCreateFieldErrors(details.fieldErrors);
      setCreateFormError(details.message);
      throw error;
    } finally {
      setCreateSubmitting(false);
    }
  };

  const saveEdit = async () => {
    if (!detail || !hasRevision(detail) || editSubmitting || !editDirty) return;
    setEditSubmitting(true);
    setEditFieldErrors({});
    setEditFormError(null);
    try {
      const response = socialEntityEnvelope(unwrapApiData(await apiRequest("social/update", {
        method: "POST",
        body: {
          identity: relationIdentity(detail),
          changes: {
            relation_type: editDraft.relation_type,
            strength: editDraft.strength,
            tags: editDraft.tags,
          },
          expected_revision: detail.revision,
        },
      })), label("social.invalidEntity", "Invalid social relation entity response"));
      const saved = relationFromEnvelope(response.entity, response.revision);
      if (matchesCurrentView(saved)) {
        const baseline = relationDraft(saved);
        setDetail(saved);
        setEditBaseline(baseline);
        setEditDraft(cloneDraft(baseline));
        setEditMode(false);
        setRelations((previous) => previous.map((relation) => relationKey(relation) === relationKey(detail) ? saved : relation));
      } else {
        setRelations((previous) => previous.filter((relation) => relationKey(relation) !== relationKey(detail)));
        setDetail(null);
        showToast(label("social.updatedOutsideView", "Updated relation is outside the current view"));
      }
    } catch (error) {
      const details = errorDetails(error);
      setEditFieldErrors(details.fieldErrors);
      setEditFormError(details.message);
      if (error instanceof ApiRequestError && (error.code === "conflict" || error.code === "edit_conflict")) {
        const entity = error.data.current_entity;
        const revision = error.data.current_revision;
        if (isSocialRelation(entity) && typeof revision === "string" && revision.length > 0) {
          setConflict({ entity: relationFromEnvelope(entity, revision), revision });
        }
      }
      throw error;
    } finally {
      setEditSubmitting(false);
    }
  };

  const reapplyLocalValues = () => {
    if (!conflict) return;
    const remoteBaseline = relationDraft(conflict.entity);
    const merged = cloneDraft(remoteBaseline);
    if (editDraft.relation_type !== editBaseline.relation_type) merged.relation_type = editDraft.relation_type;
    if (editDraft.strength !== editBaseline.strength) merged.strength = editDraft.strength;
    if (JSON.stringify(editDraft.tags) !== JSON.stringify(editBaseline.tags)) merged.tags = editDraft.tags;
    setDetail(conflict.entity);
    setEditBaseline(remoteBaseline);
    setEditDraft(merged);
    setEditMode(true);
    setEditFieldErrors({});
    setEditFormError(null);
    setConflict(null);
  };

  const loadRemoteValues = () => {
    if (!conflict) return;
    const baseline = relationDraft(conflict.entity);
    setDetail(conflict.entity);
    setEditBaseline(baseline);
    setEditDraft(cloneDraft(baseline));
    setEditMode(false);
    setConflict(null);
  };

  const executeSingleDelete = async () => {
    if (!detail || !hasRevision(detail)) return;
    try {
      unwrapApiData<SocialDeleteResponse>(await apiRequest("social/delete", {
        method: "POST",
        body: { identity: relationIdentity(detail), expected_revision: detail.revision },
      }));
      setRelations((previous) => previous.filter((relation) => relationKey(relation) !== relationKey(detail)));
      setSelected((previous) => { const next = new Set(previous); next.delete(relationKey(detail)); return next; });
      setDeleteOpen(false);
      setDetail(null);
      showToast(label("social.relationDeleted", "Relation deleted"));
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error), true);
    }
  };

  const selectedRelations = Array.from(selected)
    .map((key) => relations.find((relation) => relationKey(relation) === key))
    .filter((relation): relation is SocialRelation => Boolean(relation));
  const revisionedSelection = selectedRelations.length === selected.size && selectedRelations.every(hasRevision);
  const batchItems = selectedRelations.map((relation) => ({
    identity: relationIdentity(relation),
    expected_revision: relation.revision,
  }));

  const retainFailedSelection = (response: SocialBatchResponse) => {
    const failures = response.failures;
    if (!failures.length) return false;
    const failedKeys = failures.flatMap((failure) => {
      return [relationKey(failure.identity)];
    });
    setSelected(new Set(failedKeys));
    const failedCount = response.failed_count ?? failures.length;
    showToast(label("social.batchPartialFailure", `${failedCount} relation operation failed`, String(failedCount)), true);
    return true;
  };

  const executeBatchDelete = async () => {
    if (batchDeleteSubmittingRef.current) return;
    if (!selected.size || !revisionedSelection) {
      if (selected.size) showToast(label("social.revisionRequired", "Selected relations need a revision before deletion"), true);
      return;
    }
    batchDeleteSubmittingRef.current = true;
    setBatchDeleteSubmitting(true);
    const selectedKeys = new Set(selected);
    const selectedSnapshot = Array.from(selectedKeys)
      .map((key) => relations.find((relation) => relationKey(relation) === key))
      .filter((relation): relation is SocialRelation => Boolean(relation));
    const snapshotItems = selectedSnapshot.map((relation) => ({
      identity: relationIdentity(relation),
      expected_revision: relation.revision,
    }));
    try {
      const response = socialBatchResponse(unwrapApiData(await apiRequest("social/batch", {
        method: "POST",
        body: { action: "delete", items: snapshotItems, params: {} },
      })), label("social.invalidBatch", "Invalid social batch response"));
      if (!retainFailedSelection(response)) setSelected((previous) => new Set([...previous].filter((key) => !selectedKeys.has(key))));
      void loadRelations();
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error), true);
    } finally {
      batchDeleteSubmittingRef.current = false;
      setBatchDeleteSubmitting(false);
    }
  };

  const openBatchTag = () => {
    setBatchTagError(null);
    setBatchTagTypingDirty(false);
    setBatchTagOpen(true);
  };

  const requestCloseBatchTag = () => {
    if (batchTagSubmitting) return;
    if (batchTagDirty) setPendingDiscard("batch");
    else setBatchTagOpen(false);
  };

  const submitBatchTag = async () => {
    if (batchTagSubmitting || !selected.size || !revisionedSelection || !batchTagDraft.tags.length) return;
    setBatchTagSubmitting(true);
    setBatchTagError(null);
    try {
      const response = socialBatchResponse(unwrapApiData(await apiRequest("social/batch", {
        method: "POST",
        body: {
          action: batchTagDraft.operation,
          items: batchItems,
          params: { tags: batchTagDraft.tags },
        },
      })), label("social.invalidBatch", "Invalid social batch response"));
      if (!retainFailedSelection(response)) {
        setSelected(new Set());
        setBatchTagDraft({ ...EMPTY_BATCH_TAG });
        setBatchTagTypingDirty(false);
        setBatchTagOpen(false);
      }
      void loadRelations();
    } catch (error) {
      const details = errorDetails(error);
      setBatchTagError(details.message);
    } finally {
      setBatchTagSubmitting(false);
    }
  };

  const relationLabel = (type: string): string => {
    const key = `relation.${type}`;
    const translated = t(key);
    return translated !== key ? translated : type;
  };
  const categories = [
    { value: "all", label: label("social.allCategories", "All Categories") },
    ...Object.keys(RELATION_CATEGORIES).map((value) => ({ value, label: label(`social.category.${value}`, value) })),
  ];
  const allSelected = relations.length > 0 && selected.size === relations.length;

  const relationTable = loading ? (
    <p className="py-12 text-center text-sm text-muted-foreground">{label("table.loading", "Loading")}</p>
  ) : relations.length === 0 ? (
    <p className="py-12 text-center text-sm text-muted-foreground">{label("social.noData", "No relations found")}</p>
  ) : (
    <Card className="gap-0 py-0">
      <Table>
        <TableHeader className="sticky top-0 bg-background"><TableRow>
          <TableHead className="w-10"><Checkbox aria-label={allSelected ? label("social.deselectAll", "Deselect all relations") : label("social.selectAll", "Select all relations")} checked={allSelected} onCheckedChange={() => setSelected(allSelected ? new Set() : new Set(relations.map(relationKey)))} /></TableHead>
          <TableHead>{t("social.relations")}</TableHead>
          <TableHead>{t("social.category")}</TableHead>
          <TableHead>{t("social.strength")}</TableHead>
          <TableHead>{t("social.frequency")}</TableHead>
          <TableHead>{t("table.tags")}</TableHead>
          <TableHead>{label("table.actions", "Actions")}</TableHead>
        </TableRow></TableHeader>
        <TableBody>
          {relations.map((relation) => {
            const key = relationKey(relation);
            return <TableRow key={key} data-state={selected.has(key) ? "selected" : undefined}>
              <TableCell><Checkbox aria-label={label("social.selectRelation", `Select relation ${relation.from_user} ${relation.to_user}`, relation.from_user, relation.to_user)} checked={selected.has(key)} onCheckedChange={() => setSelected((previous) => { const next = new Set(previous); next.has(key) ? next.delete(key) : next.add(key); return next; })} /></TableCell>
              <TableCell>
                <div className="flex items-center gap-2"><span className="text-xs font-medium">{relation.from_user}</span><ArrowRightLeft /><span className="text-xs font-medium">{relation.to_user}</span></div>
                <div className="mt-0.5 text-xs text-muted-foreground">{relationLabel(relation.relation_type)}</div>
              </TableCell>
              <TableCell><Badge variant="secondary">{RELATION_CATEGORIES[relation.category] ? label(`social.category.${relation.category}`, relation.category) : relation.category}</Badge></TableCell>
              <TableCell><div className="flex items-center gap-2"><Progress aria-label={`${relation.from_user} → ${relation.to_user} ${relationLabel(relation.relation_type)} ${t("social.strength")}`} value={relation.strength} className="h-1.5 w-20" /><span className="text-xs tabular-nums text-muted-foreground">{formatDashboardPercent(relation.strength, locale, { maximumFractionDigits: 0 })}</span></div></TableCell>
              <TableCell className="text-xs tabular-nums text-muted-foreground">{relation.frequency}</TableCell>
              <TableCell><div className="flex flex-wrap items-center gap-1">{relation.tags.map((tag) => <Badge key={tag} variant="outline"><Tag data-icon="inline-start" />{tag}</Badge>)}</div></TableCell>
              <TableCell><Button variant="ghost" size="sm" aria-label={label("social.openRelation", `Open relation ${relation.from_user} ${relation.to_user}`, relation.from_user, relation.to_user)} onClick={() => openDetail(relation)}>{label("detail.view", "View")}</Button></TableCell>
            </TableRow>;
          })}
        </TableBody>
      </Table>
    </Card>
  );

  return (
    <PageFrame variant="standard" aria-label={t("social.title")}>
      <PageHeader
        title={t("social.title")}
        icon={<UsersRound />}
        actions={<>
          <Select value={groupId} onValueChange={(value) => { if (value) { setSelected(new Set()); setGroupId(value); } }} disabled={groups.length === 0}>
            <SelectTrigger className="w-36 text-xs"><span>{groupId || t("jargon.allGroups")}</span></SelectTrigger>
            <SelectContent><SelectGroup>{groups.length > 0 ? groups.map((group) => <SelectItem key={group.group_id} value={group.group_id} onClick={() => { setSelected(new Set()); setGroupId(group.group_id); }}>{group.group_id}{group.message_count ? ` (${group.message_count})` : ""}</SelectItem>) : <SelectItem value="loading">—</SelectItem>}</SelectGroup></SelectContent>
          </Select>
          <Button variant="outline" onClick={() => void loadRelations()}><RefreshCw data-icon="inline-start" />{t("common.refresh")}</Button>
          <Button onClick={openCreate}>{label("social.newRelation", "New Relation")}</Button>
        </>}
      />
      <Tabs value={category} onValueChange={(value) => { setSelected(new Set()); setCategory(value); }} className="min-h-0 flex-1 gap-0">
        <PageToolbar className="flex-nowrap overflow-x-auto bg-background"><TabsList variant="line" aria-label={t("social.category")} className="h-9 min-w-max">{categories.map((item) => <TabsTrigger key={item.value} value={item.value} className="px-3 text-xs">{item.label}</TabsTrigger>)}</TabsList></PageToolbar>
        {categories.map((item) => <TabsContent key={item.value} value={item.value} className="min-h-0 overflow-auto"><PageContent>{relationTable}</PageContent></TabsContent>)}
      </Tabs>

      {selected.size > 0 ? <PageToolbar className="border-b-0 border-t bg-muted/40"><span className="text-sm font-medium">{label("select.selected", `${selected.size} selected`, String(selected.size))}</span><Button variant="outline" size="sm" onClick={openBatchTag}>{label("social.editTags", "Edit Tags")}</Button><Button variant="destructive" size="sm" disabled={batchDeleteSubmitting} onClick={() => void executeBatchDelete()}><Trash2 data-icon="inline-start" />{t("common.delete")}</Button><Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}><X data-icon="inline-start" />{t("common.clear")}</Button></PageToolbar> : null}

      <EntityEditorSheet open={detail !== null} onOpenChange={(open) => { if (!open) requestCloseDetail(); }} title={detail ? label("social.relationDetail", `Relation: ${detail.from_user} → ${detail.to_user}`, detail.from_user, detail.to_user) : ""} description={label("social.relationDetails", "Relation details")} mode={editMode ? "edit" : "view"} isDirty={editDirty} isSubmitting={editSubmitting} canSave={hasRevision(detail)} onBeginEdit={beginEdit} onCancel={cancelEdit} onSave={saveEdit} labels={{ edit: t("detail.edit"), close: t("common.close"), cancel: t("common.cancel"), save: t("common.save"), saving: label("common.saving", "Saving...") }} view={detail ? <div className="flex flex-col gap-4 text-sm"><div className="grid grid-cols-2 gap-3"><div><span className="text-xs font-medium text-muted-foreground">{label("social.fromUser", "From user")}</span><p>{detail.from_user}</p></div><div><span className="text-xs font-medium text-muted-foreground">{label("social.toUser", "To user")}</span><p>{detail.to_user}</p></div><div><span className="text-xs font-medium text-muted-foreground">{label("social.groupId", "Group ID")}</span><p>{detail.group_id || "--"}</p></div><div><span className="text-xs font-medium text-muted-foreground">{label("social.relationType", "Relation type")}</span><p>{relationLabel(detail.relation_type)}</p></div><div><span className="text-xs font-medium text-muted-foreground">{t("social.frequency")}</span><p>{detail.frequency}</p></div><div><span className="text-xs font-medium text-muted-foreground">{label("social.lastInteraction", "Last interaction")}</span><p>{detail.last_interaction || "--"}</p></div></div><Button variant="destructive" size="sm" disabled={!hasRevision(detail)} onClick={() => setDeleteOpen(true)}><Trash2 data-icon="inline-start" />{t("common.delete")}</Button></div> : null} form={<>{editFormError ? <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{editFormError}</div> : null}<SocialRelationForm value={editDraft} onChange={(next) => { setEditDraft(next); setEditFieldErrors({}); setEditFormError(null); }} fieldErrors={editFieldErrors} disabled={editSubmitting} mode="edit" /></>} />

      <EntityCreateDialog open={createOpen} onOpenChange={(open) => { if (!open) requestCloseCreate(); }} title={label("social.newRelation", "New Relation")} description={label("social.newRelationDescription", "Create a social relation")} isDirty={createDirty} isSubmitting={createSubmitting} canSubmit={Boolean(createDraft.from_user.trim() && createDraft.to_user.trim() && createDraft.relation_type.trim())} onCancel={requestCloseCreate} onSubmit={createRelation} labels={{ close: t("common.close"), cancel: t("common.cancel"), submit: label("detail.create", "Create"), submitting: label("common.saving", "Saving...") }} form={<>{createFormError ? <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{createFormError}</div> : null}<SocialRelationForm value={createDraft} onChange={(next) => { setCreateDraft(next); setCreateFieldErrors({}); setCreateFormError(null); }} fieldErrors={createFieldErrors} disabled={createSubmitting} mode="create" /></>} />

      <Dialog open={batchTagOpen} onOpenChange={(open) => { if (!open) requestCloseBatchTag(); }}>
        <DialogContent showCloseButton={false} className="sm:max-w-md"><DialogHeader><DialogTitle>{label("social.editTags", "Edit relation tags")}</DialogTitle><DialogDescription>{label("social.batchTagDescription", "Apply tags to selected relations")}</DialogDescription><Button type="button" variant="ghost" size="icon-sm" className="absolute right-3 top-3" aria-label={t("common.close")} disabled={batchTagSubmitting} onClick={requestCloseBatchTag}><span aria-hidden="true">×</span></Button></DialogHeader>{batchTagError ? <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{batchTagError}</div> : null}<div className="flex flex-col gap-4"><Field data-disabled={batchTagSubmitting}><FieldLabel htmlFor="social-batch-operation">{label("social.operation", "Operation")}</FieldLabel><select id="social-batch-operation" aria-label={label("social.operation", "Operation")} className="h-8 w-full rounded-lg border border-input bg-background px-2.5 text-sm text-foreground disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50" value={batchTagDraft.operation} onChange={(event) => setBatchTagDraft((draft) => ({ ...draft, operation: event.currentTarget.value as BatchTagDraft["operation"] }))} disabled={batchTagSubmitting}><option value="add_tags">add_tags</option><option value="remove_tags">remove_tags</option></select></Field><Field data-disabled={batchTagSubmitting}><FieldLabel>{t("field.tags")}</FieldLabel><div onChange={(event) => setBatchTagTypingDirty(Boolean((event.target as HTMLInputElement).value?.trim()))}><TagEditor label={t("field.tags")} getRemoveLabel={(tag) => label("tags.remove", `Remove ${tag}`, tag)} values={batchTagDraft.tags} onChange={(tags) => { setBatchTagTypingDirty(false); setBatchTagDraft((draft) => ({ ...draft, tags })); }} disabled={batchTagSubmitting} /></div></Field></div><DialogFooter><Button type="button" variant="outline" disabled={batchTagSubmitting} onClick={requestCloseBatchTag}>{t("common.cancel")}</Button><Button type="button" disabled={batchTagSubmitting || !batchTagDraft.tags.length || !revisionedSelection} onClick={() => void submitBatchTag()}>{batchTagSubmitting ? label("common.saving", "Saving...") : label("common.apply", "Apply")}</Button></DialogFooter></DialogContent>
      </Dialog>

      <DeleteConfirmDialog open={deleteOpen} title={label("social.deleteRelation", "Delete Relation")} description={detail ? `${detail.from_user} → ${detail.to_user}` : ""} cancelLabel={t("common.cancel")} confirmLabel={label("social.deleteRelation", "Delete Relation")} onCancel={() => setDeleteOpen(false)} onConfirm={() => void executeSingleDelete()} />
      <UnsavedChangesDialog open={pendingDiscard !== null} title={label("config.unsaved.title", "Unsaved changes")} description={label("config.unsaved.description", "Discard your unsaved changes?")} keepEditingLabel={label("config.unsaved.keepEditing", "Keep editing")} discardLabel={label("config.unsaved.discard", "Discard changes and leave")} onKeepEditing={() => setPendingDiscard(null)} onDiscard={() => { if (pendingDiscard === "create") { resetCreate(); setCreateOpen(false); } else if (pendingDiscard === "edit") { cancelEdit(); setDetail(null); } else { setBatchTagDraft({ ...EMPTY_BATCH_TAG }); setBatchTagTypingDirty(false); setBatchTagError(null); setBatchTagOpen(false); } setPendingDiscard(null); }} />
      <EditConflictDialog open={conflict !== null} title={label("social.conflictTitle", "Relation changed")} description={label("social.conflictDescription", "This relation changed while you were editing it.")} loadRemoteLabel={label("config.conflict.loadRemote", "Load remote values")} reapplyLocalLabel={label("social.reapplyLocal", "Reapply local values")} onLoadRemote={loadRemoteValues} onReapplyLocal={reapplyLocalValues} />
    </PageFrame>
  );
}
