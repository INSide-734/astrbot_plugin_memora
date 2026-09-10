import { describe, expect, it } from "vitest";

import { RECALL_TRACE_SAMPLE } from "./data";
import { createSafeRecallTraceResponse } from "./recallTrace";

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
