import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { EVALUATION_DATASETS, EVALUATION_REPORTS, EVALUATION_VARIANTS } from "./data";
import {
  handleEvaluationDatasetImport,
  handleEvaluationDatasets,
  handleEvaluationReportDetail,
  handleEvaluationReports,
  handleEvaluationRun,
} from "./evaluationServer";

/** 从成功 Mock envelope 中读取对象数据。 */
function okData(response: { status: string; data?: unknown }): Record<string, unknown> {
  expect(response.status).toBe("ok");
  return response.data as Record<string, unknown>;
}

describe("evaluation mock server", () => {
  let reports: typeof EVALUATION_REPORTS;
  let datasets: typeof EVALUATION_DATASETS;

  beforeEach(() => {
    reports = structuredClone(EVALUATION_REPORTS);
    datasets = structuredClone(EVALUATION_DATASETS);
  });

  afterEach(() => {
    EVALUATION_REPORTS.splice(0, EVALUATION_REPORTS.length, ...reports);
    EVALUATION_DATASETS.splice(0, EVALUATION_DATASETS.length, ...datasets);
  });

  it("imports a production dataset into the local catalog", () => {
    const imported = okData(handleEvaluationDatasetImport({
      filename: "actual-memory.jsonl",
      content: '{"case_id":"coffee"}\n',
    }));
    const listed = okData(handleEvaluationDatasets());

    expect(imported.dataset).toMatchObject({
      name: "actual-memory",
      case_count: 1,
      replaced: false,
    });
    expect((listed.datasets as Array<{ name: string }>).some((item) => item.name === "actual-memory")).toBe(true);
  });

  it("returns capability descriptors with datasets", () => {
    const data = okData(handleEvaluationDatasets());

    expect(data.variants).toEqual(EVALUATION_VARIANTS);
    expect(EVALUATION_VARIANTS.some((variant) => !variant.available)).toBe(true);
    expect(EVALUATION_VARIANTS.filter((variant) => variant.default_selected && variant.available).map((variant) => variant.name))
      .toEqual(["baseline", "graph_expansion_off", "topic_expansion_off"]);
  });

  it("lists, opens, and creates reports with stable capability status", () => {
    expect((okData(handleEvaluationReports({ limit: "1" })).reports as unknown[]).length).toBe(1);
    expect(okData(handleEvaluationReportDetail({ report_id: EVALUATION_REPORTS[0].report_id })).report).toBeTruthy();

    const report = okData(handleEvaluationRun({
      datasets: ["private_basic"],
      variants: ["baseline", "final_reranker_off", "final_reranker_mmr"],
      baseline: "baseline",
      k: 7,
    }));
    expect(report).toMatchObject({ datasets: ["private_basic"], baseline: "baseline" });
    expect((report.summary as Record<string, unknown>).k).toBe(7);
    expect(Object.keys(report.variants as Record<string, unknown>)).toEqual([
      "baseline",
      "final_reranker_off",
      "final_reranker_mmr",
    ]);
    expect((report.variants as Record<string, Record<string, unknown>>).final_reranker_off)
      .toMatchObject({ status: "completed", capability_status: "available" });
    expect((report.variants as Record<string, Record<string, unknown>>).final_reranker_mmr)
      .toMatchObject({ status: "skipped", reason_code: "equivalent_to_baseline" });
  });
});
