// ================================================================
// Mock API server — simulates AstrBot Plugin Page bridge
// ================================================================
import { MEMORIES, GRAPH_NODES, GRAPH_EDGES, PROFILES, KNOWLEDGE_ENTRIES, NOTES, JARGON_CANDIDATES, JARGON_MEANINGS, AFFECTION_DATA, MOOD_TYPES, SOCIAL_RELATIONS, QUALITY_SCORES, QUALITY_ALERTS, DELEGATION_STATUS, EXPRESSION_PATTERNS, EVALUATION_DATASETS, EVALUATION_REPORTS, RECALL_TRACE_SAMPLE, DIAGNOSTIC_HEALTH, DIAGNOSTIC_EVENTS, REVIEW_ITEMS, REVIEW_ACTIONS } from "./data";
import { createMockConfigServer } from "./configServer";

type ApiResponse = { status: string; data?: unknown; message?: string; code?: string; field_errors?: Record<string, string> };

const configServer = createMockConfigServer({
  disconnectDuringReload: true,
  autoCompleteReloadMs: 750,
});

function ok(data: unknown): ApiResponse {
  return { status: "ok", data };
}

function err(message: string, code?: string, data?: unknown, field_errors?: Record<string, string>): ApiResponse {
  return { status: "error", message, ...(code ? { code } : {}), ...(data === undefined ? {} : { data }), ...(field_errors ? { field_errors } : {}) };
}

type MutableRecord = Record<string, any>;
const MOCK_BACKUPS: Array<Record<string, unknown>> = [
  { name: "v2.3.0", directory: "/backups/v2.3.0", file_count: 6, plugin_version: "2.3.0", backup_timestamp: "2026-06-01T10:00:00Z", files: ["memora.db", "conversations.db"] },
  { name: "manual_20260613_120000", directory: "/backups/manual_20260613_120000", file_count: 6, plugin_version: "2.4.2", backup_timestamp: "2026-06-13T12:00:00Z", files: ["memora.db", "conversations.db"] },
];
const MUTABLE_SEEDS = structuredClone({ MEMORIES, PROFILES, KNOWLEDGE_ENTRIES, NOTES, JARGON_MEANINGS, AFFECTION_DATA, SOCIAL_RELATIONS, EVALUATION_REPORTS, REVIEW_ITEMS, REVIEW_ACTIONS, MOCK_BACKUPS });
let nextEntityRevision = 1;
const moodHistory: MutableRecord[] = [];
const revision = () => `mock-entity-revision-${String(nextEntityRevision++).padStart(8, "0")}`;
const withoutRevision = (value: MutableRecord): MutableRecord => {
  const { revision: _revision, ...entity } = value;
  return structuredClone(entity);
};
const envelope = (value: MutableRecord): ApiResponse => ok({ entity: withoutRevision(value), revision: value.revision });
const validation = (field_errors: Record<string, string>): ApiResponse => err("Validation failed", "validation_error", undefined, field_errors);
const notFound = (message: string): ApiResponse => err(message, "not_found");
const identityKey = (identity: MutableRecord) => [identity.from_user, identity.to_user, identity.relation_type, identity.group_id].join("\u0000");
const socialIdentityOf = (record: MutableRecord) => ({ from_user: record.from_user, to_user: record.to_user, relation_type: record.relation_type, group_id: record.group_id });
const jargonIdentityOf = (record: MutableRecord) => ({ term: record.term, group_id: record.group_id });
const affectionIdentityOf = (record: MutableRecord) => ({ user_id: record.user_id, group_id: record.group_id });
const SOCIAL_CATEGORIES: Record<string, string> = {
  parent_child: "blood", siblings: "blood", relatives: "blood",
  neighbor: "geographic", fellow_town: "geographic", fellow_passenger: "geographic",
  colleague: "career", mentor_mentee: "career", classmate: "career",
  lover: "emotional", best_friend: "emotional", ambiguous: "emotional", rival: "emotional",
  board_game_friend: "interest", gaming_teammate: "interest",
  core_intimate: "intimacy", daily_normal: "intimacy", stranger: "intimacy", acquaintance: "intimacy", friend: "intimacy", close_friend: "intimacy", confidant: "intimacy",
};
const socialCategory = (relationType: string) => SOCIAL_CATEGORIES[relationType] ?? "intimacy";

export function resetMockServerState(): void {
  const seeds = structuredClone(MUTABLE_SEEDS);
  for (const [target, source] of [[MEMORIES, seeds.MEMORIES], [PROFILES, seeds.PROFILES], [KNOWLEDGE_ENTRIES, seeds.KNOWLEDGE_ENTRIES], [NOTES, seeds.NOTES], [JARGON_MEANINGS, seeds.JARGON_MEANINGS], [SOCIAL_RELATIONS, seeds.SOCIAL_RELATIONS], [EVALUATION_REPORTS, seeds.EVALUATION_REPORTS], [REVIEW_ITEMS, seeds.REVIEW_ITEMS], [MOCK_BACKUPS, seeds.MOCK_BACKUPS]] as Array<[any[], any[]]>) {
    target.splice(0, target.length, ...source);
  }
  for (const key of Object.keys(AFFECTION_DATA)) delete AFFECTION_DATA[key];
  Object.assign(AFFECTION_DATA, seeds.AFFECTION_DATA);
  for (const key of Object.keys(REVIEW_ACTIONS)) delete REVIEW_ACTIONS[key];
  Object.assign(REVIEW_ACTIONS, seeds.REVIEW_ACTIONS);
  nextEntityRevision = 1;
  moodHistory.splice(0);
  for (const record of PROFILES) (record as MutableRecord).revision = revision();
  for (const record of SOCIAL_RELATIONS) (record as MutableRecord).revision = revision();
  for (const record of JARGON_MEANINGS) (record as MutableRecord).revision = revision();
  for (const group of Object.values(AFFECTION_DATA)) {
    for (const user of group.top_users) (user as MutableRecord).revision = revision();
  }
}

resetMockServerState();

function validateText(body: MutableRecord, fields: readonly string[]): ApiResponse | null {
  for (const field of fields) {
    if (!String(body[field] ?? "").trim()) return validation({ [field]: "必填字段" });
    if (String(body[field]).length > 128) return validation({ [field]: "文本过长" });
  }
  return null;
}

function unknownFieldErrors(value: MutableRecord, allowed: readonly string[]): Record<string, string> {
  const allowedSet = new Set(allowed);
  return Object.fromEntries(Object.keys(value).filter((key) => !allowedSet.has(key)).map((key) => [key, "字段不可写"]));
}

function textFieldError(value: unknown, maximum = 128): string | null {
  if (typeof value !== "string") return "必须为字符串";
  if (!value.trim()) return "不能为空";
  if (value.length > maximum) return "文本过长";
  return null;
}

function optionalTextFieldError(value: unknown, maximum = 128): string | null {
  if (value === undefined) return null;
  if (typeof value !== "string") return "必须为字符串";
  if (value.trim().length > maximum) return "文本过长";
  return null;
}

function revisionFieldError(value: unknown): string | null {
  if (value === undefined || value === null || value === "" || (typeof value === "string" && !value.trim())) return "不能为空";
  return textFieldError(value, 256);
}

function normalizeIdentity(value: unknown, identityFields: readonly string[], optionalFields: readonly string[] = []): { identity?: MutableRecord; errors: Record<string, string> } {
  if (!value || typeof value !== "object" || Array.isArray(value)) return { errors: { identity: "必须为对象" } };
  const source = value as MutableRecord;
  const errors = unknownFieldErrors(source, identityFields);
  const identity: MutableRecord = {};
  for (const field of identityFields) {
    const error = optionalFields.includes(field) ? optionalTextFieldError(source[field]) : textFieldError(source[field]);
    if (error) errors[`identity.${field}`] = error;
    else identity[field] = typeof source[field] === "string" ? source[field].trim() : "";
  }
  return { identity, errors };
}

function revisionedRequestErrors(body: MutableRecord, allowedTop: readonly string[], identityFields: readonly string[], optionalIdentityFields: readonly string[] = []): Record<string, string> {
  const errors = unknownFieldErrors(body, allowedTop);
  const normalized = normalizeIdentity(body.identity, identityFields, optionalIdentityFields);
  Object.assign(errors, normalized.errors);
  if (normalized.identity && !Object.keys(normalized.errors).length) body.identity = normalized.identity;
  const revisionError = revisionFieldError(body.expected_revision);
  if (revisionError) errors.expected_revision = revisionError; else body.expected_revision = body.expected_revision.trim();
  return errors;
}

const PROFILE_TAG_CATEGORIES = new Set(["interest", "personality", "habit", "relation", "knowledge", "preference", "custom"]);

function normalizeProfileTag(tag: unknown, field: string): { tag?: MutableRecord; errors: Record<string, string> } {
  if (!tag || typeof tag !== "object" || Array.isArray(tag)) return { errors: { [field]: "必须为对象" } };
  const source = tag as MutableRecord;
  const errors = Object.fromEntries(Object.entries(unknownFieldErrors(source, ["category", "value", "confidence"])).map(([key, message]) => [`${field}.${key}`, message]));
  const rawCategory = source.category ?? "custom";
  const category = typeof rawCategory === "string" ? rawCategory.trim() : "";
  if (typeof rawCategory !== "string" || !PROFILE_TAG_CATEGORIES.has(category)) errors[`${field}.category`] = "不支持的标签分类";
  const valueError = textFieldError(source.value); if (valueError) errors[`${field}.value`] = valueError;
  const rawConfidence = source.confidence ?? 0.5;
  let confidence = Number.NaN;
  if (typeof rawConfidence === "boolean" || typeof rawConfidence !== "number") errors[`${field}.confidence`] = "必须为数字";
  else if (!Number.isFinite(rawConfidence)) errors[`${field}.confidence`] = "必须为有限数字";
  else if (rawConfidence < 0 || rawConfidence > 1) errors[`${field}.confidence`] = "必须在 0.0 到 1.0 之间";
  else confidence = rawConfidence;
  return { tag: { category, value: typeof source.value === "string" ? source.value.trim() : "", confidence }, errors };
}

function profileTagErrors(tag: unknown): Record<string, string> {
  const normalized = normalizeProfileTag(tag, "params.tag");
  if (normalized.tag && tag && typeof tag === "object" && !Array.isArray(tag) && !Object.keys(normalized.errors).length) Object.assign(tag, normalized.tag);
  return normalized.errors;
}

function normalizeProfilePreferences(value: unknown, field = "preferences"): { preferences?: MutableRecord; errors: Record<string, string> } {
  if (value === undefined) return { preferences: {}, errors: {} };
  if (!value || typeof value !== "object" || Array.isArray(value)) return { errors: { [field]: "必须为对象" } };
  const source = value as MutableRecord;
  const errors = Object.fromEntries(Object.entries(unknownFieldErrors(source, ["reply_style", "preferred_topics", "avoided_topics", "active_hours"])).map(([key, message]) => [`${field}.${key}`, message]));
  const preferences: MutableRecord = {};
  if ("reply_style" in source) {
    const error = textFieldError(source.reply_style); if (error) errors[`${field}.reply_style`] = error; else preferences.reply_style = source.reply_style.trim();
  }
  for (const listField of ["preferred_topics", "avoided_topics"]) if (listField in source) {
    if (!Array.isArray(source[listField])) errors[`${field}.${listField}`] = "必须为字符串数组";
    else {
      const normalized: string[] = [];
      for (const [index, item] of source[listField].entries()) {
        const itemField = `${field}.${listField}.${index}`;
        if (typeof item !== "string") errors[itemField] = "必须为字符串";
        else { const text = item.trim(); if (text.length > 64) errors[itemField] = "文本过长"; else if (text && !normalized.includes(text)) normalized.push(text); }
      }
      if (normalized.length > 32) errors[`${field}.${listField}`] = "项目过多";
      preferences[listField] = normalized;
    }
  }
  if ("active_hours" in source) {
    if (!Array.isArray(source.active_hours)) errors[`${field}.active_hours`] = "必须为整数数组";
    else {
      const normalized: number[] = [];
      for (const [index, hour] of source.active_hours.entries()) {
        const itemField = `${field}.active_hours.${index}`;
        if (typeof hour === "boolean" || !Number.isInteger(hour)) errors[itemField] = "必须为整数";
        else if (hour < 0 || hour > 23) errors[itemField] = "必须在 0 到 23 之间";
        else if (!normalized.includes(hour)) normalized.push(hour);
      }
      preferences.active_hours = normalized;
    }
  }
  return { preferences, errors };
}

function normalizeProfileTags(value: unknown, field = "tags"): { tags?: MutableRecord[]; errors: Record<string, string> } {
  if (value === undefined) return { tags: [], errors: {} };
  if (!Array.isArray(value)) return { errors: { [field]: "必须为数组" } };
  if (value.length > 100) return { errors: { [field]: "项目过多" } };
  const tags: MutableRecord[] = []; const errors: Record<string, string> = {}; const identities = new Set<string>();
  for (const [index, item] of value.entries()) {
    const normalized = normalizeProfileTag(item, `${field}.${index}`); Object.assign(errors, normalized.errors);
    if (normalized.tag) {
      const identity = profileTagKey(normalized.tag); if (identities.has(identity)) errors[`${field}.${index}.value`] = "标签重复"; else identities.add(identity);
      tags.push(normalized.tag);
    }
  }
  return { tags, errors };
}

function invalidRevisionedItem(item: unknown, index: number, identityFields: readonly string[], optionalIdentityFields: readonly string[] = [], preserveRevisionIdentity = true): MutableRecord | null {
  if (!item || typeof item !== "object" || Array.isArray(item)) return { identity: { item_index: index }, code: "validation_error", message: "Validation failed", field_errors: { item: "必须为对象" } };
  const value = item as MutableRecord;
  const errors = unknownFieldErrors(value, ["identity", "expected_revision"]);
  const normalized = normalizeIdentity(value.identity, identityFields, optionalIdentityFields);
  Object.assign(errors, normalized.errors);
  const identityMalformed = Object.keys(normalized.errors).length > 0;
  if (normalized.identity && !identityMalformed) value.identity = normalized.identity;
  const revisionError = revisionFieldError(value.expected_revision);
  if (revisionError) errors.expected_revision = revisionError; else value.expected_revision = value.expected_revision.trim();
  if (!Object.keys(errors).length) return null;
  const keepIdentity = !identityMalformed && (!revisionError || preserveRevisionIdentity);
  return { identity: keepIdentity ? structuredClone(value.identity) : { item_index: index }, code: "validation_error", message: "Validation failed", field_errors: errors };
}

function conflictFailure(identity: MutableRecord, current: MutableRecord, message: string): MutableRecord {
  return { identity, code: "edit_conflict", message, current_entity: withoutRevision(current), current_revision: current.revision };
}

function handleSocialCreate(body: MutableRecord): ApiResponse {
  const errors = unknownFieldErrors(body, ["from_user", "to_user", "group_id", "relation_type", "strength", "tags"]);
  for (const field of ["from_user", "to_user", "relation_type"]) { const error = textFieldError(body[field]); if (error) errors[field] = error; }
  const groupError = optionalTextFieldError(body.group_id); if (groupError) errors.group_id = groupError;
  if (typeof body.strength !== "number" || !Number.isFinite(body.strength) || body.strength < 0 || body.strength > 1) errors.strength = "必须在 0.0 到 1.0 之间";
  if (!Array.isArray(body.tags) || body.tags.some((tag: unknown) => typeof tag !== "string")) errors.tags = "必须为字符串数组";
  if (Object.keys(errors).length) return validation(errors);
  const normalized = { from_user: body.from_user.trim(), to_user: body.to_user.trim(), group_id: body.group_id?.trim() ?? "", relation_type: body.relation_type.trim() };
  if (SOCIAL_RELATIONS.some((item) => identityKey(item) === identityKey(normalized))) return err("Relation already exists", "already_exists");
  const record: MutableRecord = { ...normalized, strength: body.strength, tags: body.tags.map((tag: string) => tag.trim()), category: socialCategory(normalized.relation_type), frequency: 0, last_interaction: 0, revision: revision() };
  SOCIAL_RELATIONS.push(record as any); return envelope(record);
}

function findSocial(identity: MutableRecord): MutableRecord | undefined {
  return SOCIAL_RELATIONS.find((item) => identityKey(item) === identityKey(identity)) as MutableRecord | undefined;
}

function handleSocialUpdate(body: MutableRecord): ApiResponse {
  const requestErrors = revisionedRequestErrors(body, ["identity", "changes", "expected_revision"], ["from_user", "to_user", "group_id", "relation_type"], ["group_id"]);
  if (Object.keys(requestErrors).length) return validation(requestErrors);
  const changes = body.changes;
  if (!changes || typeof changes !== "object" || Array.isArray(changes)) return validation({ changes: "必须为对象" });
  const errors: Record<string, string> = unknownFieldErrors(changes, ["relation_type", "strength", "tags"]);
  if ("relation_type" in changes && (typeof changes.relation_type !== "string" || !changes.relation_type.trim())) errors["changes.relation_type"] = "不能为空";
  if ("strength" in changes && (typeof changes.strength !== "number" || !Number.isFinite(changes.strength) || changes.strength < 0 || changes.strength > 1)) errors["changes.strength"] = "必须在 0.0 到 1.0 之间";
  if ("tags" in changes && (!Array.isArray(changes.tags) || changes.tags.some((tag: unknown) => typeof tag !== "string"))) errors["changes.tags"] = "必须为字符串数组";
  if (Object.keys(errors).length) return validation(errors);
  const current = findSocial(body.identity ?? {});
  if (!current) return notFound("Relation not found");
  if (body.expected_revision !== current.revision) return err("Relation changed", "edit_conflict", { current_entity: withoutRevision(current), current_revision: current.revision });
  const normalized = structuredClone(changes);
  Object.assign(current, normalized, { ...(normalized.relation_type ? { category: socialCategory(normalized.relation_type) } : {}), revision: revision() });
  return envelope(current);
}

function handleSocialDelete(body: MutableRecord): ApiResponse {
  const requestErrors = revisionedRequestErrors(body, ["identity", "expected_revision"], ["from_user", "to_user", "group_id", "relation_type"], ["group_id"]);
  if (Object.keys(requestErrors).length) return validation(requestErrors);
  const index = SOCIAL_RELATIONS.findIndex((item) => identityKey(item) === identityKey(body.identity ?? {}));
  if (index < 0) return notFound("Relation not found");
  const current = SOCIAL_RELATIONS[index] as MutableRecord;
  if (body.expected_revision !== current.revision) return err("Relation changed", "edit_conflict", { current_entity: withoutRevision(current), current_revision: current.revision });
  SOCIAL_RELATIONS.splice(index, 1);
  return ok({ deleted: true, identity: structuredClone(body.identity) });
}

function batchResult(total: number, succeeded_ids: MutableRecord[], failures: MutableRecord[]): ApiResponse {
  return ok({ total, succeeded_count: succeeded_ids.length, failed_count: failures.length, succeeded_ids, failures });
}

function handleSocialBatch(body: MutableRecord): ApiResponse {
  const topErrors = unknownFieldErrors(body, ["action", "items", "params"]); if (Object.keys(topErrors).length) return validation(topErrors);
  if (!["delete", "add_tags", "remove_tags"].includes(body.action)) return validation({ action: "仅支持 delete、add_tags 或 remove_tags" });
  const items = Array.isArray(body.items) ? body.items : [];
  if (items.length < 1 || items.length > 100) return validation({ items: "项目数量必须在 1 到 100 之间" });
  const params = body.params && typeof body.params === "object" && !Array.isArray(body.params) ? body.params : {};
  const paramErrors = unknownFieldErrors(params, body.action === "delete" ? [] : ["tags"]);
  if (Object.keys(paramErrors).length) return validation(paramErrors);
  if (body.action !== "delete" && (!Array.isArray(params.tags) || params.tags.some((tag: unknown) => typeof tag !== "string"))) return validation({ "params.tags": "必须为字符串数组" });
  const succeeded: MutableRecord[] = [];
  const failures: MutableRecord[] = [];
  for (const [index, item] of items.entries()) {
    const malformed = invalidRevisionedItem(item, index, ["from_user", "to_user", "group_id", "relation_type"], ["group_id"]);
    if (malformed) { failures.push(malformed); continue; }
    const value = item as MutableRecord; const identity = structuredClone(value.identity); const current = findSocial(identity);
    if (!current) { failures.push({ identity, code: "not_found", message: "Relation not found" }); continue; }
    if (value.expected_revision !== current.revision) { failures.push(conflictFailure(identity, current, "Relation changed")); continue; }
    if (body.action === "delete") SOCIAL_RELATIONS.splice(SOCIAL_RELATIONS.indexOf(current as any), 1);
    else if (body.action === "add_tags") { current.tags = [...new Set([...(current.tags ?? []), ...params.tags])]; current.revision = revision(); }
    else { const removed = new Set(params.tags); current.tags = (current.tags ?? []).filter((tag: string) => !removed.has(tag)); current.revision = revision(); }
    succeeded.push(identity);
  }
  return batchResult(items.length, succeeded, failures);
}

function handleProfileCreate(body: MutableRecord): ApiResponse {
  const errors = unknownFieldErrors(body, ["user_id", "display_name", "preferences", "tags"]);
  const userError = textFieldError(body.user_id); if (userError) errors.user_id = userError;
  if (body.display_name !== undefined && typeof body.display_name !== "string") errors.display_name = "必须为字符串";
  const normalizedPreferences = normalizeProfilePreferences(body.preferences); Object.assign(errors, normalizedPreferences.errors);
  const normalizedTags = normalizeProfileTags(body.tags); Object.assign(errors, normalizedTags.errors);
  if (Object.keys(errors).length) return validation(errors);
  const userId = body.user_id.trim(); if (PROFILES.some((item) => item.user_id === userId)) return err("Profile already exists", "already_exists");
  const record: MutableRecord = { user_id: userId, display_name: body.display_name?.trim() ?? "", preferences: normalizedPreferences.preferences, tags: normalizedTags.tags, message_count: 0, last_active: "", revision: revision() };
  PROFILES.push(record as any); return envelope(record);
}

function handleProfileUpdate(body: MutableRecord): ApiResponse {
  const revisioned = "identity" in body || "changes" in body || "expected_revision" in body;
  if (revisioned) { const requestErrors = revisionedRequestErrors(body, ["identity", "changes", "expected_revision"], ["user_id"]); if (Object.keys(requestErrors).length) return validation(requestErrors); }
  const userId = body.identity?.user_id ?? body.user_id;
  const current = PROFILES.find((item) => item.user_id === userId) as MutableRecord | undefined;
  if (!current) return notFound("Profile not found");
  if (!revisioned) {
    if ("preferences" in body && (!body.preferences || typeof body.preferences !== "object" || Array.isArray(body.preferences))) return validation({ preferences: "必须为对象" });
    if ("display_name" in body) current.display_name = String(body.display_name);
    if ("preferences" in body) current.preferences = structuredClone(body.preferences);
    current.revision = revision();
    return ok(withoutRevision(current));
  }
  const changes = body.changes;
  if (!changes || typeof changes !== "object" || Array.isArray(changes)) return validation({ changes: "必须为对象" });
  const errors = unknownFieldErrors(changes, ["display_name", "preferences", "tags"]);
  const normalizedChanges = structuredClone(changes);
  if ("preferences" in changes) { const normalized = normalizeProfilePreferences(changes.preferences, "changes.preferences"); Object.assign(errors, normalized.errors); normalizedChanges.preferences = normalized.preferences; }
  if ("tags" in changes) { const normalized = normalizeProfileTags(changes.tags, "changes.tags"); Object.assign(errors, normalized.errors); normalizedChanges.tags = normalized.tags; }
  if (Object.keys(errors).length) return validation(errors);
  if (body.expected_revision !== current.revision) return err("Profile changed", "edit_conflict", { current_entity: withoutRevision(current), current_revision: current.revision });
  Object.assign(current, normalizedChanges, { revision: revision() });
  return envelope(current);
}

function handleProfileDelete(body: MutableRecord): ApiResponse {
  if ("identity" in body || "expected_revision" in body) { const requestErrors = revisionedRequestErrors(body, ["identity", "expected_revision"], ["user_id"]); if (Object.keys(requestErrors).length) return validation(requestErrors); }
  const userId = body.identity?.user_id ?? body.user_id;
  const identity = { user_id: userId };
  const index = PROFILES.findIndex((item) => item.user_id === userId);
  if (index < 0) return "identity" in body ? notFound("Profile not found") : ok({ deleted: false, user_id: userId });
  const current = PROFILES[index] as MutableRecord;
  if (body.expected_revision !== undefined && body.expected_revision !== current.revision) return err("Profile changed", "edit_conflict", { current_entity: withoutRevision(current), current_revision: current.revision });
  PROFILES.splice(index, 1);
  return ok("identity" in body ? { deleted: true, identity } : { deleted: true, user_id: userId });
}

const profileTagKey = (tag: MutableRecord) => `${String(tag.category ?? "")}\u0000${String(tag.value ?? tag.name ?? "")}`;

function handleProfileBatch(body: MutableRecord): ApiResponse {
  if ("user_ids" in body && !Array.isArray(body.user_ids)) return validation({ user_ids: "必须为数组" });
  if (Array.isArray(body.user_ids)) {
    const topErrors = unknownFieldErrors(body, ["action", "user_ids"]); if (Object.keys(topErrors).length) return validation(topErrors);
    if (body.action !== undefined && body.action !== "delete") return validation({ action: "仅支持 delete" });
    if (body.user_ids.length < 1 || body.user_ids.length > 100) return validation({ user_ids: "项目数量必须在 1 到 100 之间" });
    let deleted_count = 0; const failed_ids: unknown[] = [];
    for (const rawId of body.user_ids) { const userId = typeof rawId === "boolean" ? "" : String(rawId).trim(); const index = PROFILES.findIndex((profile) => profile.user_id === userId); if (!userId || index < 0) failed_ids.push(rawId); else { PROFILES.splice(index, 1); deleted_count += 1; } }
    return ok({ deleted_count, failed_count: failed_ids.length, total: body.user_ids.length, failed_ids });
  }
  const topErrors = unknownFieldErrors(body, ["action", "items", "params"]); if (Object.keys(topErrors).length) return validation(topErrors);
  if (!["delete", "tags_add", "tags_remove"].includes(body.action)) return validation({ action: "仅支持 delete、tags_add 或 tags_remove" });
  if (body.params !== undefined && (!body.params || typeof body.params !== "object" || Array.isArray(body.params))) return validation({ params: "必须为对象" });
  const params = (body.params ?? {}) as MutableRecord;
  const paramErrors = unknownFieldErrors(params, body.action === "delete" ? [] : ["tag"]);
  if (body.action !== "delete") Object.assign(paramErrors, profileTagErrors(params.tag));
  if (Object.keys(paramErrors).length) return validation(paramErrors);
  const items = Array.isArray(body.items) ? body.items : [];
  if (items.length < 1 || items.length > 100) return validation({ items: "项目数量必须在 1 到 100 之间" });
  const succeeded: MutableRecord[] = []; const failures: MutableRecord[] = [];
  for (const [index, item] of items.entries()) {
    const malformed = invalidRevisionedItem(item, index, ["user_id"]); if (malformed) { failures.push(malformed); continue; }
    const value = item as MutableRecord; const identity = structuredClone(value.identity); const current = PROFILES.find((profile) => profile.user_id === identity.user_id) as MutableRecord | undefined;
    if (!current) { failures.push({ identity, code: "not_found", message: "Profile not found" }); continue; }
    if (current.revision !== value.expected_revision) { failures.push(conflictFailure(identity, current, "Profile changed")); continue; }
    if (body.action === "delete") PROFILES.splice(PROFILES.indexOf(current as any), 1);
    else { const tag = structuredClone(params.tag); const key = profileTagKey(tag); if (body.action === "tags_add" && !(current.tags ?? []).some((existing: MutableRecord) => profileTagKey(existing) === key)) current.tags = [...(current.tags ?? []), tag]; if (body.action === "tags_remove") current.tags = (current.tags ?? []).filter((existing: MutableRecord) => profileTagKey(existing) !== key); current.revision = revision(); }
    succeeded.push(identity);
  }
  return batchResult(items.length, succeeded, failures);
}

function findJargon(identity: MutableRecord): MutableRecord | undefined { return JARGON_MEANINGS.find((item) => item.term === identity.term && item.group_id === identity.group_id) as MutableRecord | undefined; }
function handleJargonCreate(body: MutableRecord): ApiResponse {
  const errors = unknownFieldErrors(body, ["term", "group_id", "meaning", "confidence", "is_jargon", "is_confirmed", "is_global"]);
  for (const field of ["term", "group_id"]) { const error = textFieldError(body[field]); if (error) errors[field] = error; }
  const meaningError = textFieldError(body.meaning, 4096); if (meaningError) errors.meaning = meaningError;
  if (body.confidence === undefined || body.confidence === null || body.confidence === "") errors.confidence = "不能为空";
  else if (typeof body.confidence === "boolean" || typeof body.confidence !== "number") errors.confidence = "必须为数字";
  else if (!Number.isFinite(body.confidence)) errors.confidence = "必须为有限数字";
  else if (body.confidence < 0 || body.confidence > 1) errors.confidence = "必须在 0.0 到 1.0 之间";
  for (const field of ["is_jargon", "is_confirmed", "is_global"]) if (body[field] !== undefined && typeof body[field] !== "boolean") errors[field] = "必须为布尔值";
  if (Object.keys(errors).length) return validation(errors);
  const normalized = { term: body.term.trim(), group_id: body.group_id.trim() }; if (findJargon(normalized)) return err("Jargon meaning already exists", "already_exists");
  const now = Date.now() / 1000; const isConfirmed = body.is_confirmed ?? true;
  const record: MutableRecord = { ...normalized, meaning: body.meaning.trim(), confidence: body.confidence, is_jargon: body.is_jargon ?? true, is_confirmed: isConfirmed, is_global: body.is_global ?? false, is_complete: isConfirmed, count: 0, last_inference_count: 0, created_at: now, updated_at: now, revision: revision() };
  JARGON_MEANINGS.push(record as any); return envelope(record);
}
function handleJargonUpdate(body: MutableRecord): ApiResponse {
  const requestErrors = revisionedRequestErrors(body, ["identity", "changes", "expected_revision"], ["term", "group_id"]); if (Object.keys(requestErrors).length) return validation(requestErrors);
  const changes = body.changes;
  if (!changes || typeof changes !== "object" || Array.isArray(changes)) return validation({ changes: "必须为对象" });
  const errors = unknownFieldErrors(changes, ["meaning", "confidence", "is_jargon", "is_confirmed", "is_global"]);
  if (!Object.keys(changes).length) errors.changes = "不能为空";
  if ("meaning" in changes && (typeof changes.meaning !== "string" || !changes.meaning.trim())) errors["changes.meaning"] = "不能为空";
  if ("confidence" in changes && (typeof changes.confidence !== "number" || !Number.isFinite(changes.confidence) || changes.confidence < 0 || changes.confidence > 1)) errors["changes.confidence"] = "必须在 0.0 到 1.0 之间";
  for (const field of ["is_jargon", "is_confirmed", "is_global"]) if (field in changes && typeof changes[field] !== "boolean") errors[`changes.${field}`] = "必须为布尔值";
  if (Object.keys(errors).length) return validation(errors);
  const current = findJargon(body.identity ?? body); if (!current) return notFound("Jargon meaning not found");
  if (body.expected_revision !== current.revision) return err("Jargon meaning changed", "edit_conflict", { current_entity: withoutRevision(current), current_revision: current.revision });
  Object.assign(current, structuredClone(changes), { updated_at: Date.now() / 1000, revision: revision() }); return envelope(current);
}
function handleJargonDelete(body: MutableRecord): ApiResponse {
  const requestErrors = revisionedRequestErrors(body, ["identity", "expected_revision"], ["term", "group_id"]); if (Object.keys(requestErrors).length) return validation(requestErrors);
  const identity = body.identity ?? body; const current = findJargon(identity); if (!current) return notFound("Jargon meaning not found");
  if (body.expected_revision !== current.revision) return err("Jargon meaning changed", "edit_conflict", { current_entity: withoutRevision(current), current_revision: current.revision });
  JARGON_MEANINGS.splice(JARGON_MEANINGS.indexOf(current as any), 1); return ok({ deleted: true, identity: jargonIdentityOf(current) });
}
function handleJargonBatch(body: MutableRecord): ApiResponse {
  const topErrors = unknownFieldErrors(body, ["action", "items"]); if (Object.keys(topErrors).length) return validation(topErrors);
  if (!["delete", "confirm", "unconfirm", "set_global", "unset_global"].includes(body.action)) return validation({ action: "不支持的批量操作" });
  const items = Array.isArray(body.items) ? body.items : [];
  if (items.length < 1 || items.length > 100) return validation({ items: "项目数量必须在 1 到 100 之间" });
  const succeeded: MutableRecord[] = []; const failures: MutableRecord[] = [];
  for (const [index, item] of items.entries()) {
    const malformed = invalidRevisionedItem(item, index, ["term", "group_id"], [], false); if (malformed) { failures.push(malformed); continue; }
    const value = item as MutableRecord; const identity = structuredClone(value.identity); const current = findJargon(identity);
    if (!current) { failures.push({ identity, code: "not_found", message: "Jargon meaning not found" }); continue; }
    if (current.revision !== value.expected_revision) { failures.push(conflictFailure(identity, current, "Jargon meaning changed")); continue; }
    if (body.action === "delete") JARGON_MEANINGS.splice(JARGON_MEANINGS.indexOf(current as any), 1);
    else { if (body.action === "confirm") current.is_confirmed = true; if (body.action === "unconfirm") current.is_confirmed = false; if (body.action === "set_global") current.is_global = true; if (body.action === "unset_global") current.is_global = false; current.revision = revision(); }
    succeeded.push(identity);
  }
  return batchResult(items.length, succeeded, failures);
}

function affectionUsers(groupId: string): MutableRecord[] { return (AFFECTION_DATA[groupId]?.top_users ?? []) as MutableRecord[]; }
function findAffection(identity: MutableRecord): MutableRecord | undefined { return affectionUsers(identity.group_id).find((item) => item.user_id === identity.user_id); }
function affectionLevel(score: number): string { return score >= 100 ? "INTIMATE" : score >= 75 ? "CLOSE" : score >= 50 ? "FRIENDLY" : score >= 25 ? "WARM" : score >= 0 ? "NEUTRAL" : score >= -25 ? "COLD" : score >= -50 ? "DISLIKED" : "HOSTILE"; }
const AFFECTION_LEVEL_NAMES: Record<string, string> = { HOSTILE: "敌对", DISLIKED: "不喜", COLD: "冷淡", NEUTRAL: "中立", WARM: "温暖", FRIENDLY: "友好", CLOSE: "亲密", INTIMATE: "挚友" };
function handleAffectionCreate(body: MutableRecord): ApiResponse {
  const errors = unknownFieldErrors(body, ["group_id", "user_id", "affection_score"]);
  for (const field of ["group_id", "user_id"]) { const error = textFieldError(body[field]); if (error) errors[field] = error; }
  if (typeof body.affection_score === "boolean" || !Number.isInteger(body.affection_score)) errors.affection_score = "必须为整数";
  else if (body.affection_score < -100 || body.affection_score > 100) errors.affection_score = "必须在 -100 到 100 之间";
  if (Object.keys(errors).length) return validation(errors);
  const identity = { group_id: body.group_id.trim(), user_id: body.user_id.trim() };
  if (!AFFECTION_DATA[identity.group_id]) AFFECTION_DATA[identity.group_id] = { group_id: identity.group_id, total_affection: 0, max_total_affection: 0, user_count: 0, top_users: [], current_mood: { mood_type: "NEUTRAL", intensity: 0, description: "", is_active: false } };
  const group = AFFECTION_DATA[identity.group_id]; if (findAffection(identity)) return err("Affection user already exists", "already_exists");
  const score = body.affection_score; const level = affectionLevel(score);
  const record: MutableRecord = { ...identity, affection_score: score, affection_level: level, level_name: AFFECTION_LEVEL_NAMES[level], interaction_count: 0, last_interaction: 0, revision: revision() };
  group.top_users.push(record as any); group.user_count += 1; group.total_affection += score; group.max_total_affection = Math.max(group.max_total_affection, group.total_affection); return envelope(record);
}
function handleAffectionUpdate(body: MutableRecord): ApiResponse {
  const requestErrors = revisionedRequestErrors(body, ["identity", "changes", "expected_revision"], ["group_id", "user_id"]); if (Object.keys(requestErrors).length) return validation(requestErrors);
  const changes = body.changes;
  if (!changes || typeof changes !== "object" || Array.isArray(changes)) return validation({ changes: "必须为对象" });
  const errors = unknownFieldErrors(changes, ["affection_score"]);
  if (typeof changes.affection_score === "boolean" || !Number.isInteger(changes.affection_score)) errors["changes.affection_score"] = "必须为整数";
  else if (changes.affection_score < -100 || changes.affection_score > 100) errors["changes.affection_score"] = "必须在 -100 到 100 之间";
  if (Object.keys(errors).length) return validation(errors);
  const identity = body.identity ?? body; const current = findAffection(identity); if (!current) return notFound("Affection user not found");
  if (body.expected_revision !== current.revision) return err("Affection user changed", "edit_conflict", { current_entity: withoutRevision(current), current_revision: current.revision });
  const previousScore = Number(current.affection_score); Object.assign(current, structuredClone(changes)); const group = AFFECTION_DATA[identity.group_id]; group.total_affection += Number(current.affection_score) - previousScore; group.max_total_affection = Math.max(group.max_total_affection, group.total_affection); current.affection_level = affectionLevel(Number(current.affection_score)); current.level_name = AFFECTION_LEVEL_NAMES[current.affection_level]; current.revision = revision(); return envelope(current);
}
function handleAffectionDelete(body: MutableRecord): ApiResponse {
  const requestErrors = revisionedRequestErrors(body, ["identity", "expected_revision"], ["group_id", "user_id"]); if (Object.keys(requestErrors).length) return validation(requestErrors);
  const identity = body.identity ?? body; const group = AFFECTION_DATA[identity.group_id]; const current = findAffection(identity); if (!group || !current) return notFound("Affection user not found");
  if (body.expected_revision !== current.revision) return err("Affection user changed", "edit_conflict", { current_entity: withoutRevision(current), current_revision: current.revision });
  group.top_users.splice(group.top_users.indexOf(current as any), 1); group.user_count = Math.max(0, group.user_count - 1); group.total_affection -= Number(current.affection_score); return ok({ deleted: true, identity: affectionIdentityOf(current) });
}
function handleAffectionBatch(body: MutableRecord): ApiResponse {
  const topErrors = unknownFieldErrors(body, ["action", "items", "params"]); if (Object.keys(topErrors).length) return validation(topErrors);
  if (body.action !== "delete") return validation({ action: "仅支持 delete" });
  if (body.params !== undefined && (!body.params || typeof body.params !== "object" || Array.isArray(body.params))) return validation({ params: "必须为对象" });
  const params = (body.params ?? {}) as MutableRecord; const paramErrors = unknownFieldErrors(params, []); if (Object.keys(paramErrors).length) return validation(paramErrors);
  const items = Array.isArray(body.items) ? body.items : []; if (items.length < 1 || items.length > 100) return validation({ items: "项目数量必须在 1 到 100 之间" });
  const succeeded: MutableRecord[] = []; const failures: MutableRecord[] = [];
  for (const [index, item] of items.entries()) {
    const malformed = invalidRevisionedItem(item, index, ["group_id", "user_id"]); if (malformed) { failures.push(malformed); continue; }
    const value = item as MutableRecord; const identity = structuredClone(value.identity); const current = findAffection(identity);
    if (!current) { failures.push({ identity, code: "not_found", message: "Affection user not found" }); continue; }
    if (current.revision !== value.expected_revision) { failures.push(conflictFailure(identity, current, "Affection user changed")); continue; }
    const group = AFFECTION_DATA[identity.group_id]; group.top_users.splice(group.top_users.indexOf(current as any), 1); group.user_count = Math.max(0, group.user_count - 1); group.total_affection -= Number(current.affection_score); succeeded.push(identity);
  }
  return batchResult(items.length, succeeded, failures);
}
function handleMoodSet(body: MutableRecord): ApiResponse {
  const invalid = validateText(body, ["group_id", "mood_type"]);
  if (invalid) return invalid;
  const errors: Record<string, string> = {};
  const supported = new Set(MOOD_TYPES.map((item) => item.type.toLowerCase()));
  if (!supported.has(String(body.mood_type).toLowerCase())) errors.mood_type = "不支持的情绪类型";
  if (typeof body.intensity !== "number" || !Number.isFinite(body.intensity) || body.intensity < 0.1 || body.intensity > 1) errors.intensity = "必须在 0.1 到 1.0 之间";
  if (typeof body.duration_hours !== "number" || !Number.isFinite(body.duration_hours) || body.duration_hours < 0.25 || body.duration_hours > 168) errors.duration_hours = "必须在 0.25 到 168.0 之间";
  if (body.description !== undefined && typeof body.description !== "string") errors.description = "必须为字符串";
  if (Object.keys(errors).length) return validation(errors);
  const group = AFFECTION_DATA[body.group_id];
  if (!group) return notFound("Affection group not found");
  const mood = { mood_type: String(body.mood_type), intensity: body.intensity, duration_hours: body.duration_hours, description: String(body.description ?? ""), start_time: Date.now() / 1000, is_active: true };
  group.current_mood = structuredClone(mood) as any;
  moodHistory.push({ group_id: body.group_id, ...structuredClone(mood) });
  return ok(mood);
}
function handleMoodReset(body: MutableRecord): ApiResponse { const group = AFFECTION_DATA[body.group_id]; if (!group) return notFound("Affection group not found"); const mood = { mood_type: "calm", intensity: 0.5, duration_hours: 4, description: "Default calm mood", start_time: Date.now() / 1000, is_active: true }; group.current_mood = structuredClone(mood) as any; moodHistory.push({ group_id: body.group_id, ...structuredClone(mood) }); return ok(mood); }

// Simulate network latency (80-250ms)
function delay(): Promise<void> {
  const ms = 80 + Math.random() * 170;
  return new Promise((r) => setTimeout(r, ms));
}

// ---- Route handlers ----

function handleStats(): ApiResponse {
  const active = MEMORIES.filter((m) => m.status === "active").length;
  const archived = MEMORIES.filter((m) => m.status === "archived").length;
  const deleted = MEMORIES.filter((m) => m.status === "deleted").length;

  const importanceDist: Record<string, number> = {};
  for (let i = 0; i < 10; i++) importanceDist[`${i}-${i + 1}`] = 0;
  MEMORIES.forEach((m) => {
    const normalized = m.importance <= 1 ? m.importance * 10 : m.importance;
    const index = Math.min(9, Math.max(0, Math.floor(normalized)));
    const bucket = `${index}-${index + 1}`;
    importanceDist[bucket] = (importanceDist[bucket] ?? 0) + 1;
  });

  const atomTypes: Record<string, number> = {};
  MEMORIES.forEach((m) => {
    atomTypes[m.type] = (atomTypes[m.type] ?? 0) + 1;
  });

  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);
  const dailyMemoryCounts = Array.from({ length: 90 }, (_, index) => {
    const day = new Date(today);
    day.setUTCDate(today.getUTCDate() - (89 - index));
    return { date: day.toISOString().slice(0, 10), count: 0 };
  });
  const dailyByDate = new Map(dailyMemoryCounts.map((item) => [item.date, item]));
  MEMORIES.forEach((memory) => {
    const bucket = dailyByDate.get(String(memory.created_at).slice(0, 10));
    if (bucket) bucket.count += 1;
  });

  return ok({
    total_memories: MEMORIES.length,
    active_count: active,
    archived_count: archived,
    deleted_count: deleted,
    graph_nodes: GRAPH_NODES.length,
    graph_edges: GRAPH_EDGES.length,
    graph_entries: GRAPH_NODES.length,
    atom_count: MEMORIES.length,
    avg_importance: MEMORIES.length > 0
      ? MEMORIES.reduce((sum, memory) => sum + Math.min(1, memory.importance > 1 ? memory.importance / 10 : memory.importance), 0) / MEMORIES.length
      : 0,
    status_breakdown: { active, archived, deleted },
    importance_distribution: importanceDist,
    atom_breakdown: atomTypes,
    recent_sessions: [
      { session_id: "sess_5", message_count: 55 },
      { session_id: "sess_1", message_count: 45 },
      { session_id: "sess_2", message_count: 32 },
      { session_id: "sess_3", message_count: 28 },
      { session_id: "sess_4", message_count: 19 },
    ],
    daily_memory_counts: dailyMemoryCounts,
    backups: [
      { name: "pre_v2.4.0_backup", size: 2_560_000, created: "2026-06-01T00:00:00Z" },
      { name: "pre_v2.3.0_backup", size: 2_100_000, created: "2026-05-15T00:00:00Z" },
    ],
  });
}

function handleMetricsSummary(): ApiResponse {
  return ok({
    recall: {
      sample_count: 42,
      avg_total_ms: 83.6,
      avg_bm25_ms: 12.4,
      avg_vector_ms: 24.8,
      avg_graph_ms: 18.3,
      avg_rerank_ms: 28.1,
      p50_total_ms: 76.5,
      p95_total_ms: 148.2,
      recent: [
        { total_ms: 72.1, bm25_ms: 10.2, vector_ms: 22.4, graph_ms: 16.8, rerank_ms: 22.7 },
        { total_ms: 148.2, bm25_ms: 19.1, vector_ms: 41.3, graph_ms: 27.6, rerank_ms: 60.2 },
      ],
    },
    quality: {
      status: "ok",
      total_scored: 1042,
      avg_overall: 0.78,
      paused: false,
      alert_counts: { critical: 1, high: 1, medium: 1, info: 1 },
    },
    background_tasks: {
      tracked: 5,
      active: 2,
      completed: 3,
      failed: 1,
      cancelled: 0,
      failed_tasks: [
        {
          name: "provider-retry",
          error: "TimeoutError",
          message: "provider retry timed out",
          suggestion: "检查 LLM/Embedding provider 配置与网络状态，然后等待重试或重启插件初始化。",
        },
      ],
      schedulers: {
        backfill: {
          status: "completed_with_errors",
          running: false,
          errors: 1,
          processed: 120,
          total: 128,
          last_error: "topic split failed",
          suggestion: "检查话题分割配置和最近的错误详情；修复后可重新启动存量回填。",
        },
      },
    },
    provider: {
      status: "ready",
      providers_ready: true,
      attempts: 3,
      max_attempts: 60,
      retry_active: false,
      missing_provider: [],
    },
    index: {
      validator_available: true,
      last_rebuild_success: true,
      last_rebuild_duration_seconds: 2.4,
      last_rebuild_errors: 0,
      last_rebuild_total: 128,
    },
    write_coordinator: {
      operations_total: 318,
      lock_retries_total: 7,
      failures_total: 1,
      retry_exhausted_total: 0,
      fatal_failures_total: 0,
      non_retryable_failures_total: 1,
      last_error: null,
    },
    prometheus: {
      available: true,
      collector_count: 9,
      metric_names: ["memora_write_operations", "memora_write_lock_retries", "memora_write_failures"],
    },
  });
}

function handleMemories(params: Record<string, string>): ApiResponse {
  let filtered = [...MEMORIES];

  const keyword = params.keyword?.toLowerCase();
  if (keyword) {
    filtered = filtered.filter(
      (m) =>
        m.id.toLowerCase().includes(keyword) ||
        (m.content ?? "").toLowerCase().includes(keyword) ||
        (m.summary ?? "").toLowerCase().includes(keyword)
    );
  }

  if (params.session_id) {
    filtered = filtered.filter((m) => m.session_id === params.session_id);
  }

  if (params.status && params.status !== "all") {
    filtered = filtered.filter((m) => m.status === params.status);
  }

  const page = parseInt(params.page ?? "1", 10);
  const pageSize = parseInt(params.page_size ?? "20", 10);
  const total = filtered.length;
  const start = (page - 1) * pageSize;
  const items = filtered.slice(start, start + pageSize);

  return ok({ items, total, page, page_size: pageSize });
}

function handleMemoryDetail(id: string): ApiResponse {
  const m = MEMORIES.find((m) => m.id === id);
  return m ? ok({ memory: m }) : err("Memory not found");
}

function handleMemoryUpdate(body: Record<string, unknown>): ApiResponse {
  const idx = MEMORIES.findIndex((m) => m.id === (body.memory_id as string));
  if (idx === -1) return err("Memory not found");
  if (body.changes && typeof body.changes === "object") Object.assign(MEMORIES[idx], structuredClone(body.changes));
  const field = body.field as string;
  if (field && body.value !== undefined) (MEMORIES[idx] as Record<string, unknown>)[field] = body.value;
  return ok({ updated: true });
}

function handleMemoryBatch(body: Record<string, unknown>): ApiResponse {
  const ids = body.memory_ids as string[];
  const action = body.action as string;
  if (!ids || !action) return err("Missing memory_ids or action");
  return ok({ action, affected: ids.length });
}

function handleGraphSearch(params: Record<string, string>): ApiResponse {
  const q = params.query?.toLowerCase();
  let nodes = GRAPH_NODES;
  let edges = GRAPH_EDGES;

  if (q) {
    nodes = nodes.filter(
      (n) =>
        (n.label ?? "").toLowerCase().includes(q) ||
        (n.type ?? "").toLowerCase().includes(q)
    );
    const nodeIds = new Set(nodes.map((n) => n.id));
        edges = edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));
  }

  return ok({ nodes, edges });
}

function handleProfiles(params: Record<string, string>): ApiResponse {
  const limit = parseInt(params.limit ?? "100", 10);
  const offset = parseInt(params.offset ?? "0", 10);
  const items = PROFILES.slice(offset, offset + limit);
  return ok({ profiles: items, total: PROFILES.length, count: PROFILES.length, limit, offset });
}

function handleProfileDetail(userId: string): ApiResponse {
  const profile = PROFILES.find((item) => item.user_id === userId) as MutableRecord | undefined;
  return profile ? ok(structuredClone(profile)) : notFound("Profile not found");
}

function handleKnowledgeList(params: Record<string, string>): ApiResponse {
  let items = [...KNOWLEDGE_ENTRIES];
  if (params.category) {
    items = items.filter((e) => e.category === params.category);
  }
  return ok({ entries: items, items, total: KNOWLEDGE_ENTRIES.length, count: KNOWLEDGE_ENTRIES.length });
}

function handleKnowledgeSearch(query: string): ApiResponse {
  const q = query.toLowerCase();
  const items = KNOWLEDGE_ENTRIES.filter(
    (e) =>
      e.title.toLowerCase().includes(q) ||
      (e.content ?? "").toLowerCase().includes(q)
  );
  return ok({ entries: items, items });
}

function handleKnowledgeDetail(entryId: string): ApiResponse {
  const e = KNOWLEDGE_ENTRIES.find((e) => e.entry_id === entryId);
  return e ? ok({ entry: e }) : err("Entry not found");
}

function handleKnowledgeCreate(body: Record<string, unknown>): ApiResponse {
  const newEntry = {
    entry_id: `k${KNOWLEDGE_ENTRIES.length + 1}`,
    title: (body.title as string) ?? "Untitled",
    content: (body.content as string) ?? "",
    category: (body.category as string) ?? "fact",
    confidence: 0.5,
    access_count: 0,
    updated_at: new Date().toISOString(),
  };
  KNOWLEDGE_ENTRIES.push(newEntry);
  return ok({ entry: newEntry });
}

function handleKnowledgeDelete(body: Record<string, unknown>): ApiResponse {
  const id = body.entry_id as string;
  const idx = KNOWLEDGE_ENTRIES.findIndex((e) => e.entry_id === id);
  if (idx === -1) return err("Entry not found");
  KNOWLEDGE_ENTRIES.splice(idx, 1);
  return ok({ deleted: true });
}

function handleKnowledgeUpdate(body: Record<string, unknown>): ApiResponse {
  const entry = KNOWLEDGE_ENTRIES.find((item) => item.entry_id === body.entry_id);
  if (!entry) return err("Entry not found");
  if (body.changes && typeof body.changes === "object") Object.assign(entry, structuredClone(body.changes));
  const field = body.field as string;
  if (field && body.value !== undefined) (entry as Record<string, unknown>)[field] = field === "confidence" ? Number(body.value) : body.value;
  entry.updated_at = new Date().toISOString();
  return ok({ entry });
}

function handleKnowledgeBatch(body: Record<string, unknown>): ApiResponse {
  const ids = body.entry_ids as string[];
  if (!ids) return err("Missing entry_ids");
  for (const id of ids) {
    const idx = KNOWLEDGE_ENTRIES.findIndex((e) => e.entry_id === id);
    if (idx !== -1) KNOWLEDGE_ENTRIES.splice(idx, 1);
  }
  return ok({ action: body.action ?? "delete", affected: ids.length });
}

function handleNotesList(params: Record<string, string>): ApiResponse {
  let items = [...NOTES];
  if (params.status) {
    items = items.filter((n) => n.status === params.status);
  }
  return ok({ notes: items, items, total: NOTES.length, count: NOTES.length });
}

function handleNoteSearch(query: string): ApiResponse {
  const q = query.toLowerCase();
  const items = NOTES.filter(
    (n) =>
      n.title.toLowerCase().includes(q) ||
      (n.content ?? "").toLowerCase().includes(q) ||
      (n.tags ?? []).some((t) => t.toLowerCase().includes(q))
  );
  return ok({ notes: items, items });
}

function handleNoteDetail(noteId: string): ApiResponse {
  const n = NOTES.find((n) => n.note_id === noteId);
  return n ? ok({ note: n }) : err("Note not found");
}

function handleNoteCreate(body: Record<string, unknown>): ApiResponse {
  const newNote = {
    note_id: `note_${String(NOTES.length + 1).padStart(3, "0")}`,
    title: (body.title as string) ?? "Untitled",
    content: (body.content as string) ?? "",
    tags: (body.tags as string[]) ?? [],
    status: "active",
    version: 1,
    updated_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
  };
  NOTES.push(newNote);
  return ok({ note: newNote });
}

function handleNoteDelete(body: Record<string, unknown>): ApiResponse {
  const id = body.note_id as string;
  const idx = NOTES.findIndex((n) => n.note_id === id);
  if (idx === -1) return err("Note not found");
  NOTES.splice(idx, 1);
  return ok({ deleted: true });
}

function handleNoteArchive(body: Record<string, unknown>): ApiResponse {
  const id = body.note_id as string;
  const note = NOTES.find((n) => n.note_id === id);
  if (!note) return err("Note not found");
  note.status = "archived";
  return ok({ note });
}

function handleNoteUpdate(body: Record<string, unknown>): ApiResponse {
  const note = NOTES.find((item) => item.note_id === body.note_id);
  if (!note) return err("Note not found");
  if (body.changes && typeof body.changes === "object") Object.assign(note, structuredClone(body.changes));
  const field = body.field as string;
  if (field && body.value !== undefined) {
    (note as Record<string, unknown>)[field] = field === "tags" && typeof body.value === "string"
      ? body.value.split(",").map((tag) => tag.trim()).filter(Boolean)
      : body.value;
  }
  note.updated_at = new Date().toISOString();
  note.version = (note.version ?? 1) + 1;
  return ok({ note });
}

function handleNoteBatch(body: Record<string, unknown>): ApiResponse {
  const ids = body.note_ids as string[];
  const action = body.action as string;
  if (!ids) return err("Missing note_ids");
  for (const id of ids) {
    const note = NOTES.find((n) => n.note_id === id);
    if (!note) continue;
    if (action === "delete") {
      const idx = NOTES.findIndex((n) => n.note_id === id);
      if (idx !== -1) NOTES.splice(idx, 1);
    } else if (action === "archive") {
      note.status = "archived";
    }
  }
  return ok({ action, affected: ids.length });
}

function handleRecallTest(body: Record<string, unknown>): ApiResponse {
  const k = (body.k as number) ?? 5;
  const results = MEMORIES.slice(0, Math.min(k, MEMORIES.length)).map((m, i) => ({
    ...m,
    score: parseFloat((0.95 - i * 0.07).toFixed(3)),
    doc_kw_score: parseFloat((0.85 - i * 0.1).toFixed(3)),
    doc_vec_score: parseFloat((0.88 - i * 0.08).toFixed(3)),
    graph_kw_score: parseFloat((0.72 - i * 0.1).toFixed(3)),
    graph_vec_score: parseFloat((0.68 - i * 0.12).toFixed(3)),
  }));
  return ok({ results, memories: results });
}

function clampInt(value: unknown, min: number, max: number, fallback: number): number {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, Math.round(n)));
}

function handleRecallTrace(body: Record<string, unknown>): ApiResponse {
  const k = clampInt(body.k, 1, 20, 5);
  const chainDepth = clampInt(body.chain_depth, 0, 5, 2);
  return ok({
    ...RECALL_TRACE_SAMPLE,
    trace_id: `trace-mock-${Date.now()}`,
    query: String(body.query ?? RECALL_TRACE_SAMPLE.query),
    results: RECALL_TRACE_SAMPLE.results.slice(0, k),
    created_at: Date.now() / 1000,
    metadata: {
      ...RECALL_TRACE_SAMPLE.metadata,
      session_id: String(body.session_id ?? ""),
      user_id: String(body.user_id ?? ""),
      chat_type: String(body.chat_type ?? "private"),
      chain_depth: chainDepth,
      requested_k: k,
    },
  });
}

function handleRecallTraceDetail(): ApiResponse {
  return ok(RECALL_TRACE_SAMPLE);
}

function handleEvaluationDatasets(): ApiResponse {
  return ok({ datasets: EVALUATION_DATASETS });
}

function handleEvaluationReports(params: Record<string, string>): ApiResponse {
  const limit = Math.min(50, Math.max(1, parseInt(params.limit ?? "10", 10)));
  return ok({ reports: EVALUATION_REPORTS.slice(0, limit) });
}

function handleEvaluationReportDetail(params: Record<string, string>): ApiResponse {
  const reportId = params.report_id ?? params.id;
  const report = EVALUATION_REPORTS.find((item) => item.report_id === reportId);
  return report ? ok({ report }) : err("Evaluation report not found");
}

function handleEvaluationRun(body: Record<string, unknown>): ApiResponse {
  const template = EVALUATION_REPORTS[0];
  const datasets = Array.isArray(body.datasets) ? body.datasets.map(String) : ["private_basic"];
  const variants = Array.isArray(body.variants) ? body.variants.map(String) : ["baseline"];
  const k = Math.min(20, Math.max(1, Number(body.k ?? template.summary.k)));
  const report = {
    ...template,
    report_id: `eval-${Date.now()}`,
    created_at: Date.now() / 1000,
    baseline: String(body.baseline ?? "baseline"),
    datasets,
    summary: { ...template.summary, k },
    variants: Object.fromEntries(
      Object.entries(template.variants).filter(([name]) => variants.includes(name))
    ),
    deltas: Object.fromEntries(
      Object.entries(template.deltas ?? {}).filter(([name]) => variants.includes(name))
    ),
    cases: (template.cases ?? []).map((item) => ({ ...item })),
  };
  EVALUATION_REPORTS.unshift(report);
  return ok(report);
}

function handleDiagnosticHealth(): ApiResponse {
  return ok(DIAGNOSTIC_HEALTH);
}

function handleDiagnosticEvents(params: Record<string, string>): ApiResponse {
  const limit = Math.min(100, Math.max(1, parseInt(params.limit ?? "50", 10)));
  return ok({
    events: DIAGNOSTIC_EVENTS.slice(0, limit),
    total: DIAGNOSTIC_EVENTS.length,
  });
}

function handleDiagnosticAction(body: Record<string, unknown>): ApiResponse {
  const action = String(body.action ?? "");
  if (action === "refresh_metrics") {
    return ok({ action, refreshed: true, message: "diagnostic metrics refreshed" });
  }
  if (action === "rebuild_index") {
    if (body.confirmed !== true) return err("confirmation_required");
    return ok({ action, accepted: true, message: "index rebuild requested" });
  }
  return err(`unknown diagnostic action: ${action}`);
}

function handleReviewItems(params: Record<string, string>): ApiResponse {
  let items = [...REVIEW_ITEMS];
  if (params.status) items = items.filter((item) => item.status === params.status);
  if (params.reason) items = items.filter((item) => item.reasons.includes(params.reason));
  if (params.severity) items = items.filter((item) => item.severity === params.severity);
  const limit = Math.min(100, Math.max(1, parseInt(params.limit ?? "50", 10)));
  return ok({ items: items.slice(0, limit), total: items.length });
}

function handleReviewDetail(params: Record<string, string>): ApiResponse {
  const reviewId = params.review_id ?? params.item_id ?? "";
  const item = REVIEW_ITEMS.find((entry) => entry.item_id === reviewId);
  if (!item) return err("Review item not found");
  return ok({ item, actions: REVIEW_ACTIONS[reviewId] ?? [] });
}

function handleReviewAction(body: Record<string, unknown>): ApiResponse {
  const reviewId = String(body.review_id ?? "");
  const action = String(body.action ?? "");
  const item = REVIEW_ITEMS.find((entry) => entry.item_id === reviewId);
  if (!item) return err("Review item not found");
  const statusByAction: Record<string, string> = {
    approve: "approved",
    edit: "edited",
    merge: "merged",
    archive: "archived",
    delete: "deleted",
    mark_safe: "safe",
  };
  const nextStatus = statusByAction[action];
  if (!nextStatus) return err(`unsupported review action: ${action}`);
  if (action === "delete" && body.confirmed !== true) return err("confirmation_required");

  const payload = body.payload && typeof body.payload === "object" && !Array.isArray(body.payload)
    ? body.payload as Record<string, unknown>
    : {};
  if (action === "edit" && (typeof payload.content !== "string" || payload.content.trim() === "")) {
    return err("edit content required");
  }
  if (action === "merge" && (typeof payload.target_memory_id !== "string" || payload.target_memory_id.trim() === "")) {
    return err("target_memory_id required");
  }

  if (action === "edit") {
    item.content_preview = String(payload.content);
  }
  item.status = nextStatus;
  item.updated_at = Date.now() / 1000;

  const record = {
    action_id: `review-action-${Date.now()}`,
    item_id: reviewId,
    action: nextStatus,
    actor_id: "operator",
    payload,
    created_at: Date.now() / 1000,
  };
  REVIEW_ACTIONS[reviewId] = [record, ...(REVIEW_ACTIONS[reviewId] ?? [])];
  return ok({ item, action: record, accepted: true });
}


function handleLearningStatus(): ApiResponse {
  return ok({
    hit_rate: 0.78,
    avg_quality: 0.842,
    total_trials: 156,
    total_corrections: 23,
    parameters: {
      recall_weight: 0.65,
      graph_weight: 0.35,
      emotion_bonus: 0.12,
      recency_decay: 0.03,
      importance_threshold: 4.2,
      fusion_k: 60,
      mmr_lambda: 0.7,
      learning_rate: 0.01,
    },
    history: [
      { timestamp: "2026-06-15T14:30:00Z", action: "weight_adjust", detail: "recall_weight +0.03 (hit_rate improved)" },
      { timestamp: "2026-06-14T09:15:00Z", action: "threshold_tune", detail: "importance_threshold 5.0→4.2 (wider recall)" },
      { timestamp: "2026-06-13T16:45:00Z", action: "correction", detail: "emotion_bonus reverted: -0.05 (negative feedback)" },
      { timestamp: "2026-06-12T11:00:00Z", action: "param_init", detail: "Initial parameter set from defaults" },
      { timestamp: "2026-06-11T08:20:00Z", action: "weight_adjust", detail: "graph_weight +0.05 (graph route underused)" },
    ],
  });
}

function handleBackupList(): ApiResponse {
  return ok({ backups: MOCK_BACKUPS, total: MOCK_BACKUPS.length });
}

function handleBackupDelete(body: Record<string, unknown>): ApiResponse {
  const name = body.name as string;
  if (!name) return err("backup name required");
  const idx = MOCK_BACKUPS.findIndex((b) => b.name === name);
  if (idx === -1) return err(`backup not found: ${name}`);
  MOCK_BACKUPS.splice(idx, 1);
  return ok({ message: `deleted ${name}`, name });
}

function handleBackupBatchDelete(body: Record<string, unknown>): ApiResponse {
  const names = body.names as string[];
  if (!names?.length) return err("names required");
  let deleted = 0;
  for (const name of names) {
    const idx = MOCK_BACKUPS.findIndex((b) => b.name === name);
    if (idx !== -1) { MOCK_BACKUPS.splice(idx, 1); deleted++; }
  }
  return ok({ message: `deleted ${deleted}/${names.length}`, deleted, failed: names.length - deleted });
}

function handleBackupRestore(body: Record<string, unknown>): ApiResponse {
  const name = body.name as string;
  if (!name) return err("backup name required");
  const found = MOCK_BACKUPS.find((b) => b.name === name);
  if (!found) return err(`backup not found: ${name}`);
  return ok({ message: `restored ${found.file_count} files from ${name}`, restored: found.file_count });
}

function handleExportMemories(body: Record<string, unknown>): ApiResponse {
  const format = (body.format as string) ?? "jsonl";
  if (format === "markdown") {
    const content = MEMORIES.map((m, i) =>
      `## Memory #${i + 1}\n\n- **Importance**: ${m.importance?.toFixed(2) ?? "0.50"}\n- **Type**: ${m.type ?? "GENERAL"}\n\n${m.content ?? ""}\n\n---\n`
    ).join("\n");
    return ok({ content, count: MEMORIES.length, format });
  }
  const content = MEMORIES.map((m) =>
    JSON.stringify({ id: m.id, content: m.content, metadata: { type: m.type, importance: m.importance }, exported_at: Date.now() / 1000 })
  ).join("\n");
  return ok({ content, count: MEMORIES.length, format });
}

// ---- Main router ----

export async function handleApiGet(path: string, params: Record<string, string> = {}): Promise<ApiResponse> {
  await delay();
  const p = path.replace(/^page\/?/, "");
  const configResponse = configServer.handleGet(p, params);
  if (configResponse) return configResponse;

  if (p === "stats") return handleStats();
  if (p === "metrics/summary") return handleMetricsSummary();
  if (p === "memories" || p.startsWith("memories?")) return handleMemories(params);
  // 兼容 memory/detail 和 memories/detail 两种路径
  if (p.startsWith("memory/detail") || p.startsWith("memories/detail")) return handleMemoryDetail(params.id ?? "");
  if (p === "graph/search" || p.startsWith("graph/search?")) return handleGraphSearch(params);
  if (p === "profiles" || p.startsWith("profiles?")) return handleProfiles(params);
  if (p.startsWith("profiles/detail")) return handleProfileDetail(params.user_id ?? "");
  if (p === "knowledge" || p.startsWith("knowledge?")) return handleKnowledgeList(params);
  if (p.startsWith("knowledge/search")) return handleKnowledgeSearch(params.query ?? "");
  if (p.startsWith("knowledge/detail")) return handleKnowledgeDetail(params.entry_id ?? "");
  if (p === "notes" || p.startsWith("notes?")) return handleNotesList(params);
  if (p.startsWith("notes/search")) return handleNoteSearch(params.query ?? "");
  if (p.startsWith("notes/detail")) return handleNoteDetail(params.note_id ?? "");
  if (p === "backup/list" || p.startsWith("backup/list")) return handleBackupList();
  if (p === "learning/status" || p.startsWith("learning/status")) return handleLearningStatus();
  if (p === "recall/trace/detail" || p.startsWith("recall/trace/detail")) return handleRecallTraceDetail();
  if (p === "evaluation/datasets" || p.startsWith("evaluation/datasets")) return handleEvaluationDatasets();
  if (p === "evaluation/reports/detail" || p.startsWith("evaluation/reports/detail")) return handleEvaluationReportDetail(params);
  if (p === "evaluation/reports" || p.startsWith("evaluation/reports?")) return handleEvaluationReports(params);
  if (p === "diagnostics/health") return handleDiagnosticHealth();
  if (p === "diagnostics/events" || p.startsWith("diagnostics/events?")) return handleDiagnosticEvents(params);
  if (p === "review/items" || p.startsWith("review/items?")) return handleReviewItems(params);
  if (p === "review/items/detail" || p.startsWith("review/items/detail")) return handleReviewDetail(params);
  if (p === "config/topic-segmentation") return handleTopicSegConfigGet();
  if (p === "backfill/status") return handleBackfillStatus();
  // v1.0.0+ new subsystems
  if (p === "jargon/candidates" || p.startsWith("jargon/candidates?")) return handleJargonCandidates(params);
  if (p === "jargon/meanings" || p.startsWith("jargon/meanings?")) return handleJargonMeanings(params);
  if (p === "jargon/stats" || p.startsWith("jargon/stats?")) return handleJargonStats(params);
  if (p === "affection/status" || p.startsWith("affection/status?")) return handleAffectionStatus(params);
  if (p === "affection/users" || p.startsWith("affection/users?")) {
    const errors: Record<string, string> = {};
    if (!params.group_id?.trim()) errors.group_id = "不能为空";
    const limit = Number(params.limit ?? 50); const offset = Number(params.offset ?? 0);
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) errors.limit = "必须在 1 到 100 之间";
    if (!Number.isInteger(offset) || offset < 0 || offset > 1_000_000) errors.offset = "必须在 0 到 1000000 之间";
    if (Object.keys(errors).length) return validation(errors);
    const users = affectionUsers(params.group_id);
    return ok({ group_id: params.group_id, users: users.slice(offset, offset + limit), total: users.length, limit, offset });
  }
  if (p === "affection/moods/history" || p.startsWith("affection/moods/history?")) {
    if (!params.group_id?.trim()) return validation({ group_id: "不能为空" });
    const limit = Number(params.limit ?? 20); if (!Number.isInteger(limit) || limit < 1 || limit > 100) return validation({ limit: "必须在 1 到 100 之间" });
    const history = moodHistory.filter((mood) => mood.group_id === params.group_id).slice().reverse().slice(0, limit).map(({ group_id: _group, ...mood }) => mood);
    return ok({ group_id: params.group_id, limit, history });
  }
  if (p === "social/relations" || p.startsWith("social/relations?")) return handleSocialRelations(params);
  if (p === "quality/stats" || p.startsWith("quality/stats")) return handleQualityStats();
  if (p === "quality/recent" || p.startsWith("quality/recent?")) return handleQualityRecent(params);
  if (p === "quality/alerts" || p.startsWith("quality/alerts?")) return handleQualityAlerts(params);
  if (p === "delegation/status" || p.startsWith("delegation/status")) return handleDelegationStatus();
  if (p === "expression/patterns" || p.startsWith("expression/patterns?")) return handleExpressionPatterns(params);
  if (p === "groups" || p.startsWith("groups")) return handleGroups();

  console.warn(`[Mock] Unhandled GET: ${p}`, params);
  return ok({});
}

export async function handleApiPost(path: string, body: unknown = {}): Promise<ApiResponse> {
  await delay();
  if (!body || typeof body !== "object" || Array.isArray(body)) return err("请求体必须为 JSON 对象", "invalid_request");
  const p = path.replace(/^page\/?/, "");
  const configResponse = configServer.handlePost(p, body);
  if (configResponse) return configResponse;
  const data = body as Record<string, unknown>;

  if (p === "recall/test") return handleRecallTest(data);
  if (p === "recall/trace") return handleRecallTrace(data);
  if (p === "memory/update") return handleMemoryUpdate(data);
  if (p === "social/create") return handleSocialCreate(data);
  if (p === "social/update") return handleSocialUpdate(data);
  if (p === "social/delete") return handleSocialDelete(data);
  if (p === "social/batch") return handleSocialBatch(data);
  if (p === "profiles/create") return handleProfileCreate(data);
  if (p === "profiles/update") return handleProfileUpdate(data);
  if (p === "memories/batch") return handleMemoryBatch(data);
  if (p === "knowledge/create") return handleKnowledgeCreate(data);
  if (p === "knowledge/delete") return handleKnowledgeDelete(data);
  if (p === "knowledge/update") return handleKnowledgeUpdate(data);
  if (p === "knowledge/batch") return handleKnowledgeBatch(data);
  if (p === "notes/create") return handleNoteCreate(data);
  if (p === "notes/delete") return handleNoteDelete(data);
  if (p === "notes/update") return handleNoteUpdate(data);
  if (p === "notes/archive") return handleNoteArchive(data);
  if (p === "notes/batch") return handleNoteBatch(data);
  if (p === "profiles/delete") return handleProfileDelete(data);
  if (p === "profiles/batch") return handleProfileBatch(data);
  if (p === "system/rebuild") return ok({ rebuilt: true });
  if (p === "system/purge") return ok({ purged: true });
  if (p === "system/compact") return ok({ compacted: true });
  if (p === "backup/create") return ok({ backup: { name: `backup_${Date.now()}`, size: 2_600_000, created: new Date().toISOString() } });
  if (p === "backup/restore") return handleBackupRestore(data);
  if (p === "backup/delete") return handleBackupDelete(data);
  if (p === "backup/batch-delete") return handleBackupBatchDelete(data);
  if (p === "export/memories") return handleExportMemories(data);
  if (p === "learning/reset") return ok({ message: "Learning parameters reset to defaults", reset: true });
  if (p === "evaluation/run") return handleEvaluationRun(data);
  if (p === "diagnostics/actions/run") return handleDiagnosticAction(data);
  if (p === "review/action") return handleReviewAction(data);
  if (p === "review/refresh") return ok({ refreshed: true, item_count: REVIEW_ITEMS.length });
  if (p === "config/topic-segmentation") return handleTopicSegConfigUpdate(data);
  if (p === "backfill/start") return handleBackfillStart();
  if (p === "backfill/status") return handleBackfillStatus();
  // v1.0.0+ new subsystems
  if (p === "jargon/create") return handleJargonCreate(data);
  if (p === "jargon/update") return handleJargonUpdate(data);
  if (p === "jargon/delete") return handleJargonDelete(data);
  if (p === "jargon/batch") return handleJargonBatch(data);
  if (p === "affection/users/create") return handleAffectionCreate(data);
  if (p === "affection/users/update") return handleAffectionUpdate(data);
  if (p === "affection/users/delete") return handleAffectionDelete(data);
  if (p === "affection/users/batch") return handleAffectionBatch(data);
  if (p === "affection/mood/set") return handleMoodSet(data);
  if (p === "affection/mood/reset") return handleMoodReset(data);
  if (p === "jargon/confirm") return handleJargonConfirm(data);
  if (p === "jargon/mine") return handleJargonMine(data);
  if (p === "quality/reset") return handleQualityReset();

  console.warn(`[Mock] Unhandled POST: ${p}`, data);
  return ok({});
}

// ---- Topic segmentation mocks ----

interface TopicSegConfig {
  enabled: boolean;
  strategy: string;
  available_strategies: { key: string; label: string; desc: string }[];
  strategy_b: { similarity_threshold: number; min_cluster_size: number; max_clusters: number };
  strategy_c: { topic_shift_threshold: number; min_chunk_size: number };
  strategy_d: { stage1_max_topics: number; enable_parallel_stage2: boolean };
}

let _topicSegConfig: TopicSegConfig = {
  enabled: true,
  strategy: "a_b_hybrid",
  available_strategies: [
    { key: "a_b_hybrid", label: "A+B 混合模式", desc: "LLM 主分割 + 嵌入聚类兜底，零额外 API 成本" },
    { key: "a", label: "方案 A — Prompt 工程", desc: "LLM 直接输出 memories[] 数组" },
    { key: "b", label: "方案 B — 嵌入聚类", desc: "key_facts 嵌入相似度聚类分拆" },
    { key: "c", label: "方案 C — 话题预分块", desc: "LLM 调用前检测话题边界" },
    { key: "d", label: "方案 D — 两阶段 LLM", desc: "先识别话题范围再分别抽取" },
  ],
  strategy_b: { similarity_threshold: 0.5, min_cluster_size: 1, max_clusters: 5 },
  strategy_c: { topic_shift_threshold: 0.3, min_chunk_size: 2 },
  strategy_d: { stage1_max_topics: 5, enable_parallel_stage2: true },
};

let _backfillState: { status: "idle" | "running" | "completed" | "failed"; processed: number; total: number; errors: number; job_id: string; started_at: number } = {
  status: "idle", processed: 0, total: 0, errors: 0,
  job_id: "", started_at: 0,
};

function handleTopicSegConfigGet() {
  return ok(_topicSegConfig);
}

function handleTopicSegConfigUpdate(data: Record<string, unknown>) {
  if (data.strategy !== undefined) _topicSegConfig.strategy = String(data.strategy);
  if (data.enabled !== undefined) _topicSegConfig.enabled = Boolean(data.enabled);
  if (data.strategy_b && typeof data.strategy_b === "object") {
    Object.assign(_topicSegConfig.strategy_b, data.strategy_b);
  }
  if (data.strategy_c && typeof data.strategy_c === "object") {
    Object.assign(_topicSegConfig.strategy_c, data.strategy_c);
  }
  if (data.strategy_d && typeof data.strategy_d === "object") {
    Object.assign(_topicSegConfig.strategy_d, data.strategy_d);
  }
  return ok({ ok: true, updated: Object.keys(data), message: "配置已更新" });
}

function handleBackfillStart() {
  if (_backfillState.status === "running") return err("回填任务已在运行中");
  _backfillState = {
    status: "running", processed: 0, total: 1230, errors: 0,
    job_id: `bf_${Date.now()}`, started_at: Date.now(),
  };
  // Simulate async progress
  const iv = setInterval(() => {
    if (_backfillState.status !== "running") { clearInterval(iv); return; }
    _backfillState.processed += 100;
    if (_backfillState.processed >= _backfillState.total) {
      _backfillState.processed = _backfillState.total;
      _backfillState.status = "completed";
      clearInterval(iv);
    }
  }, 2000);
  return ok({ job_id: _backfillState.job_id, message: "回填任务已启动" });
}

function handleBackfillStatus() {
  return ok(_backfillState);
}

// ---- v1.0.0+ new subsystem handlers ----

function handleJargonCandidates(params: Record<string, string>): ApiResponse {
  const groupId = params.group_id;
  let items = [...JARGON_CANDIDATES];
  if (groupId) items = items.filter((c) => c.group_id === groupId);
  const limit = parseInt(params.limit ?? "20", 10);
  return ok({ candidates: items.slice(0, limit), total: items.length, group_id: groupId ?? "" });
}

function handleJargonMeanings(params: Record<string, string>): ApiResponse {
  const groupId = params.group_id;
  const confirmedOnly = params.confirmed_only !== "false";
  let items = [...JARGON_MEANINGS];
  if (groupId) items = items.filter((m) => m.group_id === groupId);
  if (confirmedOnly) items = items.filter((m) => m.is_confirmed);
  return ok({ meanings: items, total: items.length, group_id: groupId ?? "" });
}

function handleJargonStats(params: Record<string, string>): ApiResponse {
  const groupId = params.group_id ?? "group_001";
  const candidates = JARGON_CANDIDATES.filter((c) => c.group_id === groupId);
  const meanings = JARGON_MEANINGS.filter((m) => m.group_id === groupId);
  const confirmed = meanings.filter((m) => m.is_confirmed);
  return ok({
    group_id: groupId,
    total_terms: meanings.length,
    candidate_count: candidates.length,
    top_candidates: candidates.slice(0, 5),
    store_total: meanings.length,
    store_confirmed: confirmed.length,
  });
}

function handleJargonConfirm(body: Record<string, unknown>): ApiResponse {
  const term = body.term as string;
  const groupId = body.group_id as string;
  const confirmed = body.confirmed !== false;
  if (!term || !groupId) return err("term and group_id required");
  const found = JARGON_MEANINGS.find((m) => m.term === term && m.group_id === groupId);
  if (found) {
    // Mock server intentionally mutates in-memory state to simulate
    // server-side persistence across requests within a session.
    found.is_confirmed = confirmed;
    found.updated_at = Date.now() / 1000;
    (found as MutableRecord).revision = revision();
  }
  return ok({ term, group_id: groupId, action: confirmed ? "confirmed" : "rejected", message: confirmed ? `「${term}」已确认` : `「${term}」已驳回` });
}

function handleJargonMine(body: Record<string, unknown>): ApiResponse {
  const groupId = body.group_id as string;
  if (!groupId) return err("group_id required");
  const results = JARGON_MEANINGS.filter((m) => m.group_id === groupId).slice(0, 3);
  return ok({ group_id: groupId, inferred_count: results.length, results, message: `在 ${groupId} 中发现了 ${results.length} 个黑话` });
}

function handleAffectionStatus(params: Record<string, string>): ApiResponse {
  const groupId = params.group_id ?? "group_001";
  const data = AFFECTION_DATA[groupId];
  if (!data) return err("No affection data for this group");
  return ok(data);
}

function handleSocialRelations(params: Record<string, string>): ApiResponse {
  let items = [...SOCIAL_RELATIONS];
  if (params.group_id) items = items.filter((r) => r.group_id === params.group_id);
  if (params.category && params.category !== "all") items = items.filter((r) => r.category === params.category);
  return ok({ relations: items, total: items.length });
}

function handleQualityStats(): ApiResponse {
  const scores = QUALITY_SCORES;
  const n = scores.length;
  const avg = (arr: number[]) => arr.reduce((a, b) => a + b, 0) / n;
  return ok({
    avg_overall: avg(scores.map((s) => s.overall)),
    avg_consistency: avg(scores.map((s) => s.consistency)),
    avg_coherence: avg(scores.map((s) => s.coherence)),
    avg_relevance: avg(scores.map((s) => s.relevance)),
    avg_freshness: avg(scores.map((s) => s.freshness)),
    avg_accuracy: avg(scores.map((s) => s.accuracy)),
    total_scored: 1042,
    paused: false,
    pause_reason: "",
    alert_counts: { critical: 1, high: 1, medium: 1, info: 1 },
  });
}

function handleQualityRecent(params: Record<string, string>): ApiResponse {
  const limit = parseInt(params.limit ?? "20", 10);
  return ok({ scores: QUALITY_SCORES.slice(0, limit), total_scores: QUALITY_SCORES.length });
}

function handleQualityAlerts(params: Record<string, string>): ApiResponse {
  let items = [...QUALITY_ALERTS];
  if (params.level) items = items.filter((a) => a.level === params.level);
  const limit = parseInt(params.limit ?? "50", 10);
  return ok({ alerts: items.slice(0, limit), total_alerts: QUALITY_ALERTS.length, filtered_count: items.length });
}

function handleQualityReset(): ApiResponse {
  return ok({ message: "quality scorer and alert history reset" });
}

function handleDelegationStatus(): ApiResponse {
  return ok(DELEGATION_STATUS);
}

function handleExpressionPatterns(params: Record<string, string>): ApiResponse {
  const groupId = params.group_id ?? "group_001";
  const items = EXPRESSION_PATTERNS.filter((p) => p.group_id === groupId);
  return ok({ patterns: items, total: EXPRESSION_PATTERNS.length, group_patterns: items.length, group_id: groupId });
}

function handleGroups(): ApiResponse {
  return ok({
    groups: [
      { group_id: "group_001", source: "session", message_count: 342 },
      { group_id: "group_002", source: "jargon", message_count: 198 },
    ],
    total: 2,
  });
}
