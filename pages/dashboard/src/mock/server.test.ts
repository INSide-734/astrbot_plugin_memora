import { beforeEach, describe, expect, it } from "vitest";

import * as mockData from "./data";
import * as mockServer from "./server";

type JsonObject = Record<string, unknown>;
type ApiResponse = {
  status: string;
  data?: unknown;
  message?: string;
  code?: string;
  field_errors?: Record<string, string>;
};

type EntityEnvelope = { entity: JsonObject; revision: string };

const serverExports = mockServer as unknown as Record<string, unknown>;
const resetMockServerState = serverExports.resetMockServerState;
const get = mockServer.handleApiGet as (path: string, params?: Record<string, string>) => Promise<ApiResponse>;
const post = mockServer.handleApiPost as (path: string, body?: unknown) => Promise<ApiResponse>;

const revisionPattern = /^mock-entity-revision-\d{8}$/;
const socialIdentity = (suffix: string) => ({
  from_user: `task17-social-from-${suffix}`,
  to_user: `task17-social-to-${suffix}`,
  relation_type: "colleague",
  group_id: "group_001",
});
const profileDraft = (suffix: string) => ({
  user_id: `task17-profile-${suffix}`,
  display_name: `Task 17 ${suffix}`,
  preferences: {
    reply_style: "concise",
    preferred_topics: ["testing", suffix],
    avoided_topics: ["spoilers"],
    active_hours: [9, 17],
  },
  tags: [{ category: "interest", value: `tag-${suffix}`, confidence: 0.9 }],
});
const jargonDraft = (suffix: string) => ({
  term: `task17-jargon-${suffix}`,
  group_id: "group_001",
  meaning: `deterministic meaning ${suffix}`,
  confidence: 0.8,
  is_jargon: true,
  is_confirmed: false,
  is_global: false,
});
const affectionDraft = (suffix: string, affection_score = 42) => ({
  group_id: "group_001",
  user_id: `task17-affection-${suffix}`,
  affection_score,
});

function resetIfAvailable(): void {
  if (typeof resetMockServerState === "function") {
    (resetMockServerState as () => void)();
  }
}

function requireReset(): () => void {
  expect(
    resetMockServerState,
    "mock server must export resetMockServerState before mutable route tests can be deterministic",
  ).toBeTypeOf("function");
  return resetMockServerState as () => void;
}

function okData(response: ApiResponse): JsonObject {
  expect(response.status).toBe("ok");
  expect(response.data).toBeTruthy();
  expect(typeof response.data).toBe("object");
  return response.data as JsonObject;
}

function entityEnvelope(response: ApiResponse): EntityEnvelope {
  const data = okData(response);
  expect(data.entity).toBeTruthy();
  expect(typeof data.entity).toBe("object");
  expect(data.revision).toMatch(revisionPattern);
  expect(data.entity).not.toHaveProperty("revision");
  return data as EntityEnvelope;
}

function expectValidation(response: ApiResponse, fieldErrors: Record<string, string>): void {
  expect(response).toMatchObject({
    status: "error",
    code: "validation_error",
    field_errors: fieldErrors,
  });
}

function expectBatch(
  response: ApiResponse,
  succeededIds: JsonObject[],
  failures: Array<JsonObject>,
): JsonObject {
  const data = okData(response);
  expect(data).toEqual({
    total: succeededIds.length + failures.length,
    succeeded_count: succeededIds.length,
    failed_count: failures.length,
    succeeded_ids: succeededIds,
    failures,
  });
  return data;
}

beforeEach(() => resetIfAvailable());

describe("mutable mock reset and revision allocator", () => {
  it("exports the deterministic reset boundary", () => {
    expect(
      resetMockServerState,
      "Expected ./server to export resetMockServerState(): void; use a namespace import so this RED test remains transform-safe",
    ).toBeTypeOf("function");
  });

  it("deep-resets exported containers and nested seed values without contaminating the baseline", () => {
    const reset = requireReset();
    const references = {
      memories: mockData.MEMORIES,
      profiles: mockData.PROFILES,
      knowledge: mockData.KNOWLEDGE_ENTRIES,
      notes: mockData.NOTES,
      jargon: mockData.JARGON_MEANINGS,
      affection: mockData.AFFECTION_DATA,
      social: mockData.SOCIAL_RELATIONS,
    };
    const baseline = structuredClone(references);

    mockData.MEMORIES[0].summary = "contaminated";
    (mockData.PROFILES[0].preferences as JsonObject).reply_style = "contaminated";
    mockData.KNOWLEDGE_ENTRIES[0].title = "contaminated";
    mockData.NOTES[0].tags.push("contaminated");
    mockData.JARGON_MEANINGS[0].meaning = "contaminated";
    mockData.AFFECTION_DATA.group_001.current_mood.description = "contaminated";
    mockData.SOCIAL_RELATIONS[0].tags.push("contaminated");

    reset();
    expect(mockData.MEMORIES).toBe(references.memories);
    expect(mockData.PROFILES).toBe(references.profiles);
    expect(mockData.KNOWLEDGE_ENTRIES).toBe(references.knowledge);
    expect(mockData.NOTES).toBe(references.notes);
    expect(mockData.JARGON_MEANINGS).toBe(references.jargon);
    expect(mockData.AFFECTION_DATA).toBe(references.affection);
    expect(mockData.SOCIAL_RELATIONS).toBe(references.social);
    expect(references).toEqual(baseline);

    (mockData.PROFILES[0].preferences as JsonObject).reply_style = "second contamination";
    reset();
    expect(references).toEqual(baseline);
  });

  it("gives seed GET records stable revisions and repeats allocation after reset", async () => {
    const reset = requireReset();
    const firstProfiles = okData(await get("profiles", { limit: "100", offset: "0" })).profiles as JsonObject[];
    const firstSocial = okData(await get("social/relations", { group_id: "group_001" })).relations as JsonObject[];
    const firstJargon = okData(await get("jargon/meanings", { group_id: "group_001", confirmed_only: "false" })).meanings as JsonObject[];
    const firstAffection = okData(await get("affection/users", { group_id: "group_001", limit: "50", offset: "0" })).users as JsonObject[];
    const snapshot = [firstProfiles[0].revision, firstSocial[0].revision, firstJargon[0].revision, firstAffection[0].revision];

    expect(snapshot).toEqual(snapshot.map((revision) => expect.stringMatching(revisionPattern)));
    expect(new Set(snapshot).size).toBe(snapshot.length);
    await get("profiles", { limit: "1", offset: "0" });
    await get("social/relations", { group_id: "group_001" });
    const created = entityEnvelope(await post("profiles/create", profileDraft("allocator")));

    reset();
    const repeatedProfiles = okData(await get("profiles", { limit: "100", offset: "0" })).profiles as JsonObject[];
    const repeatedSocial = okData(await get("social/relations", { group_id: "group_001" })).relations as JsonObject[];
    const repeatedJargon = okData(await get("jargon/meanings", { group_id: "group_001", confirmed_only: "false" })).meanings as JsonObject[];
    const repeatedAffection = okData(await get("affection/users", { group_id: "group_001", limit: "50", offset: "0" })).users as JsonObject[];
    expect([repeatedProfiles[0].revision, repeatedSocial[0].revision, repeatedJargon[0].revision, repeatedAffection[0].revision]).toEqual(snapshot);
    expect(entityEnvelope(await post("profiles/create", profileDraft("allocator"))).revision).toBe(created.revision);
  });

  it("resets evaluation, review, action, and backup repositories", async () => {
    const reset = requireReset();
    const reports = structuredClone(mockData.EVALUATION_REPORTS);
    const reviewItems = structuredClone(mockData.REVIEW_ITEMS);
    const reviewActions = structuredClone(mockData.REVIEW_ACTIONS);
    const backups = structuredClone((okData(await get("backup/list")).backups as JsonObject[]));

    mockData.EVALUATION_REPORTS.unshift({ report_id: "task17-contaminated" } as never);
    mockData.REVIEW_ITEMS.splice(0, 1);
    mockData.REVIEW_ACTIONS["task17-contaminated"] = [{ action: "delete" }] as never;
    await post("backup/delete", { name: backups[0].name });
    reset();

    expect(mockData.EVALUATION_REPORTS).toEqual(reports);
    expect(mockData.REVIEW_ITEMS).toEqual(reviewItems);
    expect(mockData.REVIEW_ACTIONS).toEqual(reviewActions);
    expect(okData(await get("backup/list")).backups).toEqual(backups);
  });
});

describe("social CRUD and batch contracts", () => {
  it("persists create, list, update, stale conflict, and delete with exact identity envelopes", async () => {
    const identity = socialIdentity("crud");
    const created = entityEnvelope(await post("social/create", { ...identity, strength: 0.4, tags: ["first"] }));
    expect(created.entity).toMatchObject({ ...identity, strength: 0.4, tags: ["first"], frequency: 0, last_interaction: 0 });

    const listed = okData(await get("social/relations", { group_id: identity.group_id })).relations as JsonObject[];
    expect(listed).toContainEqual({ ...created.entity, revision: created.revision });

    const updated = entityEnvelope(await post("social/update", {
      identity,
      changes: { strength: 0.75, tags: ["updated"] },
      expected_revision: created.revision,
    }));
    expect(updated.entity).toMatchObject({ ...identity, strength: 0.75, tags: ["updated"] });
    expect(updated.revision).not.toBe(created.revision);

    const conflict = await post("social/update", {
      identity,
      changes: { strength: 0.1 },
      expected_revision: created.revision,
    });
    expect(conflict).toEqual({
      status: "error",
      code: "edit_conflict",
      message: expect.any(String),
      data: { current_entity: updated.entity, current_revision: updated.revision },
    });

    expect(okData(await post("social/delete", { identity, expected_revision: updated.revision }))).toEqual({ deleted: true, identity });
    expect((okData(await get("social/relations", { group_id: identity.group_id })).relations as JsonObject[])).not.toContainEqual(expect.objectContaining(identity));
  });

  it.each(["add_tags", "remove_tags"] as const)("supports %s with ordered entity revisions", async (action) => {
    const identity = socialIdentity(action);
    const created = entityEnvelope(await post("social/create", { ...identity, strength: 0.5, tags: ["keep", "remove"] }));
    const response = await post("social/batch", {
      action,
      items: [{ identity, expected_revision: created.revision }],
      params: { tags: action === "add_tags" ? ["new"] : ["remove"] },
    });
    expectBatch(response, [identity], []);
    const relation = (okData(await get("social/relations", { group_id: identity.group_id })).relations as JsonObject[]).find((item) => item.from_user === identity.from_user);
    expect(relation?.tags).toEqual(action === "add_tags" ? ["keep", "remove", "new"] : ["keep"]);
    expect(relation?.revision).toMatch(revisionPattern);
    expect(relation?.revision).not.toBe(created.revision);
  });

  it("supports delete batch partial failure without losing identities", async () => {
    const identity = socialIdentity("partial");
    const missing = socialIdentity("missing");
    const created = entityEnvelope(await post("social/create", { ...identity, strength: 0.5, tags: [] }));
    const response = await post("social/batch", {
      action: "delete",
      items: [
        { identity: missing, expected_revision: "mock-entity-revision-99999999" },
        { identity, expected_revision: created.revision },
      ],
      params: {},
    });
    const data = okData(response);
    expect(data).toMatchObject({ total: 2, succeeded_count: 1, failed_count: 1, succeeded_ids: [identity] });
    expect(data.failures).toEqual([{ identity: missing, code: "not_found", message: expect.any(String) }]);
  });

  it("rejects 101 batch items and required identity text longer than 128 characters", async () => {
    const items = Array.from({ length: 101 }, (_, index) => ({ identity: socialIdentity(`cap-${index}`), expected_revision: "mock-entity-revision-00000001" }));
    expectValidation(await post("social/batch", { action: "delete", items, params: {} }), { items: "项目数量必须在 1 到 100 之间" });
    expectValidation(await post("social/create", { ...socialIdentity("long"), from_user: "x".repeat(129), strength: 0.5, tags: [] }), { from_user: "文本过长" });
  });
});

describe("profile CRUD, structured fields, and compatibility", () => {
  it("persists structured create/list/detail/update/delete envelopes", async () => {
    const draft = profileDraft("crud");
    const created = entityEnvelope(await post("profiles/create", draft));
    expect(created.entity).toMatchObject(draft);
    expect(created.entity.preferences).toEqual(draft.preferences);
    expect(created.entity.tags).toEqual(draft.tags);

    const list = okData(await get("profiles", { limit: "1", offset: "0" }));
    expect(list).toMatchObject({ total: expect.any(Number) });
    const all = okData(await get("profiles", { limit: "100", offset: "0" })).profiles as JsonObject[];
    expect(all).toContainEqual({ ...created.entity, revision: created.revision });
    const detail = okData(await get("profiles/detail", { user_id: draft.user_id }));
    expect(detail).toEqual({ ...created.entity, revision: created.revision });

    const changes = {
      display_name: "Updated profile",
      preferences: { ...draft.preferences, preferred_topics: ["updated"] },
      tags: [{ category: "skill", value: "vitest", confidence: 1 }],
    };
    const updated = entityEnvelope(await post("profiles/update", { identity: { user_id: draft.user_id }, changes, expected_revision: created.revision }));
    expect(updated.entity).toMatchObject({ user_id: draft.user_id, ...changes });
    expect(okData(await post("profiles/delete", { identity: { user_id: draft.user_id }, expected_revision: updated.revision }))).toEqual({ deleted: true, identity: { user_id: draft.user_id } });
  });

  it.each(["tags_add", "tags_remove"] as const)("supports profile batch %s", async (action) => {
    const draft = profileDraft(action);
    const created = entityEnvelope(await post("profiles/create", draft));
    const tag = action === "tags_add" ? { category: "custom", value: "new", confidence: 0.7 } : draft.tags[0];
    expectBatch(await post("profiles/batch", {
      action,
      items: [{ identity: { user_id: draft.user_id }, expected_revision: created.revision }],
      params: { tag },
    }), [{ user_id: draft.user_id }], []);
    const detail = okData(await get("profiles/detail", { user_id: draft.user_id }));
    const tags = detail.tags as JsonObject[];
    expect(tags).toEqual(action === "tags_add" ? [...draft.tags, tag] : []);
    expect(detail.revision).not.toBe(created.revision);
  });

  it("supports revisioned and legacy profile delete/batch against the same state", async () => {
    const revisioned = profileDraft("batch-delete");
    const legacySingle = profileDraft("legacy-single");
    const legacyBatch = profileDraft("legacy-batch");
    const revision = entityEnvelope(await post("profiles/create", revisioned)).revision;
    await post("profiles/create", legacySingle);
    await post("profiles/create", legacyBatch);
    expectBatch(await post("profiles/batch", { action: "delete", items: [{ identity: { user_id: revisioned.user_id }, expected_revision: revision }], params: {} }), [{ user_id: revisioned.user_id }], []);
    expect(okData(await post("profiles/delete", { user_id: legacySingle.user_id }))).toEqual({ deleted: true, user_id: legacySingle.user_id });
    expect(okData(await post("profiles/batch", { action: "delete", user_ids: [legacyBatch.user_id] }))).toEqual({ deleted_count: 1, failed_count: 0, total: 1, failed_ids: [] });
    const users = (okData(await get("profiles", { limit: "100", offset: "0" })).profiles as JsonObject[]).map((item) => item.user_id);
    expect(users).not.toEqual(expect.arrayContaining([revisioned.user_id, legacySingle.user_id, legacyBatch.user_id]));
  });

  it("returns exact stale conflict, partial failure, cap, and 129-character validation shapes", async () => {
    const draft = profileDraft("conflict");
    const created = entityEnvelope(await post("profiles/create", draft));
    const updated = entityEnvelope(await post("profiles/update", { identity: { user_id: draft.user_id }, changes: { display_name: "current" }, expected_revision: created.revision }));
    expect(await post("profiles/update", { identity: { user_id: draft.user_id }, changes: { display_name: "stale" }, expected_revision: created.revision })).toEqual({
      status: "error", code: "edit_conflict", message: expect.any(String), data: { current_entity: updated.entity, current_revision: updated.revision },
    });
    const partial = okData(await post("profiles/batch", { action: "delete", items: [
      { identity: { user_id: "task17-profile-missing" }, expected_revision: created.revision },
      { identity: { user_id: draft.user_id }, expected_revision: updated.revision },
    ], params: {} }));
    expect(partial).toMatchObject({ succeeded_ids: [{ user_id: draft.user_id }], failures: [{ identity: { user_id: "task17-profile-missing" }, code: "not_found", message: expect.any(String) }] });
    const cap = Array.from({ length: 101 }, (_, i) => ({ identity: { user_id: `task17-profile-cap-${i}` }, expected_revision: created.revision }));
    expectValidation(await post("profiles/batch", { action: "delete", items: cap }), { items: "项目数量必须在 1 到 100 之间" });
    expectValidation(await post("profiles/create", { ...profileDraft("long"), user_id: "x".repeat(129) }), { user_id: "文本过长" });
  });
});

describe("jargon CRUD, all batch actions, and legacy routes", () => {
  it("persists create/list/update/stale conflict/delete envelopes", async () => {
    const draft = jargonDraft("crud");
    const identity = { term: draft.term, group_id: draft.group_id };
    const created = entityEnvelope(await post("jargon/create", draft));
    expect(created.entity).toMatchObject(draft);
    expect(okData(await get("jargon/meanings", { group_id: draft.group_id, confirmed_only: "false" })).meanings).toContainEqual({ ...created.entity, revision: created.revision });
    const updated = entityEnvelope(await post("jargon/update", { identity, changes: { meaning: "updated meaning", confidence: 0.95 }, expected_revision: created.revision }));
    expect(updated.entity).toMatchObject({ ...identity, meaning: "updated meaning", confidence: 0.95 });
    expect(await post("jargon/update", { identity, changes: { meaning: "stale" }, expected_revision: created.revision })).toEqual({ status: "error", code: "edit_conflict", message: expect.any(String), data: { current_entity: updated.entity, current_revision: updated.revision } });
    expect(okData(await post("jargon/delete", { identity, expected_revision: updated.revision }))).toEqual({ deleted: true, identity });
  });

  it.each([
    ["confirm", { is_confirmed: true }],
    ["unconfirm", { is_confirmed: false }],
    ["set_global", { is_global: true }],
    ["unset_global", { is_global: false }],
  ] as const)("supports jargon batch %s", async (action, expected) => {
    const draft = jargonDraft(action);
    const identity = { term: draft.term, group_id: draft.group_id };
    const created = entityEnvelope(await post("jargon/create", draft));
    expectBatch(await post("jargon/batch", { action, items: [{ identity, expected_revision: created.revision }] }), [identity], []);
    const meaning = (okData(await get("jargon/meanings", { group_id: draft.group_id, confirmed_only: "false" })).meanings as JsonObject[]).find((item) => item.term === draft.term);
    expect(meaning).toMatchObject(expected);
    expect(meaning?.revision).not.toBe(created.revision);
  });

  it("supports jargon batch delete and ordered partial failure", async () => {
    const draft = jargonDraft("delete");
    const identity = { term: draft.term, group_id: draft.group_id };
    const missing = { term: "task17-jargon-missing", group_id: draft.group_id };
    const created = entityEnvelope(await post("jargon/create", draft));
    const result = okData(await post("jargon/batch", { action: "delete", items: [
      { identity: missing, expected_revision: created.revision },
      { identity, expected_revision: created.revision },
    ] }));
    expect(result).toMatchObject({ total: 2, succeeded_count: 1, failed_count: 1, succeeded_ids: [identity], failures: [{ identity: missing, code: "not_found", message: expect.any(String) }] });
  });

  it("keeps legacy confirm and mine on the same state", async () => {
    const draft = jargonDraft("legacy");
    await post("jargon/create", draft);
    expect(okData(await post("jargon/confirm", { term: draft.term, group_id: draft.group_id, confirmed: true }))).toMatchObject({ term: draft.term, group_id: draft.group_id, action: "confirmed" });
    const stored = (okData(await get("jargon/meanings", { group_id: draft.group_id, confirmed_only: "false" })).meanings as JsonObject[]).find((item) => item.term === draft.term);
    expect(stored).toMatchObject({ is_confirmed: true, revision: expect.stringMatching(revisionPattern) });
    expect(okData(await post("jargon/mine", { group_id: draft.group_id, limit: 5 }))).toMatchObject({ group_id: draft.group_id, inferred_count: expect.any(Number), results: expect.any(Array) });
  });

  it("rejects 101 batch items and a 129-character term", async () => {
    const items = Array.from({ length: 101 }, (_, i) => ({ identity: { term: `term-${i}`, group_id: "group_001" }, expected_revision: "mock-entity-revision-00000001" }));
    expectValidation(await post("jargon/batch", { action: "delete", items }), { items: "项目数量必须在 1 到 100 之间" });
    expectValidation(await post("jargon/create", { ...jargonDraft("long"), term: "x".repeat(129) }), { term: "文本过长" });
  });
});

describe("affection users and non-revisioned mood", () => {
  it("persists paginated user create/list/update/stale conflict/delete envelopes", async () => {
    const draft = { ...affectionDraft("crud"), group_id: "task17-affection-pagination" };
    const secondDraft = { ...affectionDraft("crud-second"), group_id: draft.group_id };
    const identity = { group_id: draft.group_id, user_id: draft.user_id };
    const created = entityEnvelope(await post("affection/users/create", draft));
    const second = entityEnvelope(await post("affection/users/create", secondDraft));
    expect(created.entity).toMatchObject({ ...draft, interaction_count: 0, last_interaction: 0 });
    const allUsers = okData(await get("affection/users", { group_id: draft.group_id, limit: "50", offset: "0" })).users as JsonObject[];
    const createdOffset = allUsers.findIndex((user) => user.user_id === draft.user_id);
    const secondOffset = allUsers.findIndex((user) => user.user_id === secondDraft.user_id);
    expect(new Set([createdOffset, secondOffset])).toEqual(new Set([0, 1]));
    const page = okData(await get("affection/users", { group_id: draft.group_id, limit: "1", offset: String(createdOffset) }));
    expect(page).toEqual({ group_id: draft.group_id, users: [{ ...created.entity, revision: created.revision }], total: 2, limit: 1, offset: createdOffset });
    expect(second.entity).toMatchObject(secondDraft);
    const updated = entityEnvelope(await post("affection/users/update", { identity, changes: { affection_score: 55 }, expected_revision: created.revision }));
    expect(updated.entity).toMatchObject({ ...identity, affection_score: 55 });
    expect(await post("affection/users/update", { identity, changes: { affection_score: 1 }, expected_revision: created.revision })).toEqual({ status: "error", code: "edit_conflict", message: expect.any(String), data: { current_entity: updated.entity, current_revision: updated.revision } });
    expect(okData(await post("affection/users/delete", { identity, expected_revision: updated.revision }))).toEqual({ deleted: true, identity });
  });

  it("supports affection delete batch, ordered partial failure, and the 100-item cap", async () => {
    const draft = affectionDraft("batch");
    const identity = { group_id: draft.group_id, user_id: draft.user_id };
    const missing = { group_id: draft.group_id, user_id: "task17-affection-missing" };
    const created = entityEnvelope(await post("affection/users/create", draft));
    const result = okData(await post("affection/users/batch", { action: "delete", items: [
      { identity: missing, expected_revision: created.revision },
      { identity, expected_revision: created.revision },
    ] }));
    expect(result).toMatchObject({ total: 2, succeeded_count: 1, failed_count: 1, succeeded_ids: [identity], failures: [{ identity: missing, code: "not_found", message: expect.any(String) }] });
    const cap = Array.from({ length: 101 }, (_, i) => ({ identity: { group_id: "group_001", user_id: `task17-affection-cap-${i}` }, expected_revision: created.revision }));
    expectValidation(await post("affection/users/batch", { action: "delete", items: cap }), { items: "项目数量必须在 1 到 100 之间" });
  });

  it.each([
    [true, "必须为整数"],
    [1.5, "必须为整数"],
    [-101, "必须在 -100 到 100 之间"],
    [101, "必须在 -100 到 100 之间"],
  ] as const)("rejects invalid affection_score %s exactly", async (affection_score, message) => {
    const response = await post("affection/users/create", affectionDraft(`invalid-${String(affection_score)}`, affection_score as unknown as number));
    expect(response).toMatchObject({
      status: "error",
      code: "validation_error",
      field_errors: { affection_score: message },
    });
  });

  it("rejects a 129-character user identity", async () => {
    expectValidation(await post("affection/users/create", { group_id: "group_001", user_id: "x".repeat(129), affection_score: 1 }), { user_id: "文本过长" });
  });

  it("sets, records, and resets mood without consuming or returning entity revisions", async () => {
    const reset = requireReset();
    const beforeProfile = entityEnvelope(await post("profiles/create", profileDraft("mood-sequence-before"))).revision;
    reset();
    const setResult = okData(await post("affection/mood/set", { group_id: "group_001", mood_type: "happy", intensity: 0.7, duration_hours: 2.5, description: "Task 17 mood" }));
    expect(setResult).toMatchObject({ mood_type: "happy", intensity: 0.7, duration_hours: 2.5, description: "Task 17 mood", start_time: expect.any(Number), is_active: true });
    expect(setResult).not.toHaveProperty("revision");
    expect(setResult).not.toHaveProperty("entity");
    const history = okData(await get("affection/moods/history", { group_id: "group_001", limit: "50" }));
    expect(history.history).toContainEqual(expect.objectContaining({ mood_type: "happy", intensity: 0.7, duration_hours: 2.5, description: "Task 17 mood" }));
    const resetResult = okData(await post("affection/mood/reset", { group_id: "group_001" }));
    expect(resetResult).toMatchObject({ mood_type: expect.any(String), intensity: expect.any(Number), duration_hours: expect.any(Number), description: expect.any(String) });
    expect(resetResult).not.toHaveProperty("revision");
    expect(entityEnvelope(await post("profiles/create", profileDraft("mood-sequence-before"))).revision).toBe(beforeProfile);
  });
});

describe("Python-compatible validation and read envelopes", () => {
  it("rejects affection batch actions and params before deleting anything", async () => {
    const draft = affectionDraft("batch-validation");
    const identity = { group_id: draft.group_id, user_id: draft.user_id };
    const created = entityEnvelope(await post("affection/users/create", draft));
    expectValidation(await post("affection/users/batch", { action: "set_score", items: [{ identity, expected_revision: created.revision }], params: { score: 5 } }), { action: "仅支持 delete" });
    expectValidation(await post("affection/users/batch", { action: "delete", items: [{ identity, expected_revision: created.revision }], params: { score: 5 } }), { score: "字段不可写" });
    expect((okData(await get("affection/users", { group_id: draft.group_id, limit: "50", offset: "0" })).users as JsonObject[])).toContainEqual({ ...created.entity, revision: created.revision });
  });

  it("rejects read-only, malformed, and out-of-range revisioned changes without mutation", async () => {
    const socialId = socialIdentity("invalid-update");
    const social = entityEnvelope(await post("social/create", { ...socialId, strength: 0.5, tags: [] }));
    expectValidation(await post("social/update", { identity: socialId, changes: { from_user: "other", strength: 2 }, expected_revision: social.revision }), { from_user: "字段不可写", "changes.strength": "必须在 0.0 到 1.0 之间" });
    const profile = profileDraft("invalid-update");
    const profileCreated = entityEnvelope(await post("profiles/create", profile));
    expectValidation(await post("profiles/update", { identity: { user_id: profile.user_id }, changes: { preferences: [] }, expected_revision: profileCreated.revision }), { "changes.preferences": "必须为对象" });
    const jargon = jargonDraft("invalid-update");
    const jargonCreated = entityEnvelope(await post("jargon/create", jargon));
    expectValidation(await post("jargon/update", { identity: { term: jargon.term, group_id: jargon.group_id }, changes: { confidence: 2, is_global: "yes" }, expected_revision: jargonCreated.revision }), { "changes.confidence": "必须在 0.0 到 1.0 之间", "changes.is_global": "必须为布尔值" });
    const affection = affectionDraft("invalid-update");
    const affectionCreated = entityEnvelope(await post("affection/users/create", affection));
    expectValidation(await post("affection/users/update", { identity: { group_id: affection.group_id, user_id: affection.user_id }, changes: { affection_score: 101, user_id: "other" }, expected_revision: affectionCreated.revision }), { user_id: "字段不可写", "changes.affection_score": "必须在 -100 到 100 之间" });
  });

  it("rejects invalid mood values without changing current mood or history", async () => {
    const before = structuredClone(okData(await get("affection/status", { group_id: "group_001" })).current_mood);
    expectValidation(await post("affection/mood/set", { group_id: "group_001", mood_type: "unknown", intensity: 0, duration_hours: 0, description: 42 }), { mood_type: "不支持的情绪类型", intensity: "必须在 0.1 到 1.0 之间", duration_hours: "必须在 0.25 到 168.0 之间", description: "必须为字符串" });
    expect(okData(await get("affection/status", { group_id: "group_001" })).current_mood).toEqual(before);
    expect(okData(await get("affection/moods/history", { group_id: "group_001", limit: "20" }))).toEqual({ group_id: "group_001", limit: 20, history: [] });
  });

  it("returns Python read envelopes and validates required pagination", async () => {
    expectValidation(await get("affection/users", { limit: "50", offset: "0" }), { group_id: "不能为空" });
    expectValidation(await get("affection/users", { group_id: "group_001", limit: "0", offset: "-1" }), { limit: "必须在 1 到 100 之间", offset: "必须在 0 到 1000000 之间" });
    expect(okData(await get("affection/users", { group_id: "group_001", limit: "1", offset: "0" }))).toMatchObject({ group_id: "group_001", limit: 1, offset: 0, total: expect.any(Number) });
    const profile = (okData(await get("profiles", { limit: "1", offset: "0" })).profiles as JsonObject[])[0];
    expect(okData(await get("profiles/detail", { user_id: String(profile.user_id) }))).toEqual(profile);
  });

  it("keeps latest mood first and omits mock-only history totals", async () => {
    await post("affection/mood/set", { group_id: "group_001", mood_type: "happy", intensity: 0.6, duration_hours: 1, description: "first" });
    await post("affection/mood/set", { group_id: "group_001", mood_type: "calm", intensity: 0.7, duration_hours: 2, description: "latest" });
    expect(okData(await get("affection/moods/history", { group_id: "group_001", limit: "1" }))).toEqual({ group_id: "group_001", limit: 1, history: [expect.objectContaining({ description: "latest" })] });
  });

  it("preserves legacy profile update, delete, and batch response shapes", async () => {
    const updateDraft = profileDraft("legacy-update"); await post("profiles/create", updateDraft);
    expect(okData(await post("profiles/update", { user_id: updateDraft.user_id, display_name: "Legacy updated", preferences: { reply_style: "brief" } }))).toMatchObject({ user_id: updateDraft.user_id, display_name: "Legacy updated", preferences: { reply_style: "brief" } });
    const deleteDraft = profileDraft("legacy-delete-shape"); await post("profiles/create", deleteDraft);
    expect(okData(await post("profiles/delete", { user_id: deleteDraft.user_id }))).toEqual({ deleted: true, user_id: deleteDraft.user_id });
    const batchDraft = profileDraft("legacy-batch-shape"); await post("profiles/create", batchDraft);
    expect(okData(await post("profiles/batch", { action: "delete", user_ids: [batchDraft.user_id, "missing"] }))).toEqual({ deleted_count: 1, failed_count: 1, total: 2, failed_ids: ["missing"] });
  });
});

describe("Python-compatible batch failure envelopes", () => {
  it("continues after malformed items and includes current state for conflicts", async () => {
    const cases = [
      { prefix: "social", create: () => post("social/create", { ...socialIdentity("batch-conflict"), strength: 0.5, tags: [] }), update: (r: string) => post("social/update", { identity: socialIdentity("batch-conflict"), changes: { strength: 0.6 }, expected_revision: r }), batch: (r: string) => post("social/batch", { action: "delete", items: [null, { identity: socialIdentity("batch-conflict"), expected_revision: r }], params: {} }), identity: socialIdentity("batch-conflict") },
      { prefix: "profiles", create: () => post("profiles/create", profileDraft("batch-conflict")), update: (r: string) => post("profiles/update", { identity: { user_id: profileDraft("batch-conflict").user_id }, changes: { display_name: "current" }, expected_revision: r }), batch: (r: string) => post("profiles/batch", { action: "delete", items: [null, { identity: { user_id: profileDraft("batch-conflict").user_id }, expected_revision: r }], params: {} }), identity: { user_id: profileDraft("batch-conflict").user_id } },
      { prefix: "jargon", create: () => post("jargon/create", jargonDraft("batch-conflict")), update: (r: string) => post("jargon/update", { identity: { term: jargonDraft("batch-conflict").term, group_id: "group_001" }, changes: { meaning: "current" }, expected_revision: r }), batch: (r: string) => post("jargon/batch", { action: "delete", items: [null, { identity: { term: jargonDraft("batch-conflict").term, group_id: "group_001" }, expected_revision: r }] }), identity: { term: jargonDraft("batch-conflict").term, group_id: "group_001" } },
      { prefix: "affection", create: () => post("affection/users/create", affectionDraft("batch-conflict")), update: (r: string) => post("affection/users/update", { identity: { group_id: "group_001", user_id: affectionDraft("batch-conflict").user_id }, changes: { affection_score: 43 }, expected_revision: r }), batch: (r: string) => post("affection/users/batch", { action: "delete", items: [null, { identity: { group_id: "group_001", user_id: affectionDraft("batch-conflict").user_id }, expected_revision: r }], params: {} }), identity: { group_id: "group_001", user_id: affectionDraft("batch-conflict").user_id } },
    ];
    for (const item of cases) {
      resetIfAvailable();
      const created = entityEnvelope(await item.create());
      const current = entityEnvelope(await item.update(created.revision));
      const result = okData(await item.batch(created.revision));
      expect(result.failures, item.prefix).toEqual([
        expect.objectContaining({ identity: { item_index: 0 }, code: "validation_error", field_errors: expect.any(Object) }),
        expect.objectContaining({ identity: item.identity, code: "edit_conflict", current_entity: current.entity, current_revision: current.revision }),
      ]);
    }
  });
});

describe("strict Python mutation contracts", () => {
  it("rejects unknown and malformed create fields without persisting read-only data", async () => {
    expectValidation(await post("social/create", { ...socialIdentity("strict-create"), strength: 2, tags: [1], frequency: 99 }), { frequency: "字段不可写", strength: "必须在 0.0 到 1.0 之间", tags: "必须为字符串数组" });
    expectValidation(await post("profiles/create", { ...profileDraft("strict-create"), preferences: [], message_count: 99 }), { message_count: "字段不可写", preferences: "必须为对象" });
    expectValidation(await post("jargon/create", { ...jargonDraft("strict-create"), confidence: 2, is_global: "yes", count: 99 }), { count: "字段不可写", confidence: "必须在 0.0 到 1.0 之间", is_global: "必须为布尔值" });
    expectValidation(await post("affection/users/create", { ...affectionDraft("strict-create"), affection_level: "CLOSE" }), { affection_level: "字段不可写" });
  });

  it("rejects invalid top-level identity and revision fields before lookup", async () => {
    const cases = [
      ["social/update", { identity: { ...socialIdentity("strict"), from_user: "x".repeat(129), extra: true }, changes: {}, expected_revision: "" }, { extra: "字段不可写", "identity.from_user": "文本过长", expected_revision: "不能为空" }],
      ["profiles/delete", { identity: { user_id: "x".repeat(129), extra: true }, expected_revision: "r".repeat(257) }, { extra: "字段不可写", "identity.user_id": "文本过长", expected_revision: "文本过长" }],
      ["jargon/update", { identity: { term: "", group_id: "group_001" }, changes: { meaning: "valid" } }, { "identity.term": "不能为空", expected_revision: "不能为空" }],
      ["affection/users/delete", { identity: { group_id: true, user_id: "user" }, expected_revision: 1 }, { "identity.group_id": "必须为字符串", expected_revision: "必须为字符串" }],
    ] as const;
    for (const [route, body, errors] of cases) expectValidation(await post(route, body), errors);
    expectValidation(await post("social/update", { identity: socialIdentity("strict"), changes: {}, expected_revision: "r", extra: true }), { extra: "字段不可写" });
  });

  it("validates batch item identity text and revision bounds with item-index failures", async () => {
    const result = okData(await post("social/batch", { action: "delete", params: {}, items: [
      { identity: { ...socialIdentity("blank"), from_user: "" }, expected_revision: "r" },
      { identity: { ...socialIdentity("long"), to_user: "x".repeat(129) }, expected_revision: "r".repeat(257) },
    ] }));
    expect(result.failures).toEqual([
      expect.objectContaining({ identity: { item_index: 0 }, code: "validation_error", field_errors: expect.objectContaining({ "identity.from_user": "不能为空" }) }),
      expect.objectContaining({ identity: { item_index: 1 }, code: "validation_error", field_errors: expect.objectContaining({ "identity.to_user": "文本过长", expected_revision: "文本过长" }) }),
    ]);
  });

  it("strictly validates profile batch params, legacy caps, and affection params objects", async () => {
    const profile = profileDraft("strict-batch"); const created = entityEnvelope(await post("profiles/create", profile));
    const item = { identity: { user_id: profile.user_id }, expected_revision: created.revision };
    expectValidation(await post("profiles/batch", { action: "tags_add", items: [item], params: [] }), { params: "必须为对象" });
    expectValidation(await post("profiles/batch", { action: "tags_add", items: [item], params: { extra: true } }), { extra: "字段不可写" });
    expectValidation(await post("profiles/batch", { action: "tags_add", items: [item], params: { tag: { category: "invalid", value: "", confidence: 2, extra: true } } }), { "params.tag.extra": "字段不可写", "params.tag.category": "不支持的标签分类", "params.tag.value": "不能为空", "params.tag.confidence": "必须在 0.0 到 1.0 之间" });
    expectValidation(await post("profiles/batch", { action: "delete", user_ids: [] }), { user_ids: "项目数量必须在 1 到 100 之间" });
    expectValidation(await post("profiles/batch", { action: "delete", user_ids: Array.from({ length: 101 }, (_, index) => `u-${index}`) }), { user_ids: "项目数量必须在 1 到 100 之间" });
    expectValidation(await post("affection/users/batch", { action: "delete", items: [item], params: [] }), { params: "必须为对象" });
  });
});

describe("existing mutable editors accept full-form and legacy update requests", () => {
  it("mutates Memory through {changes} and legacy field/value against one state", async () => {
    const id = (okData(await get("memories", { page: "1", page_size: "1" })).items as JsonObject[])[0].id as string;
    expect(okData(await post("memory/update", { memory_id: id, changes: { summary: "full summary", content: "full content", importance: 8 }, reason: "Task 17" }))).toMatchObject({ updated: true });
    expect(okData(await get("memory/detail", { id })).memory).toMatchObject({ id, summary: "full summary", content: "full content", importance: 8 });
    await post("memory/update", { memory_id: id, field: "summary", value: "legacy summary" });
    expect(okData(await get("memory/detail", { id })).memory).toMatchObject({ summary: "legacy summary", content: "full content" });
  });

  it("mutates Knowledge through {changes} and legacy field/value against one state", async () => {
    const id = (okData(await get("knowledge", { limit: "1", offset: "0" })).entries as JsonObject[])[0].entry_id as string;
    await post("knowledge/update", { entry_id: id, changes: { title: "full title", content: "full content", category: "fact", confidence: 0.77 } });
    expect(okData(await get("knowledge/detail", { entry_id: id })).entry).toMatchObject({ entry_id: id, title: "full title", content: "full content", category: "fact", confidence: 0.77 });
    await post("knowledge/update", { entry_id: id, field: "title", value: "legacy title" });
    expect(okData(await get("knowledge/detail", { entry_id: id })).entry).toMatchObject({ title: "legacy title", content: "full content" });
  });

  it("mutates Notes through {changes} and legacy field/value against one state", async () => {
    const id = (okData(await get("notes", {})).notes as JsonObject[])[0].note_id as string;
    await post("notes/update", { note_id: id, changes: { title: "full note", content: "full content", tags: ["one", "two"], status: "active" } });
    expect(okData(await get("notes/detail", { note_id: id })).note).toMatchObject({ note_id: id, title: "full note", content: "full content", tags: ["one", "two"], status: "active" });
    await post("notes/update", { note_id: id, field: "title", value: "legacy note" });
    expect(okData(await get("notes/detail", { note_id: id })).note).toMatchObject({ title: "legacy note", content: "full content", tags: ["one", "two"] });
  });
});
