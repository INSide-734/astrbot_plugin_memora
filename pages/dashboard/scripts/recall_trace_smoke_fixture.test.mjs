import { describe, expect, it } from "vitest";

import { recallTracePayload } from "./recall_trace_smoke_fixture.mjs";

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
  it("只返回 smoke 所需的安全 trace 标量", () => {
    const payload = recallTracePayload("trace-safe");
    const keys = collectKeys(payload);

    expect(payload.trace_id).toBe("trace-safe");
    expect(payload.results[0].rank).toBe(1);
    expect(keys.filter((key) => FORBIDDEN_KEYS.has(key))).toEqual([]);
  });
});
