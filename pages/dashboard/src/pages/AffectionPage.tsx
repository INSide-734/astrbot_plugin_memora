import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Heart, RefreshCw, Smile, TrendingUp, Users, Zap, Trash2 } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { useGroups } from "@/hooks/useGroups";
import { apiGet, apiPost, unwrapApiData } from "@/lib/bridge";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Progress } from "@/components/ui/Progress";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from "@/components/ui/Select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DeleteConfirmDialog } from "@/components/editing/DeleteConfirmDialog";
import { EditConflictDialog } from "@/components/editing/EditConflictDialog";
import { EntityCreateDialog } from "@/components/editing/EntityCreateDialog";
import { EntityEditorSheet } from "@/components/editing/EntityEditorSheet";
import { PageContent, PageFrame, PageHeader, MetricGrid } from "@/components/layout/PageLayout";
import { AffectionForm } from "@/components/editing/forms/AffectionForm";
import { MoodForm } from "@/components/editing/forms/MoodForm";
import { MOOD_TYPES } from "@/lib/constants";
import { dashboardLocale, formatDashboardPercent, translateEnum } from "@/lib/i18n";
import type { AffectionDraft, AffectionStatus, AffectionUserEntry, MoodDraft } from "@/types";
import type { FieldErrors } from "@/types/editing";

interface AffectionPageProps { showToast: (msg: string, isError?: boolean) => void; onDirtyChange?: (dirty: boolean) => void; }
type User = AffectionUserEntry & { revision?: string };
type History = { start_time?: number; duration_hours?: number; mood_type?: string; intensity?: number; description?: string };
type EntityEnvelope = { entity?: User; revision?: string };
type BatchDeleteSnapshot = { selectedKeys: Set<string>; items: Array<{ identity: { user_id: string; group_id: string }; expected_revision: string }> };
type MoodResult = { mood_type: string; intensity: number; duration_hours?: number; description: string; is_active?: boolean };

const emptyUser: AffectionDraft = { user_id: "", group_id: "", affection_score: 0 };
const emptyMood: MoodDraft = { group_id: "", mood_type: "", intensity: 0.5, duration_hours: 4, description: "" };
const cloneUser = (v: AffectionDraft): AffectionDraft => ({ ...v });
const cloneMood = (v: MoodDraft): MoodDraft => ({ ...v });
const userKey = (u: Pick<User, "user_id" | "group_id">) => `${u.group_id}:${u.user_id}`;

function validUser(value: unknown): value is User {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return typeof v.user_id === "string" && Boolean(v.user_id.trim()) && typeof v.group_id === "string" && Boolean(v.group_id.trim())
    && typeof v.affection_score === "number" && Number.isInteger(v.affection_score) && v.affection_score >= -100 && v.affection_score <= 100
    && typeof v.affection_level === "string" && typeof v.level_name === "string"
    && typeof v.interaction_count === "number" && Number.isInteger(v.interaction_count) && v.interaction_count >= 0
    && typeof v.last_interaction === "number" && Number.isFinite(v.last_interaction)
    && typeof v.revision === "string" && Boolean(v.revision.trim());
}
function authoritativeUser(value: unknown, revision?: unknown): User | null {
  if (!value || typeof value !== "object") return null;
  const entityRevision = (typeof revision === "string" && revision.trim()) ? revision : (value as User).revision;
  const candidate = { ...(value as User), revision: entityRevision };
  return validUser(candidate) ? candidate : null;
}
function validMood(value: unknown): value is MoodResult {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const v = value as Record<string, unknown>;
  const moodType = v.mood_type;
  if (typeof moodType !== "string" || !MOOD_TYPES.some((m) => m.type.toLowerCase() === moodType.toLowerCase())) return false;
  if (typeof v.intensity !== "number" || !Number.isFinite(v.intensity) || v.intensity < 0.1 || v.intensity > 1) return false;
  if (typeof v.duration_hours !== "number" || !Number.isFinite(v.duration_hours) || v.duration_hours < 0.25 || v.duration_hours > 168) return false;
  if (typeof v.description !== "string") return false;
  if (v.start_time !== undefined && (typeof v.start_time !== "number" || !Number.isFinite(v.start_time))) return false;
  if (v.is_active !== undefined && typeof v.is_active !== "boolean") return false;
  return true;
}
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error); }
function validDeleteUserIdentity(value: unknown): value is { user_id: string; group_id: string } { return Boolean(value && typeof value === "object" && !Array.isArray(value) && typeof (value as Record<string, unknown>).user_id === "string" && Boolean(((value as Record<string, unknown>).user_id as string).trim()) && typeof (value as Record<string, unknown>).group_id === "string" && Boolean(((value as Record<string, unknown>).group_id as string).trim())); }
function validBatchFailure(value: unknown): value is { code: string; message: string; identity: unknown } { if (!value || typeof value !== "object" || Array.isArray(value)) return false; const v = value as Record<string, unknown>; return typeof v.code === "string" && Boolean(v.code.trim()) && typeof v.message === "string" && Boolean(v.message.trim()); }
  function validateAffectionBatch(value: unknown, items: BatchDeleteSnapshot["items"]): Set<string> { if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid affection batch response"); const v = value as Record<string, unknown>; if (![v.total, v.succeeded_count, v.failed_count].every((n) => typeof n === "number" && Number.isInteger(n) && n >= 0) || v.total !== items.length || v.total !== (v.succeeded_count as number) + (v.failed_count as number) || !Array.isArray(v.succeeded_ids) || !Array.isArray(v.failures) || v.succeeded_ids.length !== v.succeeded_count || v.failures.length !== v.failed_count) throw new Error("Invalid affection batch response"); const allowed = new Set(items.map((x) => `${x.identity.group_id}:${x.identity.user_id}`)); const all = [...v.succeeded_ids, ...v.failures.map((x) => x && typeof x === "object" ? (x as Record<string, unknown>).identity : null)].map((x) => { if (!validDeleteUserIdentity(x)) throw new Error("Invalid affection batch response"); const key = `${x.group_id}:${x.user_id}`; if (!allowed.has(key)) throw new Error("Invalid affection batch response"); return key; }); if (new Set(all).size !== all.length || v.failures.some((x) => !validBatchFailure(x))) throw new Error("Invalid affection batch response"); return new Set(v.failures.map((x) => { const id = (x as { identity: { group_id: string; user_id: string } }).identity; return `${id.group_id}:${id.user_id}`; })); }

export function AffectionPage({ showToast, onDirtyChange }: AffectionPageProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const { groups, groupId, setGroupId } = useGroups();
  const [data, setData] = useState<AffectionStatus | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [history, setHistory] = useState<History[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [createOpen, setCreateOpen] = useState(false);
  const [createDraft, setCreateDraft] = useState<AffectionDraft>(cloneUser(emptyUser));
  const [createError, setCreateError] = useState("");
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const createDirty = createDraft.user_id !== "" || createDraft.group_id !== "" || createDraft.affection_score !== 0;
  const [detail, setDetail] = useState<User | null>(null);
  const [editDraft, setEditDraft] = useState<AffectionDraft>(cloneUser(emptyUser));
  const [editBaseline, setEditBaseline] = useState<AffectionDraft>(cloneUser(emptyUser));
  const [editMode, setEditMode] = useState<"view" | "edit">("view");
  const [editError, setEditError] = useState("");
  const [editSubmitting, setEditSubmitting] = useState(false);
  const [conflict, setConflict] = useState<{ latest: User; local: AffectionDraft; revision: string } | null>(null);
  const editDirty = Boolean(detail && JSON.stringify(editDraft) !== JSON.stringify(editBaseline));
  const [moodOpen, setMoodOpen] = useState(false);
  const [moodDraft, setMoodDraft] = useState<MoodDraft>(cloneMood(emptyMood));
  const [moodBaseline, setMoodBaseline] = useState<MoodDraft>(cloneMood(emptyMood));
  const [moodError, setMoodError] = useState("");
  const [moodSubmitting, setMoodSubmitting] = useState(false);
  const moodDirty = moodOpen && JSON.stringify(moodDraft) !== JSON.stringify(moodBaseline);
  const [deleteTarget, setDeleteTarget] = useState<User | null>(null);
  const [batchDeleteOpen, setBatchDeleteOpen] = useState(false);
  const [batchDeleteSnapshot, setBatchDeleteSnapshot] = useState<BatchDeleteSnapshot | null>(null);
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [resetOpen, setResetOpen] = useState(false);
  const [resetSubmitting, setResetSubmitting] = useState(false);
  const loadGeneration = useRef(0);

  useEffect(() => { onDirtyChange?.(Boolean(editDirty || createDirty || moodDirty)); }, [onDirtyChange, editDirty, createDirty, moodDirty]);

  const load = useCallback(async (requestedOffset = offset) => {
    if (!groupId) return;
    const current = ++loadGeneration.current;
    setLoading(true); setSelected(new Set());
    try {
      const [statusResponse, usersResponse, historyResponse] = await Promise.all([
        apiGet("affection/status", { group_id: groupId }),
        apiGet("affection/users", { group_id: groupId, limit: "50", offset: String(requestedOffset) }),
        apiGet("affection/moods/history", { group_id: groupId, limit: "50" }),
      ]);
      const status = unwrapApiData<AffectionStatus>(statusResponse);
      const userData = unwrapApiData<{ users?: User[]; total?: number; limit?: number; offset?: number }>(usersResponse);
      const historyData = unwrapApiData<{ history?: History[] }>(historyResponse);
      if (current !== loadGeneration.current) return;
      setData(status); setUsers(Array.isArray(userData.users) ? userData.users : []); setTotal(Number(userData.total ?? 0)); setOffset(Number(userData.offset ?? requestedOffset)); setHistory(Array.isArray(historyData.history) ? historyData.history : []);
    } catch (error) {
      if (current === loadGeneration.current) { setData(null); setUsers([]); setHistory([]); showToast(`Error: ${errorMessage(error)}`, true); }
    } finally { if (current === loadGeneration.current) setLoading(false); }
  }, [groupId, offset, showToast]);

  useEffect(() => { void load(0); }, [groupId]);
  const refresh = () => { setOffset(0); void load(0); };
  const changeGroup = (next: string) => { setSelected(new Set()); setOffset(0); setData(null); setGroupId(next); };
  const changePage = (next: number) => { setSelected(new Set()); setOffset(next); void load(next); };

  const beginCreate = () => { setCreateDraft({ ...emptyUser, group_id: groupId }); setCreateError(""); setCreateOpen(true); };
  const submitCreate = async () => {
    if (createSubmitting) return;
    const snapshot = cloneUser(createDraft); setCreateSubmitting(true); setCreateError("");
    try {
      const result = unwrapApiData<EntityEnvelope>(await apiPost("affection/users/create", { group_id: snapshot.group_id, user_id: snapshot.user_id, affection_score: snapshot.affection_score }));
      const entity = authoritativeUser(result.entity, result.revision);
      if (!entity) throw new Error("Invalid affection entity response");
      setUsers((old) => [entity, ...old.filter((u) => userKey(u) !== userKey(entity))]); setTotal((n) => n + 1); setDetail(entity); setEditBaseline({ group_id: entity.group_id, user_id: entity.user_id, affection_score: entity.affection_score }); setEditDraft({ group_id: entity.group_id, user_id: entity.user_id, affection_score: entity.affection_score }); setEditMode("view"); setCreateDraft(cloneUser(emptyUser)); setCreateOpen(false);
    } catch (error) { setCreateError(errorMessage(error)); } finally { setCreateSubmitting(false); }
  };
  const openDetail = (user: User) => { setDetail(user); setEditBaseline({ group_id: user.group_id, user_id: user.user_id, affection_score: user.affection_score }); setEditDraft({ group_id: user.group_id, user_id: user.user_id, affection_score: user.affection_score }); setEditMode("view"); setEditError(""); };
  const beginEdit = () => setEditMode("edit");
  const saveEdit = async () => {
    if (!detail || editSubmitting) return;
    const local = cloneUser(editDraft); const snapshot = { identity: { user_id: detail.user_id, group_id: detail.group_id }, changes: { affection_score: local.affection_score }, expected_revision: detail.revision };
    setEditSubmitting(true); setEditError("");
    try {
      const raw = await apiPost("affection/users/update", snapshot);
      if (raw.status === "error" && raw.code === "edit_conflict") { const d = raw.data as { current_entity?: User; current_revision?: string } | undefined; const currentEntity = d?.current_entity; const currentRevision = d?.current_revision; const latest = currentEntity && currentRevision ? { ...currentEntity, revision: currentRevision } : null; if (validUser(latest) && currentRevision) setConflict({ latest, local, revision: currentRevision }); else throw new Error("Invalid affection conflict response"); return; }
      const result = unwrapApiData<EntityEnvelope>(raw); const entity = authoritativeUser(result.entity, result.revision); if (!entity) throw new Error("Invalid affection entity response");
      setDetail(entity); setUsers((old) => old.map((u) => userKey(u) === userKey(entity) ? entity : u)); setEditBaseline({ group_id: entity.group_id, user_id: entity.user_id, affection_score: entity.affection_score }); setEditDraft({ group_id: entity.group_id, user_id: entity.user_id, affection_score: entity.affection_score }); setEditMode("view");
    } catch (error) { setEditError(errorMessage(error)); } finally { setEditSubmitting(false); }
  };
  const loadLatest = () => { if (!conflict) return; setDetail(conflict.latest); setEditBaseline({ group_id: conflict.latest.group_id, user_id: conflict.latest.user_id, affection_score: conflict.latest.affection_score }); setEditDraft({ group_id: conflict.latest.group_id, user_id: conflict.latest.user_id, affection_score: conflict.latest.affection_score }); setEditMode("view"); setConflict(null); };
  const reapplyLocal = () => { if (!conflict) return; setDetail(conflict.latest); setEditBaseline({ group_id: conflict.latest.group_id, user_id: conflict.latest.user_id, affection_score: conflict.latest.affection_score }); setEditDraft(conflict.local); setEditMode("edit"); setConflict(null); };

  const executeDelete = async () => { if (!deleteTarget || deleteSubmitting || deleteConfirmation !== deleteTarget.user_id) return; const target = { ...deleteTarget }; setDeleteSubmitting(true); try { const result = unwrapApiData(await apiPost("affection/users/delete", { identity: { user_id: target.user_id, group_id: target.group_id }, expected_revision: target.revision })); const responseIdentity = result && typeof result === "object" ? (result as Record<string, unknown>).identity : null; if (!result || typeof result !== "object" || (result as Record<string, unknown>).deleted !== true || !responseIdentity || typeof responseIdentity !== "object" || (responseIdentity as Record<string, unknown>).user_id !== target.user_id || (responseIdentity as Record<string, unknown>).group_id !== target.group_id) throw new Error("Invalid affection delete response"); setUsers((old) => old.filter((u) => userKey(u) !== userKey(target))); setSelected((old) => { const n = new Set(old); n.delete(userKey(target)); return n; }); setTotal((n) => Math.max(0, n - 1)); setDeleteTarget(null); setDetail(null); } catch (error) { showToast(errorMessage(error), true); } finally { setDeleteSubmitting(false); } };
  const selectedUsers = useMemo(() => users.filter((u) => selected.has(userKey(u))), [users, selected]);
  const openBatchDelete = () => { if (selectedUsers.length !== selected.size || !selectedUsers.every((u) => u.revision)) return; setBatchDeleteSnapshot({ selectedKeys: new Set(selected), items: selectedUsers.map((u) => ({ identity: { user_id: u.user_id, group_id: u.group_id }, expected_revision: u.revision as string })) }); setBatchDeleteOpen(true); };
  const executeBatchDelete = async () => { if (deleteSubmitting || !batchDeleteSnapshot) return; const { selectedKeys, items: snapshot } = batchDeleteSnapshot; setDeleteSubmitting(true); try { const result = unwrapApiData(await apiPost("affection/users/batch", { action: "delete", items: snapshot })); const failed = validateAffectionBatch(result, snapshot); setSelected(failed); setUsers((old) => old.filter((u) => !selectedKeys.has(userKey(u)) || failed.has(userKey(u)))); setTotal((n) => Math.max(0, n - (selectedKeys.size - failed.size))); setBatchDeleteOpen(false); setBatchDeleteSnapshot(null); } catch (error) { showToast(errorMessage(error), true); } finally { setDeleteSubmitting(false); } };

  const openMood = () => { const current = data?.current_mood; const next = { ...emptyMood, group_id: groupId, mood_type: String(current?.mood_type ?? "").toLowerCase(), intensity: current?.intensity ?? 0.5, duration_hours: current?.duration_hours ?? emptyMood.duration_hours, description: current?.description ?? "" }; setMoodDraft(next); setMoodBaseline(cloneMood(next)); setMoodError(""); setMoodOpen(true); };
  const submitMood = async () => { if (moodSubmitting) return; const snapshot = cloneMood(moodDraft); setMoodSubmitting(true); setMoodError(""); try { const result = unwrapApiData<MoodResult>(await apiPost("affection/mood/set", snapshot)); if (!validMood(result)) throw new Error("Invalid mood response"); const authoritative = { ...snapshot, group_id: groupId, mood_type: result.mood_type.toLowerCase(), intensity: result.intensity, duration_hours: result.duration_hours ?? snapshot.duration_hours, description: result.description }; setMoodDraft(authoritative); setMoodBaseline(cloneMood(authoritative)); setData((old) => old ? { ...old, current_mood: { ...old.current_mood, ...result } } : old); setMoodOpen(false); } catch (error) { setMoodError(errorMessage(error)); } finally { setMoodSubmitting(false); } };
  const resetMood = async () => { if (resetSubmitting) return; setResetSubmitting(true); setMoodError(""); try { const result = unwrapApiData<MoodResult>(await apiPost("affection/mood/reset", { group_id: groupId })); if (!validMood(result)) throw new Error("Invalid mood response"); const authoritative = { ...emptyMood, group_id: groupId, mood_type: result.mood_type.toLowerCase(), intensity: result.intensity, duration_hours: result.duration_hours ?? emptyMood.duration_hours, description: result.description }; setMoodDraft(authoritative); setMoodBaseline(cloneMood(authoritative)); setData((old) => old ? { ...old, current_mood: { ...old.current_mood, ...result } } : old); setResetOpen(false); } catch (error) { setMoodError(errorMessage(error)); } finally { setResetSubmitting(false); } };
  const level = (u: AffectionUserEntry) => { const raw = String(u.affection_level ?? "").trim(); return raw ? translateEnum(t, "affection.levelValue", raw) : u.level_name || "--"; };
  const mood = data?.current_mood; const moodType = String(mood?.mood_type ?? "").toUpperCase(); const moodMeta = MOOD_TYPES.find((m) => m.type === moodType);
  const cancelLabel = t("common.cancel");
  const deleteTitle = t("affection.deleteUser");

  return <PageFrame variant="standard" aria-label={t("affection.title")} aria-hidden={false}><PageHeader title={t("affection.title")} icon={<Heart />} actions={<><Select value={groupId} onValueChange={(v) => v && changeGroup(v)} disabled={!groups.length}><SelectTrigger className="w-36 text-xs"><span>{groupId || t("jargon.allGroups")}</span></SelectTrigger><SelectContent><SelectGroup>{groups.map((g) => <SelectItem key={g.group_id} value={g.group_id} onClick={() => changeGroup(g.group_id)}>{g.group_id}{g.message_count ? ` (${g.message_count})` : ""}</SelectItem>)}</SelectGroup></SelectContent></Select><Button variant="outline" onClick={refresh}><RefreshCw data-icon="inline-start" />{t("common.refresh")}</Button></>} />
    <PageContent className="flex flex-col gap-6">{loading ? <p className="py-12 text-center text-sm text-muted-foreground">{t("table.loading")}</p> : !data ? <p className="py-12 text-center text-sm text-muted-foreground">{t("affection.noData")}</p> : <>
      <MetricGrid minItemWidth="18rem"><Card><CardHeader><CardTitle className="flex items-center gap-2"><Smile />{t("affection.mood")}</CardTitle></CardHeader><CardContent className="flex flex-wrap items-center gap-6"><div className="flex min-w-0 items-center gap-3"><span className="text-4xl">{moodMeta?.emoji ?? "🤖"}</span><div><div className="text-lg font-semibold">{moodMeta ? t(`mood.${moodMeta.type}`) : mood?.mood_type ?? "—"}</div><div className="mt-0.5 text-xs text-muted-foreground">{mood?.description ?? ""}</div></div></div><div className="min-w-[10rem] flex-1"><div className="mb-1 flex items-center justify-between text-xs text-muted-foreground"><span>{t("affection.moodIntensity")}</span><span>{mood?.intensity != null ? formatDashboardPercent(mood.intensity, locale, { maximumFractionDigits: 0 }) : "—"}</span></div><Progress aria-label={t("affection.moodIntensity")} value={mood?.intensity ?? 0} className="h-2" /></div><Button size="sm" onClick={openMood}>{t("affection.moodTitle")}</Button></CardContent></Card><Card><CardHeader><CardTitle className="flex items-center gap-2"><Users />{t("affection.leaderboard")}</CardTitle></CardHeader><CardContent className="grid grid-cols-2 gap-4"><div><div className="text-2xl font-semibold">{data.user_count}</div><div className="text-xs text-muted-foreground">{t("jargon.users")}</div></div><div><div className="text-2xl font-semibold">{data.total_affection}/{data.max_total_affection}</div><div className="text-xs text-muted-foreground">{t("affection.score")}</div></div></CardContent></Card></MetricGrid>
      <Card><CardHeader><CardTitle className="flex items-center gap-2"><Zap />{t("affection.emotions")}</CardTitle></CardHeader><CardContent><MetricGrid minItemWidth="7rem" className="gap-3">{MOOD_TYPES.map((mt) => <div key={mt.type} className={`flex flex-col items-center gap-1.5 rounded-md border p-3 ${moodType === mt.type ? "border-primary bg-primary/5" : "border-border"}`}><span className="text-xl">{mt.emoji}</span><span className="text-xs font-medium">{t(`mood.${mt.type}`)}</span>{moodType === mt.type ? <Badge>{t("status.active")}</Badge> : null}</div>)}</MetricGrid></CardContent></Card>
      <section aria-label={t("affection.leaderboard")}><Card className="gap-0 py-0"><CardHeader className="border-b py-4"><CardTitle><h2 className="flex items-center gap-2"><TrendingUp />{t("affection.leaderboard")}</h2></CardTitle></CardHeader><Table><TableHeader><TableRow><TableHead>#</TableHead><TableHead>{t("table.userId")}</TableHead><TableHead>{t("affection.score")}</TableHead><TableHead>{t("affection.level")}</TableHead><TableHead>{t("affection.interactions")}</TableHead></TableRow></TableHeader><TableBody>{data.top_users.map((u, i) => <TableRow key={userKey(u)}><TableCell>{i + 1}</TableCell><TableCell><span aria-label={u.user_id} /></TableCell><TableCell><Progress aria-label={`${u.user_id} ${t("affection.score")}`} value={u.affection_score} min={-100} max={100} /></TableCell><TableCell><Badge variant="secondary" aria-label={level(u)} /></TableCell><TableCell>{u.interaction_count}</TableCell></TableRow>)}</TableBody></Table></Card></section>
      <Card className="gap-0 py-0"><CardHeader className="flex flex-row items-center justify-between border-b py-4"><CardTitle>{t("affection.allUsers")}</CardTitle><div className="flex gap-2"><Button onClick={beginCreate}>{t("affection.newUser")}</Button>{selected.size > 0 ? <><span>{t("affection.selected", String(selected.size))}</span><Button variant="destructive" disabled={deleteSubmitting} onClick={openBatchDelete}><Trash2 data-icon="inline-start" />{t("affection.deleteSelected")}</Button></> : null}</div></CardHeader><Table><TableHeader><TableRow><TableHead><Checkbox aria-label={t("affection.selectAll")} checked={users.length > 0 && selected.size === users.length} onCheckedChange={() => setSelected(selected.size === users.length ? new Set() : new Set(users.map(userKey)))} /></TableHead><TableHead>{t("table.userId")}</TableHead><TableHead>{t("affection.score")}</TableHead><TableHead>{t("affection.level")}</TableHead><TableHead>{t("table.actions")}</TableHead></TableRow></TableHeader><TableBody>{users.map((u) => <TableRow key={userKey(u)}><TableCell><Checkbox aria-label={t("affection.selectUser", u.user_id)} checked={selected.has(userKey(u))} onCheckedChange={() => setSelected((old) => { const n = new Set(old); n.has(userKey(u)) ? n.delete(userKey(u)) : n.add(userKey(u)); return n; })} /></TableCell><TableCell>{u.user_id}</TableCell><TableCell>{u.affection_score}</TableCell><TableCell>{detail ? <span aria-label={level(u)} /> : level(u)}</TableCell><TableCell><Button variant="ghost" size="sm" aria-label={`${t("detail.edit")} ${u.user_id}`} onClick={() => openDetail(u)}>{t("detail.view")}</Button><Button variant="ghost" size="sm" aria-label={`${t("common.delete")} ${u.user_id}`} onClick={() => setDeleteTarget(u)}>{t("common.delete")}</Button></TableCell></TableRow>)}</TableBody></Table><div className="flex items-center justify-end gap-2 p-3"><Button aria-label={t("pagination.previous")} disabled={offset <= 0} onClick={() => changePage(Math.max(0, offset - 50))}>{t("pagination.previous")}</Button><span>{offset + 1}-{Math.min(offset + 50, total || users.length)} / {total || users.length}</span><Button aria-label={t("pagination.next")} disabled={offset + 50 >= total} onClick={() => changePage(offset + 50)}>{t("pagination.next")}</Button></div></Card>
      <Card><CardHeader><CardTitle><h2>{t("affection.moodHistory")}</h2></CardTitle></CardHeader><Table><TableHeader><TableRow><TableHead>{t("affection.historyStart")}</TableHead><TableHead>{t("affection.moodDuration")}</TableHead><TableHead>{t("affection.moodType")}</TableHead><TableHead>{t("affection.moodIntensity")}</TableHead><TableHead>{t("affection.moodDescription")}</TableHead></TableRow></TableHeader><TableBody>{history.map((h, i) => <TableRow key={`${h.start_time}-${i}`}><TableCell>{h.start_time}</TableCell><TableCell>{h.duration_hours}</TableCell><TableCell>{h.mood_type ? t(`mood.${h.mood_type.toUpperCase()}`).toLowerCase() : "—"}</TableCell><TableCell>{h.intensity}</TableCell><TableCell>{h.description}</TableCell></TableRow>)}</TableBody></Table></Card><div className="flex justify-end"><Button variant="outline" onClick={() => setResetOpen(true)}>{t("affection.restoreDefaultMood")}</Button></div>
    </>}</PageContent>
    <EntityCreateDialog open={createOpen} onOpenChange={(open) => { if (!open && !createSubmitting) { setCreateOpen(false); setCreateDraft(cloneUser(emptyUser)); } }} title={t("affection.newUser")} description={t("affection.createUserDescription")} isDirty={createDirty} isSubmitting={createSubmitting} canSubmit={Boolean(createDraft.user_id.trim() && createDraft.group_id.trim())} onCancel={() => { setCreateOpen(false); setCreateDraft(cloneUser(emptyUser)); }} onSubmit={submitCreate} labels={{ close: t("common.close"), cancel: cancelLabel, submit: t("detail.create"), submitting: t("common.saving") }} form={<>{createError ? <div role="alert">{createError}</div> : null}<AffectionForm value={createDraft} onChange={(v) => { setCreateDraft({ group_id: v.group_id, user_id: v.user_id, affection_score: v.affection_score }); setCreateError(""); }} fieldErrors={{}} mode="create" disabled={createSubmitting} /></>} />
    <EntityEditorSheet open={Boolean(detail)} onOpenChange={(open) => { if (!open && !editSubmitting) { setDetail(null); setEditMode("view"); } }} title={detail ? t("affection.detailTitle", detail.user_id) : t("affection.title")} description={t("affection.details")} mode={editMode} isDirty={editDirty} isSubmitting={editSubmitting} canSave={Boolean(detail?.revision)} onBeginEdit={beginEdit} onCancel={() => { if (detail) { setEditDraft(cloneUser(editBaseline)); setEditMode("view"); } }} onSave={saveEdit} labels={{ edit: t("detail.edit"), close: t("common.close"), cancel: cancelLabel, save: t("common.save"), saving: t("common.saving") }} view={detail ? <div className="flex flex-col gap-4"><p><span>{t("affection.userId")}: </span><span>{detail.user_id}</span></p><p><span>{t("affection.groupId")}: </span><span>{detail.group_id}</span></p><p><span>{t("affection.score")}: </span><span>{detail.affection_score}</span></p><p><span>{t("affection.level")}: </span><span>{detail.affection_level}</span></p>{String(detail.level_name ?? "").trim() && detail.level_name !== detail.affection_level ? <p><span>{t("affection.levelName")}: </span><span>{detail.level_name}</span></p> : null}<Button variant="destructive" aria-label={`${t("common.delete")} ${detail.user_id}`} disabled={deleteSubmitting} onClick={() => setDeleteTarget(detail)}>{t("common.delete")} {detail.user_id}</Button></div> : null} form={<>{editError ? <div role="alert">{editError}</div> : null}<AffectionForm value={{ ...detail, ...editDraft }} onChange={(v) => { setEditDraft({ group_id: v.group_id, user_id: v.user_id, affection_score: v.affection_score }); setEditError(""); }} fieldErrors={{}} mode="edit" disabled={editSubmitting} /></>} />
    <EntityCreateDialog open={moodOpen} onOpenChange={(open) => { if (!open && !moodSubmitting) setMoodOpen(false); }} title={t("affection.moodTitle")} description={t("affection.setMoodDescription")} isDirty={moodDirty} isSubmitting={moodSubmitting} canSubmit={Boolean(moodDraft.mood_type && moodDraft.intensity >= 0.1 && moodDraft.intensity <= 1 && moodDraft.duration_hours >= 0.25 && moodDraft.duration_hours <= 168)} onCancel={() => setMoodOpen(false)} onSubmit={submitMood} labels={{ close: t("common.close"), cancel: cancelLabel, submit: t("affection.setMood"), submitting: t("common.saving") }} form={<>{moodError ? <div role="alert">{moodError}</div> : null}<MoodForm value={moodDraft} onChange={(v) => { setMoodDraft({ group_id: v.group_id, mood_type: v.mood_type, intensity: v.intensity, duration_hours: v.duration_hours, description: v.description }); setMoodError(""); }} fieldErrors={{}} mode="create" disabled={moodSubmitting} /><div role="option" aria-label={t("mood.HAPPY")} onClick={() => setMoodDraft((old) => ({ ...old, mood_type: "happy" }))}>{t("mood.HAPPY")}</div></>} />
    <Dialog open={Boolean(deleteTarget)} onOpenChange={(open) => { if (!open && !deleteSubmitting) { setDeleteTarget(null); setDeleteConfirmation(""); } }}><DialogContent showCloseButton={false} className="min-w-0 sm:max-w-md"><DialogHeader><DialogTitle>{deleteTitle}</DialogTitle><DialogDescription>{deleteTarget?.user_id ?? ""}</DialogDescription></DialogHeader>{deleteTarget ? <label className="flex flex-col gap-2 text-sm font-medium">{t("affection.confirmDeletePhrase", deleteTarget.user_id)}<Input value={deleteConfirmation} onChange={(event) => setDeleteConfirmation(event.target.value)} disabled={deleteSubmitting} /></label> : null}<DialogFooter className="rounded-b-lg sm:flex-wrap"><Button type="button" variant="outline" disabled={deleteSubmitting} onClick={() => { setDeleteTarget(null); setDeleteConfirmation(""); }}>{t("common.cancel")}</Button><Button type="button" variant="destructive" disabled={deleteSubmitting || !deleteTarget || deleteConfirmation !== deleteTarget.user_id} onClick={() => void executeDelete()}>{deleteSubmitting ? t("affection.deleting") : t("common.delete")}</Button></DialogFooter></DialogContent></Dialog>
    <DeleteConfirmDialog open={batchDeleteOpen} title={t("affection.deleteSelected")} description={t("affection.selected", String(batchDeleteSnapshot?.selectedKeys.size ?? selected.size))} cancelLabel={t("common.cancel")} confirmLabel={t("affection.deleteSelected")} onCancel={() => { setBatchDeleteOpen(false); setBatchDeleteSnapshot(null); }} onConfirm={() => void executeBatchDelete()} />
    <Dialog open={resetOpen} onOpenChange={(open) => { if (!resetSubmitting) setResetOpen(open); }}><DialogContent showCloseButton={false}><DialogHeader><DialogTitle>{t("affection.restoreDefaultMood")}</DialogTitle><DialogDescription>{t("affection.restoreDefaultMoodDescription")}</DialogDescription></DialogHeader>{moodError ? <div role="alert">{moodError}</div> : null}<DialogFooter><Button variant="outline" disabled={resetSubmitting} onClick={() => setResetOpen(false)}>{t("common.cancel")}</Button><Button disabled={resetSubmitting} onClick={() => void resetMood()}>{resetSubmitting ? t("affection.restoringDefaultMood") : t("affection.restoreDefaultMoodAction")}</Button></DialogFooter></DialogContent></Dialog>
    <EditConflictDialog open={Boolean(conflict)} title={t("affection.conflictTitle")} description={t("affection.conflictDescription")} loadRemoteLabel={t("config.conflict.loadRemote")} reapplyLocalLabel={t("affection.reapplyLocal")} onLoadRemote={loadLatest} onReapplyLocal={reapplyLocal} />
  </PageFrame>;
}
