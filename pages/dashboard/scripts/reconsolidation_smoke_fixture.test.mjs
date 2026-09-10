import assert from "node:assert/strict";

const NODE_TEST_SPECIFIER = "node:test";
const { describe, it } = process.env.VITEST
  ? await import("vitest")
  : await import(/* @vite-ignore */ NODE_TEST_SPECIFIER);
import { reconsolidationSmokePayload } from "./reconsolidation_smoke_fixture.mjs";

describe("reconsolidation smoke fixture", () => {
  it("returns real totals and safe paginated summaries", () => {
    const first = reconsolidationSmokePayload("GET", "review/reconsolidation", {
      status: "all",
      offset: 0,
      limit: 1,
    });
    const second = reconsolidationSmokePayload("GET", "review/reconsolidation", {
      status: "all",
      offset: 1,
      limit: 1,
    });

    assert.equal(first.total, 2);
    assert.equal(first.offset, 0);
    assert.equal(first.limit, 1);
    assert.notEqual(first.items[0].candidate_id, second.items[0].candidate_id);
    assert.deepEqual(Object.keys(first.items[0]).sort(), [
      "candidate_id",
      "change_summary",
      "created_at",
      "evidence_type",
      "reason_code",
      "status",
      "updated_at",
    ]);
  });

  it("adds controlled content only on detail responses", () => {
    const detail = reconsolidationSmokePayload("GET", "review/reconsolidation/detail", {
      candidate_id: "recon-smoke-pending",
    });

    assert.equal(detail.candidate.old_content, "用户周末通常在家工作。");
    assert.equal(detail.candidate.proposed_content, "用户近期更喜欢周末在安静的咖啡馆工作。");
    assert.equal(detail.candidate.memory_id, undefined);
    assert.deepEqual(Object.keys(detail.actions[0]).sort(), ["action", "created_at", "reason_code"]);
  });
});
