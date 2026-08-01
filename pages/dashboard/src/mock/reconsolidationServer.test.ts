import { beforeEach, describe, expect, it } from "vitest";

import {
  handleReconsolidationGet,
  handleReconsolidationPost,
  resetReconsolidationMockState,
} from "./reconsolidationServer";

/** 从成功 Mock envelope 中读取对象数据。 */
function dataOf(response: ReturnType<typeof handleReconsolidationGet>) {
  expect(response?.status).toBe("ok");
  return response?.data as Record<string, unknown>;
}

describe("reconsolidation mock server", () => {
  beforeEach(() => {
    resetReconsolidationMockState();
  });

  it("returns real filtered totals and offset pagination with safe list fields", () => {
    const first = dataOf(handleReconsolidationGet("review/reconsolidation", {
      status: "all",
      offset: "0",
      limit: "1",
    }));
    const second = dataOf(handleReconsolidationGet("review/reconsolidation", {
      status: "all",
      offset: "1",
      limit: "1",
    }));

    expect(first.total).toBe(2);
    expect(first.offset).toBe(0);
    expect(first.limit).toBe(1);
    expect(second.total).toBe(2);
    expect(second.offset).toBe(1);
    expect((first.items as Array<Record<string, unknown>>)[0].candidate_id)
      .not.toBe((second.items as Array<Record<string, unknown>>)[0].candidate_id);
    expect(Object.keys((first.items as Array<Record<string, unknown>>)[0]).sort()).toEqual([
      "candidate_id",
      "change_summary",
      "created_at",
      "evidence_type",
      "reason_code",
      "status",
      "updated_at",
    ]);
  });

  it("returns only controlled content comparison and low-sensitivity action history", () => {
    const response = dataOf(handleReconsolidationGet("review/reconsolidation/detail", {
      candidate_id: "recon-smoke-pending",
    }));
    const candidate = response.candidate as Record<string, unknown>;
    const actions = response.actions as Array<Record<string, unknown>>;

    expect(candidate.old_content).toBeTruthy();
    expect(candidate.proposed_content).toBeTruthy();
    expect(candidate.memory_id).toBeUndefined();
    expect(candidate.source_revision).toBeUndefined();
    expect(Object.keys(actions[0]).sort()).toEqual(["action", "created_at", "reason_code"]);
  });

  it("applies valid status transitions, rejects stale actions, and resets deterministically", () => {
    const approved = handleReconsolidationPost("review/reconsolidation/action", {
      candidate_id: "recon-smoke-pending",
      action: "approve",
    });
    expect(dataOf(approved).status).toBe("approved");

    const conflict = handleReconsolidationPost("review/reconsolidation/action", {
      candidate_id: "recon-smoke-pending",
      action: "reject",
    });
    expect(conflict).toMatchObject({
      status: "error",
      code: "reconsolidation_review_conflict",
    });

    const rolledBack = handleReconsolidationPost("review/reconsolidation/action", {
      candidate_id: "recon-smoke-approved",
      action: "rollback",
    });
    expect(dataOf(rolledBack).status).toBe("rolled_back");

    resetReconsolidationMockState();
    const pending = dataOf(handleReconsolidationGet("review/reconsolidation/detail", {
      candidate_id: "recon-smoke-pending",
    }));
    expect((pending.candidate as Record<string, unknown>).status).toBe("pending");
  });

  it("rejects unknown filters and action fields like the production API", () => {
    expect(handleReconsolidationGet("review/reconsolidation", {
      status: "unknown",
    })).toMatchObject({ status: "error", code: "invalid_request" });
    expect(handleReconsolidationPost("review/reconsolidation/action", {
      candidate_id: "recon-smoke-pending",
      action: "approve",
      unexpected: true,
    })).toMatchObject({ status: "error", code: "invalid_request" });
  });
});
