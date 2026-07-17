import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Plus, Tag, Trash2, X } from "lucide-react";

import { useI18n } from "@/hooks/useI18n";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Input } from "@/components/ui/Input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/Dialog";
import { MetricGrid, PageContent, PageFrame, PageHeader, PageToolbar } from "@/components/layout/PageLayout";
import { DataTable } from "@/components/data-table/DataTable";
import { DataTablePagination } from "@/components/data-table/DataTablePagination";
import { actionsColumn, selectionColumn } from "@/components/data-table/data-table-columns";
import type { DataTableColumn, DataTableSort } from "@/components/data-table/table-types";
import { DeleteConfirmDialog } from "@/components/editing/DeleteConfirmDialog";
import { EditConflictDialog } from "@/components/editing/EditConflictDialog";
import { EntityCreateDialog } from "@/components/editing/EntityCreateDialog";
import { EntityEditorSheet } from "@/components/editing/EntityEditorSheet";
import { ProfileForm } from "@/components/editing/forms/ProfileForm";
import { UnsavedChangesDialog } from "@/components/editing/UnsavedChangesDialog";
import { dashboardLocale, formatDashboardDate, formatDashboardPercent } from "@/lib/i18n";
import { ApiRequestError, BULK_CONFIRMATION_THRESHOLD, editingErrorDetails, type BatchResult, type EntityEnvelope, type FieldErrors } from "@/types/editing";
import type { ProfileDraft } from "@/types";

interface ProfilesPageProps {
  showToast: (msg: string, isError?: boolean) => void;
  onDirtyChange?: (dirty: boolean) => void;
}

interface ProfileTag {
  name?: string;
  category?: string;
  value?: string;
  confidence: number;
}

interface Profile {
  user_id: string;
  display_name?: string;
  revision?: string;
  group_id?: string;
  tag_count?: number;
  tags?: ProfileTag[];
  top_interests?: string[];
  preferences?: Partial<ProfileDraft["preferences"]>;
  last_seen?: string;
  message_count?: number;
}

interface BatchTagDraft {
  category: string;
  value: string;
  confidence: number;
}

type ProfileIdentity = { user_id: string };
type ProfileEntityEnvelope = EntityEnvelope<Profile>;
type ProfileListResponse = { profiles?: Profile[]; items?: Profile[]; total?: number };
type ProfileDeleteResponse = { deleted?: boolean; identity?: ProfileIdentity };
type ProfileBatchFailure = { identity: ProfileIdentity; code: string; message: string };
type ProfileBatchResponse = Omit<BatchResult<ProfileIdentity>, "failures"> & { failures: ProfileBatchFailure[] };
type ProfileBatchItem = { identity: ProfileIdentity; expected_revision?: string };

function batchItemKey(item: ProfileBatchItem): string {
  return JSON.stringify([item.identity, item.expected_revision]);
}

function hasSameBatchItems(left: ProfileBatchItem[], right: ProfileBatchItem[]): boolean {
  if (left.length !== right.length) return false;
  const rightKeys = new Set(right.map(batchItemKey));
  return left.every((item) => rightKeys.has(batchItemKey(item)));
}

const PAGE_SIZE = 100;
const DEFAULT_SORT: DataTableSort = { id: "last_seen_at", desc: true };
const EMPTY_PROFILE_DRAFT: ProfileDraft = {
  user_id: "",
  display_name: "",
  preferences: { reply_style: "casual", preferred_topics: [], avoided_topics: [], active_hours: [] },
  tags: [],
};
const EMPTY_BATCH_TAG: BatchTagDraft = { category: "interest", value: "", confidence: 0.5 };

function cloneDraft(draft: ProfileDraft): ProfileDraft {
  return {
    ...draft,
    preferences: {
      ...draft.preferences,
      preferred_topics: [...draft.preferences.preferred_topics],
      avoided_topics: [...draft.preferences.avoided_topics],
      active_hours: [...draft.preferences.active_hours],
    },
    tags: draft.tags.map((tag) => ({ ...tag })),
  };
}

function profileDraft(profile: Profile): ProfileDraft {
  const preferences = profile.preferences ?? {};
  return {
    user_id: profile.user_id,
    display_name: profile.display_name ?? "",
    preferences: {
      reply_style: preferences.reply_style ?? "casual",
      preferred_topics: [...(preferences.preferred_topics ?? [])],
      avoided_topics: [...(preferences.avoided_topics ?? [])],
      active_hours: [...(preferences.active_hours ?? [])],
    },
    tags: (profile.tags ?? []).map((tag) => ({
      category: tag.category ?? "interest",
      value: tag.value ?? tag.name ?? "",
      confidence: Number(tag.confidence ?? 0.5),
    })),
  };
}

function profileFromEnvelope(entity: Profile, revision?: string): Profile {
  return { ...entity, revision: revision ?? entity.revision };
}

function isProfile(value: unknown): value is Profile {
  return Boolean(value && typeof value === "object" && typeof (value as Profile).user_id === "string");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function profileEntityEnvelope(value: unknown, message: string): ProfileEntityEnvelope {
  if (!isRecord(value) || !isProfile(value.entity) || typeof value.revision !== "string" || value.revision.length === 0) {
    throw new Error(message);
  }
  return { entity: value.entity, revision: value.revision };
}

function profileListResponse(value: unknown, message: string): ProfileListResponse {
  if (!isRecord(value)) throw new Error(message);
  const profiles = value.profiles ?? value.items;
  if (profiles !== undefined && (!Array.isArray(profiles) || !profiles.every(isProfile))) throw new Error(message);
  if (value.total !== undefined && (typeof value.total !== "number" || !Number.isInteger(value.total) || value.total < 0)) throw new Error(message);
  return {
    profiles: Array.isArray(value.profiles) ? value.profiles : undefined,
    items: Array.isArray(value.items) ? value.items : undefined,
    total: typeof value.total === "number" ? value.total : undefined,
  };
}

function profileBatchResponse(value: unknown, message: string, allowLegacyEmpty = false): ProfileBatchResponse {
  if (allowLegacyEmpty && isRecord(value) && Object.keys(value).length === 0) {
    return { total: 0, succeeded_count: 0, failed_count: 0, succeeded_ids: [], failures: [] };
  }
  if (!isRecord(value) || typeof value.total !== "number" || !Number.isInteger(value.total) || value.total < 0 || !Array.isArray(value.succeeded_ids) || !value.succeeded_ids.every(isProfileIdentity) || !Array.isArray(value.failures) || !value.failures.every(isProfileBatchFailure)) throw new Error(message);
  if (typeof value.succeeded_count !== "number" || !Number.isInteger(value.succeeded_count) || typeof value.failed_count !== "number" || !Number.isInteger(value.failed_count)) throw new Error(message);
  return {
    total: value.total,
    succeeded_count: value.succeeded_count,
    failed_count: value.failed_count,
    succeeded_ids: value.succeeded_ids,
    failures: value.failures,
  };
}

function legacyProfileBatchResponse(value: unknown, message: string): ProfileBatchResponse {
  if (isRecord(value) && Object.keys(value).length === 0) {
    return { total: 0, succeeded_count: 0, failed_count: 0, succeeded_ids: [], failures: [] };
  }
  if (!isRecord(value)
    || typeof value.deleted_count !== "number" || !Number.isInteger(value.deleted_count) || value.deleted_count < 0
    || typeof value.failed_count !== "number" || !Number.isInteger(value.failed_count) || value.failed_count < 0
    || typeof value.total !== "number" || !Number.isInteger(value.total) || value.total < 0
    || !Array.isArray(value.failed_ids) || !value.failed_ids.every((id) => typeof id === "string" && id.length > 0)
    || value.failed_count !== value.failed_ids.length) throw new Error(message);
  return {
    total: value.total,
    succeeded_count: value.deleted_count,
    failed_count: value.failed_count,
    succeeded_ids: [],
    failures: value.failed_ids.map((user_id) => ({
      identity: { user_id },
      code: "legacy_batch_failure",
      message: "Legacy batch delete failed",
    })),
  };
}

function isProfileIdentity(value: unknown): value is ProfileIdentity {
  return isRecord(value) && typeof value.user_id === "string" && value.user_id.length > 0;
}

function isProfileBatchFailure(value: unknown): value is ProfileBatchFailure {
  return isRecord(value) && isProfileIdentity(value.identity) && typeof value.code === "string" && typeof value.message === "string";
}

const PROFILE_FORM_FIELDS = [
  "user_id",
  "display_name",
  "preferences",
  "preferences.reply_style",
  "preferences.preferred_topics",
  "preferences.avoided_topics",
  "preferences.active_hours",
  "preferences.active_hours.0",
  "preferences.active_hours.1",
  "tags",
] as const;

function profileFormFields(draft: ProfileDraft): string[] {
  return [
    ...PROFILE_FORM_FIELDS,
    ...draft.tags.flatMap((_, index) => [
      `tags.${index}`,
      `tags.${index}.category`,
      `tags.${index}.value`,
      `tags.${index}.confidence`,
    ]),
  ];
}

function hasProfileRevision(profile: Profile | undefined): profile is Profile & { revision: string } {
  return typeof profile?.revision === "string" && profile.revision.length > 0;
}

function profileTagTotal(profile: Profile): number {
  return profile.tag_count ?? (profile.tags?.filter((tag) => typeof tag.value === "string" || typeof tag.name === "string").length ?? 0);
}

export function ProfilesPage({ showToast, onDirtyChange }: ProfilesPageProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const label = (key: string, fallback: string, ...args: string[]) => {
    const translated = t(key, ...args);
    return translated === key ? fallback : translated;
  };
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<DataTableSort>(DEFAULT_SORT);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [detail, setDetail] = useState<Profile | null>(null);
  const [editDraft, setEditDraft] = useState<ProfileDraft>(cloneDraft(EMPTY_PROFILE_DRAFT));
  const [editBaseline, setEditBaseline] = useState<ProfileDraft>(cloneDraft(EMPTY_PROFILE_DRAFT));
  const [editMode, setEditMode] = useState(false);
  const [editSubmitting, setEditSubmitting] = useState(false);
  const [editFieldErrors, setEditFieldErrors] = useState<FieldErrors>({});
  const [editFormError, setEditFormError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createDraft, setCreateDraft] = useState<ProfileDraft>(cloneDraft(EMPTY_PROFILE_DRAFT));
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createFieldErrors, setCreateFieldErrors] = useState<FieldErrors>({});
  const [createFormError, setCreateFormError] = useState<string | null>(null);
  const [pendingDiscard, setPendingDiscard] = useState<"create" | "edit" | "batch" | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false);
  const [batchDeleteSubmitting, setBatchDeleteSubmitting] = useState(false);
  const [batchTagOpen, setBatchTagOpen] = useState(false);
  const [batchTagDraft, setBatchTagDraft] = useState<BatchTagDraft>({ ...EMPTY_BATCH_TAG });
  const [batchTagSubmitting, setBatchTagSubmitting] = useState(false);
  const [batchTagError, setBatchTagError] = useState<string | null>(null);
  const [batchTagItems, setBatchTagItems] = useState<ProfileBatchItem[]>([]);
  const [conflict, setConflict] = useState<{ entity: Profile; revision: string } | null>(null);
  const profilesLoadGeneration = useRef(0);
  const batchDeleteSubmittingRef = useRef(false);

  const createDirty = JSON.stringify(createDraft) !== JSON.stringify(EMPTY_PROFILE_DRAFT);
  const editDirty = JSON.stringify(editDraft) !== JSON.stringify(editBaseline);
  const batchTagDirty = JSON.stringify(batchTagDraft) !== JSON.stringify(EMPTY_BATCH_TAG);

  useEffect(() => {
    onDirtyChange?.(createDirty || editDirty || batchTagDirty);
  }, [batchTagDirty, createDirty, editDirty, onDirtyChange]);

  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const fetchProfiles = useCallback(async () => {
    const generation = ++profilesLoadGeneration.current;
    setLoading(true);
    try {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(page * PAGE_SIZE),
        sort_by: sort.id,
        sort_order: sort.desc ? "desc" : "asc",
      });
      const response = profileListResponse(unwrapApiData(
        await apiRequest(`profiles?${params.toString()}`)
      ), label("profiles.invalidList", "Invalid profile list response"));
      const nextProfiles = response.profiles ?? response.items ?? [];
      const nextTotal = Number(response.total ?? nextProfiles.length);
      if (page > 0 && nextProfiles.length === 0 && nextTotal <= page * PAGE_SIZE) {
        if (generation !== profilesLoadGeneration.current) return;
        setSelected(new Set());
        setTotal(nextTotal);
        setPage(Math.max(0, Math.ceil(nextTotal / PAGE_SIZE) - 1));
        return;
      }
      if (generation !== profilesLoadGeneration.current) return;
      setProfiles(nextProfiles);
      setSelected((previous) => new Set([...previous].filter((id) => nextProfiles.some((profile) => profile.user_id === id))));
      setTotal(nextTotal);
    } catch (error) {
      if (generation === profilesLoadGeneration.current) showToast(error instanceof Error ? error.message : String(error), true);
    } finally {
      if (generation === profilesLoadGeneration.current) setLoading(false);
    }
  }, [page, showToast, sort]);

  useEffect(() => { void fetchProfiles(); }, [fetchProfiles]);

  const openDetail = async (userId: string, beginEdit = false) => {
    try {
      const response = unwrapApiData(
        await apiRequest(`profiles/detail?user_id=${encodeURIComponent(userId)}`)
      );
      const profile = isRecord(response) && isProfile(response.entity) && typeof response.revision === "string" && response.revision.length > 0
        ? profileFromEnvelope(response.entity, response.revision)
        : isRecord(response) && isProfile(response.profile)
          ? response.profile
          : isProfile(response) && hasProfileRevision(response)
            ? response
            : null;
      if (!profile) throw new Error(label("profiles.invalidDetail", "Invalid profile detail response"));
      const baseline = profileDraft(profile);
      setDetail(profile);
      setEditBaseline(baseline);
      setEditDraft(cloneDraft(baseline));
      setEditMode(beginEdit);
      setEditFieldErrors({});
      setEditFormError(null);
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error), true);
    }
  };

  const resetCreate = () => {
    setCreateDraft(cloneDraft(EMPTY_PROFILE_DRAFT));
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

  const beginEdit = () => {
    if (!detail) return;
    setEditMode(true);
    setEditFieldErrors({});
    setEditFormError(null);
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

  const createProfile = async () => {
    if (createSubmitting || !createDraft.user_id.trim()) return;
    setCreateSubmitting(true);
    setCreateFieldErrors({});
    setCreateFormError(null);
    try {
      const response = profileEntityEnvelope(unwrapApiData(
        await apiRequest("profiles/create", { method: "POST", body: createDraft })
      ), label("profiles.invalidEntity", "Invalid profile entity response"));
      const created = profileFromEnvelope(response.entity, response.revision);
      resetCreate();
      setCreateOpen(false);
      setTotal((previous) => previous + 1);
      if (page === 0) {
        setProfiles((previous) => [created, ...previous.filter((profile) => profile.user_id !== created.user_id)].slice(0, PAGE_SIZE));
        const baseline = profileDraft(created);
        setDetail(created);
        setEditBaseline(baseline);
        setEditDraft(cloneDraft(baseline));
        setEditMode(false);
      } else {
        showToast(label("profiles.createdOutsideView", "Created profile is outside the current view"));
      }
    } catch (error) {
      const details = editingErrorDetails(error, profileFormFields(createDraft));
      setCreateFieldErrors(details.fieldErrors);
      setCreateFormError(details.formError);
      throw error;
    } finally {
      setCreateSubmitting(false);
    }
  };

  const saveEdit = async () => {
    if (!detail || editSubmitting || !editDirty) return;
    setEditSubmitting(true);
    setEditFieldErrors({});
    setEditFormError(null);
    try {
      const response = profileEntityEnvelope(unwrapApiData(await apiRequest("profiles/update", {
        method: "POST",
        body: {
          identity: { user_id: detail.user_id },
          changes: {
            display_name: editDraft.display_name,
            preferences: editDraft.preferences,
            tags: editDraft.tags,
          },
          expected_revision: detail.revision,
        },
      })), label("profiles.invalidEntity", "Invalid profile entity response"));
      const saved = profileFromEnvelope(response.entity, response.revision);
      const baseline = profileDraft(saved);
      setDetail(saved);
      setEditBaseline(baseline);
      setEditDraft(cloneDraft(baseline));
      setEditMode(false);
      setProfiles((previous) => previous.map((profile) => profile.user_id === saved.user_id ? { ...profile, ...saved } : profile));
    } catch (error) {
      const details = editingErrorDetails(error, profileFormFields(editDraft));
      setEditFieldErrors(details.fieldErrors);
      setEditFormError(details.formError);
      if (error instanceof ApiRequestError && (error.code === "conflict" || error.code === "edit_conflict")) {
        const entity = error.data.current_entity;
        const revision = error.data.current_revision;
        if (entity && typeof entity === "object" && !Array.isArray(entity) && typeof revision === "string") {
          setConflict({ entity: profileFromEnvelope(entity as Profile, revision), revision });
        }
      }
      throw error;
    } finally {
      setEditSubmitting(false);
    }
  };

  const reapplyLocalValues = () => {
    if (!conflict) return;
    const remoteBaseline = profileDraft(conflict.entity);
    const merged = cloneDraft(remoteBaseline);
    if (editDraft.display_name !== editBaseline.display_name) merged.display_name = editDraft.display_name;
    if (JSON.stringify(editDraft.preferences) !== JSON.stringify(editBaseline.preferences)) merged.preferences = editDraft.preferences;
    if (JSON.stringify(editDraft.tags) !== JSON.stringify(editBaseline.tags)) merged.tags = editDraft.tags;
    setDetail(conflict.entity);
    setEditBaseline(remoteBaseline);
    setEditDraft(cloneDraft(merged));
    setEditMode(true);
    setEditFieldErrors({});
    setEditFormError(null);
    setConflict(null);
  };

  const loadRemoteValues = () => {
    if (!conflict) return;
    const baseline = profileDraft(conflict.entity);
    setDetail(conflict.entity);
    setEditBaseline(baseline);
    setEditDraft(cloneDraft(baseline));
    setEditMode(false);
    setConflict(null);
  };

  const executeSingleDelete = async () => {
    if (!detail) return;
    try {
      const body = hasProfileRevision(detail)
        ? { identity: { user_id: detail.user_id }, expected_revision: detail.revision }
        : { user_id: detail.user_id };
      unwrapApiData<ProfileDeleteResponse>(await apiRequest("profiles/delete", { method: "POST", body }));
      showToast(t("toast.profileDeleted"));
      setDeleteOpen(false);
      setDetail(null);
      void fetchProfiles();
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error), true);
    }
  };

  const profileForId = (userId: string) => profiles.find((profile) => profile.user_id === userId);
  const selectedProfiles = Array.from(selected).map(profileForId).filter((profile): profile is Profile => Boolean(profile));
  const revisionedSelection = selectedProfiles.length === selected.size && selectedProfiles.every(hasProfileRevision);
  const batchItems = selectedProfiles.map((profile) => ({
    identity: { user_id: profile.user_id },
    expected_revision: profile.revision,
  }));

  const retainFailedSelection = (response: ProfileBatchResponse) => {
    const failures = response.failures;
    if (!failures.length) return false;
    const failedIds = failures.flatMap((failure) => {
      return [failure.identity.user_id];
    });
    setSelected(new Set(failedIds));
    const failedCount = response.failed_count;
    showToast(label("profiles.batchPartialFailure", `${failedCount} profile operation failed`, String(failedCount)), true);
    return true;
  };

  const executeBatchDelete = async () => {
    if (batchDeleteSubmittingRef.current || !selected.size) return;
    batchDeleteSubmittingRef.current = true;
    setBatchDeleteSubmitting(true);
    setBatchDeleteOpen(false);
    const selectedKeys = new Set(selected);
    const selectedSnapshot = Array.from(selectedKeys).map(profileForId).filter((profile): profile is Profile => Boolean(profile));
    const snapshotRevisioned = selectedSnapshot.length === selectedKeys.size && selectedSnapshot.every(hasProfileRevision);
    const selectedCount = selectedKeys.size;
    try {
      const body = snapshotRevisioned
        ? { action: "delete", items: selectedSnapshot.map((profile) => ({ identity: { user_id: profile.user_id }, expected_revision: profile.revision })), params: {} }
        : { user_ids: Array.from(selectedKeys), action: "delete" };
      const responseData = unwrapApiData(await apiRequest("profiles/batch", { method: "POST", body }));
      const response = snapshotRevisioned
        ? profileBatchResponse(responseData, label("profiles.invalidBatch", "Invalid profile batch response"))
        : legacyProfileBatchResponse(responseData, label("profiles.invalidBatch", "Invalid profile batch response"));
      if (!retainFailedSelection(response)) {
        setSelected((previous) => new Set([...previous].filter((userId) => !selectedKeys.has(userId))));
        showToast(t("toast.batchDeleted", String(selectedCount)));
      }
      void fetchProfiles();
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error), true);
    } finally {
      batchDeleteSubmittingRef.current = false;
      setBatchDeleteSubmitting(false);
    }
  };

  const requestBatchDelete = () => {
    const selectedGroupIds = selectedProfiles.flatMap((profile) => {
      const groupId = profile.group_id?.trim();
      return groupId ? [groupId] : [];
    });
    if (selected.size >= BULK_CONFIRMATION_THRESHOLD || new Set(selectedGroupIds).size > 1) setBatchDeleteOpen(true);
    else void executeBatchDelete();
  };

  const openBatchTag = () => {
    if (!revisionedSelection) return;
    setBatchTagError(null);
    setBatchTagItems(batchItems.map((item) => ({ ...item, identity: { ...item.identity } })));
    setBatchTagOpen(true);
  };

  const requestCloseBatchTag = () => {
    if (batchTagSubmitting) return;
    if (batchTagDirty) setPendingDiscard("batch");
    else {
      setBatchTagDraft({ ...EMPTY_BATCH_TAG });
      setBatchTagError(null);
      setBatchTagOpen(false);
    }
  };

  const submitBatchTag = async (action: "tags_add" | "tags_remove") => {
    const snapshotComplete = batchTagItems.length === selected.size
      && batchTagItems.every((item) => hasProfileRevision({ revision: item.expected_revision, user_id: item.identity.user_id }));
    const currentSelectionComplete = batchItems.length === selected.size
      && batchItems.every((item) => hasProfileRevision({ revision: item.expected_revision, user_id: item.identity.user_id }))
      && hasSameBatchItems(batchTagItems, batchItems);
    if (batchTagSubmitting || !snapshotComplete || !currentSelectionComplete) {
      if (batchTagOpen && !batchTagSubmitting) setBatchTagError("Selected profiles changed; please review the selection");
      return;
    }
    setBatchTagSubmitting(true);
    setBatchTagError(null);
    try {
      const body = {
        action,
        items: batchTagItems,
        params: { tag: { ...batchTagDraft } },
      };
      const response = profileBatchResponse(unwrapApiData(
        await apiRequest("profiles/batch", { method: "POST", body })
      ), label("profiles.invalidBatch", "Invalid profile batch response"));
      if (!retainFailedSelection(response)) {
        setSelected(new Set());
        setBatchTagDraft({ ...EMPTY_BATCH_TAG });
        setBatchTagOpen(false);
      }
      void fetchProfiles();
    } catch (error) {
      setBatchTagError(error instanceof Error ? error.message : String(error));
    } finally {
      setBatchTagSubmitting(false);
    }
  };

  const changePage = (nextPage: number) => {
    setSelected(new Set());
    setPage(nextPage);
  };
  const changeSort = useCallback((next: DataTableSort | null) => {
    setSelected(new Set());
    setPage(0);
    setSort(next ?? DEFAULT_SORT);
  }, []);
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const columns = useMemo<DataTableColumn<Profile>[]>(() => [
    selectionColumn({
      label: t("profiles.selectAll"),
      rowLabel: (profile) => t("profiles.selectProfile", profile.display_name ?? profile.user_id),
    }),
    {
      id: "user_id",
      accessorKey: "user_id",
      header: t("table.userId"),
      meta: {
        label: t("table.userId"),
        serverSortKey: "user_id",
        required: true,
        defaultPin: "left",
        cellClassName: "font-mono text-xs text-muted-foreground",
      },
    },
    {
      id: "display_name",
      accessorKey: "display_name",
      header: t("table.name"),
      meta: { label: t("table.name"), serverSortKey: "display_name" },
      cell: ({ row }) => (
        <Button
          variant="link"
          className="h-auto p-0 font-medium"
          aria-label={t("profiles.openProfile", row.original.display_name ?? row.original.user_id)}
          onClick={() => void openDetail(row.original.user_id)}
        >
          {row.original.display_name ?? "--"}
        </Button>
      ),
    },
    {
      id: "tags",
      accessorFn: profileTagTotal,
      header: t("table.tags"),
      enableSorting: false,
      meta: { label: t("table.tags") },
      cell: ({ row }) => <Badge>{profileTagTotal(row.original)}</Badge>,
    },
    {
      id: "top_interests",
      accessorKey: "top_interests",
      header: t("table.interests"),
      enableSorting: false,
      meta: { label: t("table.interests") },
      cell: ({ row }) => (
        <div className="flex flex-wrap gap-1">
          {(row.original.top_interests ?? []).slice(0, 3).map((interest) => (
            <Badge key={interest} variant="secondary">{interest}</Badge>
          ))}
        </div>
      ),
    },
    {
      id: "last_seen_at",
      accessorFn: (profile) => profile.last_seen,
      header: t("table.lastSeen"),
      meta: {
        label: t("table.lastSeen"),
        serverSortKey: "last_seen_at",
        cellClassName: "text-xs text-muted-foreground",
      },
      cell: ({ row }) => formatDashboardDate(row.original.last_seen, locale),
    },
    actionsColumn({
      label: t("table.rowActions"),
      rowLabel: (profile) => profile.display_name ?? profile.user_id,
      actions: (profile) => [
        { id: "view", label: t("detail.view"), onSelect: () => void openDetail(profile.user_id) },
        { id: "edit", label: t("detail.edit"), onSelect: () => void openDetail(profile.user_id, true) },
        {
          id: "delete",
          label: t("common.delete"),
          destructive: true,
          onSelect: () => {
            const baseline = profileDraft(profile);
            setDetail(profile);
            setEditBaseline(baseline);
            setEditDraft(cloneDraft(baseline));
            setEditMode(false);
            setDeleteOpen(true);
          },
        },
      ],
    }),
  ], [locale, t]);

  return (
    <PageFrame variant="dense" aria-label={t("nav.profiles")}>
      <PageHeader
        title={t("nav.profiles")}
        icon={<Tag />}
        actions={<Button size="sm" onClick={() => setCreateOpen(true)}><Plus data-icon="inline-start" />{label("profiles.newProfile", "New Profile")}</Button>}
      />

      <div className="flex min-h-12 shrink-0 items-center border-b bg-muted/30 px-4 py-2 sm:px-5 lg:px-6">
        <MetricGrid minItemWidth="8rem" className="w-full max-w-md gap-3">
          <div><div className="text-lg font-bold tabular-nums">{total}</div><div className="text-xs text-muted-foreground">{t("stats.profiles")}</div></div>
          <div><div className="text-lg font-bold tabular-nums">{profiles.reduce((sum, profile) => sum + profileTagTotal(profile), 0)}</div><div className="text-xs text-muted-foreground">{t("table.tags")}</div></div>
        </MetricGrid>
      </div>

      <PageContent width="full">
        <DataTable
          tableId="profiles"
          data={profiles}
          columns={columns}
          getRowId={(profile) => profile.user_id}
          sort={sort}
          onSortChange={changeSort}
          selectedRowIds={selected}
          onSelectedRowIdsChange={setSelected}
          currentRowId={detail?.user_id ?? null}
          onRowActivate={(profile) => void openDetail(profile.user_id)}
          loading={loading}
          emptyLabel={t("table.noData")}
          pagination={(
            <DataTablePagination
              page={page}
              pageCount={totalPages}
              total={total}
              onPageChange={changePage}
            />
          )}
        />
      </PageContent>

      {selected.size > 0 ? <PageToolbar className="border-b-0 border-t bg-muted/40 animate-slide-up">
        <span className="text-sm font-medium">{t("select.selected", String(selected.size))}</span>
        <Button variant="outline" size="sm" disabled={!revisionedSelection} onClick={openBatchTag}>{label("profiles.editTags", "Edit tags")}</Button>
        <Button variant="destructive" size="sm" disabled={batchDeleteSubmitting} onClick={requestBatchDelete}><Trash2 data-icon="inline-start" />{t("common.delete")}</Button>
        <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}><X data-icon="inline-start" />{t("common.clear")}</Button>
      </PageToolbar> : null}

      <EntityEditorSheet
        open={detail !== null}
        onOpenChange={(open) => { if (!open) requestCloseDetail(); }}
        title={detail ? t("detail.profileOf", detail.display_name ?? detail.user_id) : ""}
        description={t("profiles.details")}
        mode={editMode ? "edit" : "view"}
        isDirty={editDirty}
        isSubmitting={editSubmitting}
        canSave
        onBeginEdit={beginEdit}
        onCancel={cancelEdit}
        onSave={saveEdit}
        labels={{ edit: t("detail.edit"), close: t("common.close"), cancel: t("common.cancel"), save: t("common.save"), saving: label("common.saving", "Saving...") }}
        view={detail ? <div className="flex flex-col gap-4 text-sm">
          <div className="grid grid-cols-2 gap-3"><div><span className="text-xs font-medium text-muted-foreground">{t("table.userId")}</span><p className="font-mono text-sm">{detail.user_id}</p></div><div><span className="text-xs font-medium text-muted-foreground">{t("table.messages")}</span><p>{detail.message_count ?? "--"}</p></div></div>
          {detail.tags?.length ? <div><h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold"><Tag />{t("table.tags")}</h4><div className="flex flex-col gap-1.5">{detail.tags.map((tag, index) => <div key={`${tag.value ?? tag.name}-${index}`} className="flex items-center justify-between"><span>{tag.value ?? tag.name}</span><span>{formatDashboardPercent(tag.confidence ?? 0, locale, { maximumFractionDigits: 0 })}</span></div>)}</div></div> : null}
          <Button variant="destructive" size="sm" onClick={() => { if (hasProfileRevision(detail)) setDeleteOpen(true); else void executeSingleDelete(); }}><Trash2 data-icon="inline-start" />{hasProfileRevision(detail) ? t("common.delete") : t("detail.deleteProfile")}</Button>
        </div> : null}
        form={<ProfileForm value={editDraft} onChange={(next) => { setEditDraft(next); setEditFieldErrors({}); setEditFormError(null); }} fieldErrors={editFieldErrors} formErrors={editFormError ? [editFormError] : []} disabled={editSubmitting} mode="edit" />}
      />

      <EntityCreateDialog
        open={createOpen}
        onOpenChange={(open) => { if (!open) requestCloseCreate(); }}
        title={label("profiles.newProfile", "New Profile")}
        description={label("profiles.newProfileDescription", "Create a user profile")}
        isDirty={createDirty}
        isSubmitting={createSubmitting}
        canSubmit={Boolean(createDraft.user_id.trim())}
        onCancel={requestCloseCreate}
        onSubmit={createProfile}
        labels={{ close: t("common.close"), cancel: t("common.cancel"), submit: t("detail.create"), submitting: label("common.saving", "Saving...") }}
        form={<ProfileForm value={createDraft} onChange={(next) => { setCreateDraft(next); setCreateFieldErrors({}); setCreateFormError(null); }} fieldErrors={createFieldErrors} formErrors={createFormError ? [createFormError] : []} disabled={createSubmitting} mode="create" />}
      />

      <Dialog open={batchTagOpen} onOpenChange={(open) => { if (!open) requestCloseBatchTag(); }}>
        <DialogContent showCloseButton={false} className="sm:max-w-md">
          <DialogHeader><DialogTitle>{label("profiles.editTags", "Edit tags")}</DialogTitle><DialogDescription>{label("profiles.batchTagDescription", "Apply a tag to selected profiles")}</DialogDescription><Button type="button" variant="ghost" size="icon-sm" className="absolute right-3 top-3" aria-label={t("common.close")} disabled={batchTagSubmitting} onClick={requestCloseBatchTag}><X aria-hidden="true" /></Button></DialogHeader>
          {batchTagError ? <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{batchTagError}</div> : null}
          <div className="flex flex-col gap-4"><label className="flex flex-col gap-2 text-sm font-medium">{label("profile.tagCategory", "Tag category")}<Input aria-label={label("profile.tagCategory", "Tag category")} disabled={batchTagSubmitting} value={batchTagDraft.category} onChange={(event) => setBatchTagDraft((draft) => ({ ...draft, category: event.currentTarget.value }))} /></label><label className="flex flex-col gap-2 text-sm font-medium">{label("profile.tagValue", "Tag value")}<Input aria-label={label("profile.tagValue", "Tag value")} disabled={batchTagSubmitting} value={batchTagDraft.value} onChange={(event) => setBatchTagDraft((draft) => ({ ...draft, value: event.currentTarget.value }))} /></label><label className="flex flex-col gap-2 text-sm font-medium">{label("profile.tagConfidence", "Tag confidence")}<Input aria-label={label("profile.tagConfidence", "Tag confidence")} type="number" min="0" max="1" step="0.01" disabled={batchTagSubmitting} value={batchTagDraft.confidence} onChange={(event) => { const value = Number(event.currentTarget.value); if (Number.isFinite(value) && value >= 0 && value <= 1) setBatchTagDraft((draft) => ({ ...draft, confidence: value })); }} /></label></div>
          <DialogFooter><Button type="button" variant="outline" disabled={batchTagSubmitting} onClick={requestCloseBatchTag}>{t("common.cancel")}</Button><Button type="button" variant="outline" disabled={batchTagSubmitting || !batchTagDraft.value.trim()} onClick={() => void submitBatchTag("tags_remove")}>{label("profiles.removeTag", "Remove tag")}</Button><Button type="button" disabled={batchTagSubmitting || !batchTagDraft.value.trim()} onClick={() => void submitBatchTag("tags_add")}>{label("profiles.addTag", "Add tag")}</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <UnsavedChangesDialog open={pendingDiscard !== null} title={pendingDiscard === "create" ? label("profiles.newProfile", "New Profile") : label("config.unsaved.title", "Unsaved changes")} description={label("config.unsaved.description", "Discard your unsaved changes?")} keepEditingLabel={label("config.unsaved.keepEditing", "Keep editing")} discardLabel={label("config.unsaved.discard", "Discard changes and leave")} onKeepEditing={() => setPendingDiscard(null)} onDiscard={() => { if (pendingDiscard === "create") { resetCreate(); setCreateOpen(false); } else if (pendingDiscard === "batch") { setBatchTagDraft({ ...EMPTY_BATCH_TAG }); setBatchTagError(null); setBatchTagOpen(false); } else { cancelEdit(); setDetail(null); } setPendingDiscard(null); }} />
      <DeleteConfirmDialog open={deleteOpen} title={t("detail.deleteProfile")} description={detail?.display_name ?? detail?.user_id ?? ""} cancelLabel={t("common.cancel")} confirmLabel={t("detail.deleteProfile")} onCancel={() => setDeleteOpen(false)} onConfirm={() => void executeSingleDelete()} />
      <DeleteConfirmDialog open={batchDeleteOpen} title={label("profiles.confirmDelete", "Confirm deletion")} description={t("filter.deleteSelected")} cancelLabel={t("common.cancel")} confirmLabel={t("common.delete")} confirmationRequirement={{ label: label("profiles.confirmDeletePhrase", `Type ${selected.size} to confirm`), expectedText: String(selected.size) }} onCancel={() => setBatchDeleteOpen(false)} onConfirm={() => { setBatchDeleteOpen(false); void executeBatchDelete(); }} />
      <EditConflictDialog open={conflict !== null} title={label("profiles.conflictTitle", "Profile changed")} description={label("profiles.conflictDescription", "This profile changed while you were editing it.")} loadRemoteLabel={label("config.conflict.loadRemote", "Load remote values")} reapplyLocalLabel={label("profiles.reapplyLocal", "Reapply local values")} onLoadRemote={loadRemoteValues} onReapplyLocal={reapplyLocalValues} />
    </PageFrame>
  );
}
