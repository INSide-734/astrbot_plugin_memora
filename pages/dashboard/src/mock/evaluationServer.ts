import type { EvaluationReport } from "@/types/intelligence";

import { EVALUATION_DATASETS, EVALUATION_REPORTS, EVALUATION_VARIANTS } from "./data";

export type EvaluationMockResponse = {
  status: string;
  data?: unknown;
  message?: string;
};

/** 构造成功的评测 Mock envelope。 */
function ok(data: unknown): EvaluationMockResponse {
  return { status: "ok", data };
}

/** 返回数据集和当前后端能力描述符。 */
export function handleEvaluationDatasets(): EvaluationMockResponse {
  return ok({ datasets: EVALUATION_DATASETS, variants: EVALUATION_VARIANTS });
}

/** 校验并导入一份本地开发用生产数据集摘要。 */
export function handleEvaluationDatasetImport(body: Record<string, unknown>): EvaluationMockResponse {
  const filename = typeof body.filename === "string" ? body.filename.trim() : "";
  const content = typeof body.content === "string" ? body.content.trim() : "";
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.jsonl$/.test(filename) || !content) {
    return { status: "error", message: "评测数据集无效" };
  }
  const name = filename.slice(0, -".jsonl".length);
  const caseCount = content.split(/\r?\n/).filter((line) => line.trim() && !line.trim().startsWith("#")).length;
  const nextDataset = {
    name,
    case_count: caseCount,
    path: filename,
    intents: [],
    chat_types: [],
  };
  const existingIndex = EVALUATION_DATASETS.findIndex((item) => item.name === name);
  const replaced = existingIndex >= 0;
  if (replaced) EVALUATION_DATASETS.splice(existingIndex, 1, nextDataset);
  else EVALUATION_DATASETS.push(nextDataset);
  return ok({
    dataset: {
      name,
      filename,
      case_count: caseCount,
      replaced,
    },
  });
}

/** 按请求上限返回最近的评测报告。 */
export function handleEvaluationReports(params: Record<string, string>): EvaluationMockResponse {
  const limit = Math.min(50, Math.max(1, parseInt(params.limit ?? "10", 10)));
  return ok({ reports: EVALUATION_REPORTS.slice(0, limit) });
}

/** 按稳定报告 ID 返回完整评测报告。 */
export function handleEvaluationReportDetail(params: Record<string, string>): EvaluationMockResponse {
  const reportId = params.report_id ?? params.id;
  const report = EVALUATION_REPORTS.find((item) => item.report_id === reportId);
  return report ? ok({ report }) : { status: "error", message: "未找到评测报告" };
}

/** 根据请求选择生成确定性的本地评测报告。 */
export function handleEvaluationRun(body: Record<string, unknown>): EvaluationMockResponse {
  const template = EVALUATION_REPORTS[0];
  const datasets = Array.isArray(body.datasets) ? body.datasets.map(String) : ["private_basic"];
  const variants = Array.isArray(body.variants) ? body.variants.map(String) : ["baseline"];
  const k = Math.min(20, Math.max(1, Number(body.k ?? template.summary.k)));
  const report: EvaluationReport = {
    ...template,
    report_id: `eval-${Date.now()}`,
    created_at: Date.now() / 1000,
    baseline: String(body.baseline ?? "baseline"),
    datasets,
    summary: { ...template.summary, k },
    variants: Object.fromEntries(variants.map((name) => {
      const descriptor = EVALUATION_VARIANTS.find((item) => item.name === name);
      if (descriptor && !descriptor.available) {
        return [name, {
          name,
          status: "skipped",
          capability_status: "unavailable",
          reason_code: descriptor.reason_code,
          effective_settings: {},
        }];
      }
      return [name, template.variants[name] ?? {
        name,
        status: "completed",
        capability_status: "available",
        reason_code: "available",
        effective_settings: {},
      }];
    })),
    deltas: Object.fromEntries(
      Object.entries(template.deltas ?? {}).filter(([name]) => variants.includes(name)),
    ),
    cases: (template.cases ?? []).map((item) => ({ ...item })),
  };
  EVALUATION_REPORTS.unshift(report);
  return ok(report);
}
