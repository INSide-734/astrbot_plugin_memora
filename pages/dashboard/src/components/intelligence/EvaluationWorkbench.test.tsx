import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EvaluationWorkbench } from "./EvaluationWorkbench";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale: ReturnType<typeof vi.fn>;
  getI18n: ReturnType<typeof vi.fn>;
  t: ReturnType<typeof vi.fn>;
}

function ok<T>(data: T) {
  return { status: "ok", data };
}

describe("EvaluationWorkbench", () => {
  let bridge: BridgeMock;

  beforeEach(() => {
    bridge = {
      apiGet: vi.fn(),
      apiPost: vi.fn(),
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
    };

    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/evaluation/datasets") {
        return Promise.resolve(ok({
          datasets: [
            {
              name: "private_basic",
              case_count: 10,
              path: "tests/fixtures/retrieval/private_basic.jsonl",
              intents: ["preference"],
              chat_types: ["private"],
            },
          ],
        }));
      }
      if (path === "page/evaluation/reports") {
        return Promise.resolve(ok({ reports: [] }));
      }
      return Promise.resolve(ok({}));
    });

    bridge.apiPost.mockImplementation((path: string, body: unknown) => {
      if (path === "page/evaluation/run") {
        expect(body).toEqual({
          datasets: ["private_basic"],
          k: 5,
          variants: ["baseline", "graph_expansion_off", "topic_expansion_off"],
          baseline: "baseline",
          save_report: true,
        });
        return Promise.resolve(ok({
          report_id: "eval-test",
          created_at: 1783150200.0,
          baseline: "baseline",
          datasets: ["private_basic"],
          summary: {
            total_cases: 20,
            k: 5,
            recall_at_k: 0.9,
            mrr: 0.74,
            ndcg_at_k: 0.78,
            p95_latency_ms: 42.6,
          },
          variants: {
            baseline: {
              name: "baseline",
              status: "completed",
              summary: {
                total_cases: 20,
                k: 5,
                recall_at_k: 0.9,
                mrr: 0.74,
                ndcg_at_k: 0.78,
                p95_latency_ms: 42.6,
              },
            },
            graph_expansion_off: {
              name: "graph_expansion_off",
              status: "completed",
              summary: {
                total_cases: 20,
                k: 5,
                recall_at_k: 0.85,
                mrr: 0.72,
                ndcg_at_k: 0.75,
                p95_latency_ms: 34.2,
              },
            },
          },
          deltas: {
            graph_expansion_off: {
              recall_at_k: -0.05,
              mrr: -0.02,
              ndcg_at_k: -0.03,
              p95_latency_ms: -8.4,
            },
          },
          cases: [
            {
              case_id: "coffee",
              query: "用户喜欢喝什么咖啡",
              ranked_doc_ids: ["mem-coffee"],
              recall_at_k: 1.0,
              reciprocal_rank: 1.0,
              ndcg_at_k: 1.0,
              latency_ms: 12.5,
            },
            {
              case_id: "missed",
              query: "用户周末在哪里工作",
              ranked_doc_ids: ["mem-other"],
              recall_at_k: 0,
              reciprocal_rank: 0,
              ndcg_at_k: 0,
              latency_ms: 18.2,
            },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: bridge,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  it("runs evaluation and renders summary metrics", async () => {
    render(<EvaluationWorkbench showToast={() => undefined} />);

    await screen.findByText("private_basic");
    fireEvent.click(screen.getByRole("button", { name: /Run|运行/ }));

    expect(await screen.findByText(/Recall@K/)).toBeTruthy();
    expect(screen.getByText(/MRR/)).toBeTruthy();
    expect(screen.getByText(/nDCG/)).toBeTruthy();
    expect(screen.getByText("graph_expansion_off")).toBeTruthy();
    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/evaluation/run", {
        datasets: ["private_basic"],
        k: 5,
        variants: ["baseline", "graph_expansion_off", "topic_expansion_off"],
        baseline: "baseline",
        save_report: true,
      });
    });
  });

  it("renders fixed evaluation workbench chrome from dashboard i18n", async () => {
    bridge.getLocale.mockReturnValue("zh-CN");

    render(<EvaluationWorkbench showToast={() => undefined} />);

    expect(await screen.findByText("private_basic")).toBeTruthy();
    expect(screen.getByText("评测工作台")).toBeTruthy();
    expect(screen.getByText("数据集")).toBeTruthy();
    expect(screen.getByText("变体")).toBeTruthy();
    expect(screen.getByRole("button", { name: /运行/ })).toBeTruthy();
    expect(screen.getByText("报告历史")).toBeTruthy();
    expect(screen.getByText("暂无保存的报告")).toBeTruthy();
    expect(screen.queryByText("Evaluation Workbench")).toBe(null);
  });

  it("loads full report detail when opening history", async () => {
    bridge.apiGet.mockImplementation((path: string, params?: Record<string, string>) => {
      if (path === "page/evaluation/datasets") {
        return Promise.resolve(ok({
          datasets: [
            {
              name: "private_basic",
              case_count: 10,
              path: "tests/fixtures/retrieval/private_basic.jsonl",
              intents: ["preference"],
              chat_types: ["private"],
            },
          ],
        }));
      }
      if (path === "page/evaluation/reports") {
        return Promise.resolve(ok({
          reports: [
            {
              report_id: "saved-report",
              created_at: 1783150200.0,
              baseline: "baseline",
              datasets: ["private_basic"],
              summary: {
                total_cases: 20,
                k: 5,
                recall_at_k: 0.9,
                mrr: 0.74,
                ndcg_at_k: 0.78,
                p95_latency_ms: 42.6,
              },
              variants: {
                baseline: { name: "baseline", status: "completed" },
              },
              case_count: 2,
            },
          ],
        }));
      }
      if (path === "page/evaluation/reports/detail") {
        expect(params).toEqual({ report_id: "saved-report" });
        return Promise.resolve(ok({
          report: {
            report_id: "saved-report",
            created_at: 1783150200.0,
            baseline: "baseline",
            datasets: ["private_basic"],
            summary: {
              total_cases: 20,
              k: 5,
              recall_at_k: 0.9,
              mrr: 0.74,
              ndcg_at_k: 0.78,
              p95_latency_ms: 42.6,
            },
            variants: {
              baseline: { name: "baseline", status: "completed" },
              graph_expansion_off: { name: "graph_expansion_off", status: "completed" },
            },
            deltas: {
              graph_expansion_off: {
                recall_at_k: -0.05,
                mrr: -0.02,
                ndcg_at_k: -0.03,
                p95_latency_ms: -8.4,
              },
            },
            cases: [
              {
                case_id: "missed",
                query: "用户周末在哪里工作",
                ranked_doc_ids: ["mem-other"],
                recall_at_k: 0,
                reciprocal_rank: 0,
                ndcg_at_k: 0,
                latency_ms: 18.2,
              },
            ],
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<EvaluationWorkbench showToast={() => undefined} />);

    fireEvent.click(await screen.findByText("saved-report"));

    expect(await screen.findByText("graph_expansion_off")).toBeTruthy();
    expect(screen.getByText("missed")).toBeTruthy();
    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/evaluation/reports/detail", {
        report_id: "saved-report",
      });
    });
  });
});
