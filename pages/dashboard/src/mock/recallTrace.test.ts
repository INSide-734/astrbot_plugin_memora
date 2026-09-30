import { describe, expect, it } from "vitest";

import { INJECTION_DECISIONS, RECALL_TRACE_SAMPLE } from "./data";
import { createSafeRecallTraceResponse, findRecallTraceDetail } from "./recallTrace";

const SENTINEL = "PRIVATE_SENTINEL_NEVER_EXPOSE";

describe("createSafeRecallTraceResponse", () => {
  it("不把搜索参数和身份复制到 mock 响应", () => {
    const response = createSafeRecallTraceResponse(RECALL_TRACE_SAMPLE, {
      query: SENTINEL,
      session_id: SENTINEL,
      user_id: SENTINEL,
      k: 1,
    });

    expect(JSON.stringify(response)).not.toContain(SENTINEL);
    expect(response.results).toHaveLength(1);
    expect(response.metadata).toEqual({ debug_trace_available: true });
  });
});

describe("findRecallTraceDetail", () => {
  it("决策关联码返回同码生产快照，预览样本不带生产标记", () => {
    const linked = INJECTION_DECISIONS.find((row) => row.trace_id)?.trace_id;
    expect(linked).toBeTruthy();

    const production = findRecallTraceDetail(linked as string);
    const preview = findRecallTraceDetail(RECALL_TRACE_SAMPLE.trace_id);

    expect(production?.trace_id).toBe(linked);
    expect(production?.metadata.trace_kind).toBe("production");
    expect(production?.injection?.injected_count).toBeLessThanOrEqual(
      production?.injection?.selected_count ?? 0,
    );
    expect(production?.results[0]).toMatchObject({ rank: 1, metadata: { memory_type: "episodic", status: "active" } });
    expect(production?.fact_alignment).toEqual({ aligned: 2, misaligned: 1, undeterminable: 3 });
    expect(production?.source_status).toBe("not_assessed");
    expect(production?.filter_summary?.map(({ reason }) => reason)).toEqual([
      "privacy",
      "mark_write",
      "stale",
    ]);
    expect(production?.stages).toContainEqual(expect.objectContaining({ name: "request", status: "skipped" }));
    expect(preview?.injection).toBeUndefined();
  });

  it("未知关联码返回 null，而不是伪造空 trace", () => {
    expect(findRecallTraceDetail("trace-unknown")).toBeNull();
  });
});
