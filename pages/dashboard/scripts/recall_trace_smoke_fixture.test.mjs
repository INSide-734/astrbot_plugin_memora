import { describe, expect, it } from "vitest";

import { recallTracePayload, recallTracePreviewPayload } from "./recall_trace_smoke_fixture.mjs";

const FORBIDDEN_KEYS = new Set([
  "query",
  "prompt",
  "content",
  "content_preview",
  "doc_id",
  "memory_id",
  "session_id",
  "user_id",
  "source_mapping",
  "revision",
  "scope",
  "privacy",
  "role",
  "job_id",
  "explanation",
]);

/** 递归收集对象键，验证任意嵌套层都不绕过安全 DTO。 */
function collectKeys(value) {
  if (Array.isArray(value)) {
    return value.flatMap((item) => collectKeys(item));
  }
  if (!value || typeof value !== "object") return [];
  return Object.entries(value).flatMap(([key, item]) => [key, ...collectKeys(item)]);
}

describe("recallTracePayload", () => {
  it("returns the production trace contract without unsafe fields", () => {
    const payload = recallTracePayload("trace-safe");
    const keys = collectKeys(payload);

    expect(payload.trace_id).toBe("trace-safe");
    expect(payload.metadata.trace_kind).toBe("production");
    expect(payload.injection.injected_count).toBe(2);
    expect(payload.fact_alignment).toEqual({ aligned: 1, misaligned: 0, undeterminable: 0 });
    expect(payload.source_status).toBe("not_assessed");
    expect(payload.filter_summary).toEqual([
      { stage: "retrieval", reason: "privacy", count: 1 },
      { stage: "query", reason: "mark_write", count: 1 },
      { stage: "recall", reason: "stale", count: 1 },
    ]);
    expect(payload.results[0].rank).toBe(1);
    expect(keys.filter((key) => FORBIDDEN_KEYS.has(key))).toEqual([]);
  });
});

describe("recallTracePreviewPayload", () => {
  it("keeps the manual trace preview separate from production decision details", () => {
    const preview = recallTracePreviewPayload("trace-preview");

    expect(preview.trace_id).toBe("trace-preview");
    expect(preview.metadata.trace_kind).toBeUndefined();
    expect(preview.injection).toBeUndefined();
    expect(preview.results[0].rank).toBe(1);
  });
});
