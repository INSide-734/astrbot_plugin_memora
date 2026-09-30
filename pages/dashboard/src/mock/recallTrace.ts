import type { RecallTraceResponse } from "@/types/intelligence";

import {
  INJECTION_DECISIONS,
  RECALL_TRACE_PRODUCTION_SAMPLE,
  RECALL_TRACE_SAMPLE,
} from "./data";

/** 把任意 k 输入钳制到后端允许的 1～20。 */
function clampTraceK(value: unknown): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 5;
  return Math.min(20, Math.max(1, Math.round(parsed)));
}
/**
 * 从安全样本构造一次 mock trace 响应。
 *
 * 请求 query、身份和 scope 只用于模拟搜索输入，绝不复制到响应 DTO。
 */
export function createSafeRecallTraceResponse(
  sample: RecallTraceResponse,
  body: Record<string, unknown>,
): RecallTraceResponse {
  const k = clampTraceK(body.k);
  return {
    ...sample,
    trace_id: `trace-mock-${Date.now()}`,
    results: sample.results.slice(0, k),
    created_at: Date.now() / 1000,
    metadata: { ...sample.metadata },
  };
}

/**
 * 按观测关联码查找 mock trace 详情。
 *
 * 决策关联码返回同请求生产快照样本，手动预览样本码返回预览样本；其余返回
 * `null`，由路由映射为稳定 `trace_unavailable`，不伪造空 trace。
 */
export function findRecallTraceDetail(traceId: string): RecallTraceResponse | null {
  if (INJECTION_DECISIONS.some((row) => row.trace_id === traceId)) {
    return structuredClone({ ...RECALL_TRACE_PRODUCTION_SAMPLE, trace_id: traceId });
  }
  return traceId === RECALL_TRACE_SAMPLE.trace_id ? structuredClone(RECALL_TRACE_SAMPLE) : null;
}
