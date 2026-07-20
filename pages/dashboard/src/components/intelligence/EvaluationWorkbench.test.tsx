import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EvaluationWorkbench } from "./EvaluationWorkbench";
import { RU_MAP } from "@/mock";

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
              capability_status: "available",
              reason_code: "available",
              effective_settings: { chain_graph_expansion_enabled: false },
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
              p95_latency_ms: null,
            },
          },
          cases: [
            {
              case_id: "coffee",
              recall_at_k: 1.0,
              reciprocal_rank: 1.0,
              ndcg_at_k: 1.0,
              latency_ms: 12.5,
            },
            {
              case_id: "missed",
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
    expect(screen.getAllByText("Graph off").length).toBeGreaterThan(0);
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

  it("synchronizes checkbox labels without false presence or duplicate toggles", async () => {
    render(<EvaluationWorkbench showToast={() => undefined} />);

    const datasetCheckbox = await screen.findByRole("checkbox", {
      name: /private_basic/,
    });
    const datasetSurface = datasetCheckbox.closest("label");
    expect(datasetCheckbox.getAttribute("data-slot")).toBe("checkbox");
    expect(datasetSurface?.getAttribute("data-selected")).toBe("true");
    expect(datasetSurface?.className).toContain(
      "shadow-[inset_0_0_0_1px_var(--selection-border)]",
    );

    fireEvent.click(screen.getByText("private_basic"));
    expect(datasetCheckbox.getAttribute("aria-checked")).toBe("false");
    expect(datasetSurface?.hasAttribute("data-selected")).toBe(false);

    fireEvent.click(screen.getByText("private_basic"));
    expect(datasetCheckbox.getAttribute("aria-checked")).toBe("true");
    expect(datasetSurface?.getAttribute("data-selected")).toBe("true");

    const graphVariant = screen.getByRole("checkbox", { name: "Graph off" });
    const graphSurface = graphVariant.closest("label");
    expect(graphVariant.getAttribute("data-slot")).toBe("checkbox");
    expect(graphSurface?.getAttribute("data-selected")).toBe("true");

    fireEvent.click(screen.getByText("Graph off"));

    expect(graphVariant.getAttribute("aria-checked")).toBe("false");
    expect(graphSurface?.hasAttribute("data-selected")).toBe(false);
    expect(screen.getByRole("checkbox", { name: "Baseline" })).toBeTruthy();
  });

  it("keeps the final variant selected and preserves it in the evaluation payload", async () => {
    bridge.apiPost.mockResolvedValueOnce({ status: "error", message: "stop after payload capture" });
    render(<EvaluationWorkbench showToast={() => undefined} />);

    await screen.findByText("private_basic");
    fireEvent.click(screen.getByText("Graph off"));
    fireEvent.click(screen.getByText("Topic off"));

    const baseline = screen.getByRole("checkbox", { name: "Baseline" });
    const baselineSurface = baseline.closest("label");
    expect(baseline.getAttribute("aria-checked")).toBe("true");
    expect(baselineSurface?.getAttribute("data-selected")).toBe("true");

    fireEvent.click(screen.getByText("Baseline"));

    expect(baseline.getAttribute("aria-checked")).toBe("true");
    expect(baselineSurface?.getAttribute("data-selected")).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: /^run$/i }));
    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/evaluation/run", {
        datasets: ["private_basic"],
        k: 5,
        variants: ["baseline"],
        baseline: "baseline",
        save_report: true,
      });
    });
  });

  it("uses backend variant descriptors and excludes unavailable capabilities", async () => {
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
          variants: [
            { name: "baseline", available: true, reason_code: "available", default_selected: true },
            { name: "final_reranker_off", available: true, reason_code: "available", default_selected: false },
            {
              name: "final_reranker_embedding_similarity",
              available: false,
              reason_code: "missing_document_vector_access",
              default_selected: true,
            },
          ],
        }));
      }
      if (path === "page/evaluation/reports") {
        return Promise.resolve(ok({ reports: [] }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValueOnce({ status: "error", message: "stop after payload capture" });

    render(<EvaluationWorkbench showToast={() => undefined} />);

    await screen.findByText("Final reranker off");
    expect(screen.queryByText("Graph off")).toBe(null);
    expect(screen.getByRole("checkbox", { name: "Baseline" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("checkbox", { name: "Final reranker off" }).getAttribute("aria-checked")).toBe("false");
    expect(screen.getByLabelText("1 selected").textContent).toBe("1 / 2");

    const unavailable = screen.getByRole("checkbox", { name: /Embedding similarity/ });
    expect(unavailable.hasAttribute("data-disabled")).toBe(true);
    expect(unavailable.closest("label")?.getAttribute("aria-disabled")).toBe("true");
    expect(unavailable.closest("label")?.getAttribute("data-variant-card")).toBe(
      "final_reranker_embedding_similarity",
    );
    expect(unavailable.getAttribute("aria-checked")).toBe("false");
    expect(screen.getByText("Document vectors unavailable")).toBeTruthy();

    fireEvent.click(screen.getByText("Final reranker off"));
    expect(screen.getByLabelText("2 selected").textContent).toBe("2 / 2");
    fireEvent.click(screen.getByRole("button", { name: /^run$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/evaluation/run", {
        datasets: ["private_basic"],
        k: 5,
        variants: ["baseline", "final_reranker_off"],
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
    expect(bridge.apiGet).toHaveBeenCalledTimes(2);

    bridge.getLocale.mockReturnValue("ru-RU");
    await act(async () => {
      window.dispatchEvent(new Event("languagechange"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText(RU_MAP["intelligence.evaluation.datasets"])).toBeTruthy();
    expect(bridge.apiGet).toHaveBeenCalledTimes(2);
  });

  it("localizes evaluation metrics, chat types, variant names, and unavailable deltas", async () => {
    bridge.getLocale.mockReturnValue("zh-CN");
    bridge.t.mockImplementation((key: string) => ({
      "dashboard.intelligence.evaluation.metric.cases": "用例数",
      "dashboard.common.notAvailableShort": "不适用",
    })[key] ?? key);

    render(<EvaluationWorkbench showToast={() => undefined} />);

    expect(await screen.findByText("private_basic")).toBeTruthy();
    expect(screen.getByText("私聊")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /运行/ }));

    expect(await screen.findByText("用例数")).toBeTruthy();
    expect(screen.getByText("变体执行状态")).toBeTruthy();
    expect(screen.getByText("chain_graph_expansion_enabled=false")).toBeTruthy();
    expect(screen.getAllByText("关闭图扩展").length).toBeGreaterThan(0);
    expect(screen.getByText("不适用")).toBeTruthy();
    expect(screen.queryByText("Cases")).toBe(null);
    expect(screen.queryByText("private")).toBe(null);
    expect(screen.queryByText("graph_expansion_off")).toBe(null);
    expect(screen.queryByText("n/a")).toBe(null);
  });

  it("loads full report detail when opening history", async () => {
    const localeSpy = vi.spyOn(Date.prototype, "toLocaleString");
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

    expect((await screen.findAllByText("Graph off")).length).toBeGreaterThan(0);
    expect(screen.getByText("Variant execution status")).toBeTruthy();
    expect(screen.getByText("missed")).toBeTruthy();
    expect(localeSpy).toHaveBeenCalledWith("en-US");
    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/evaluation/reports/detail", {
        report_id: "saved-report",
      });
    });
  });

  it("guards same-tick evaluation runs and preserves selections after failure", async () => {
    let resolveRun!: (value: { status: "error"; message: string }) => void;
    bridge.apiPost.mockReturnValue(new Promise((resolve) => { resolveRun = resolve; }));
    render(<EvaluationWorkbench showToast={() => undefined} />);

    await screen.findByText("private_basic");
    const run = screen.getByRole("button", { name: /^run$/i });
    fireEvent.click(run);
    fireEvent.click(run);

    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
    expect(run).toHaveProperty("disabled", true);
    expect(run.textContent?.toLowerCase()).toContain("running");

    await act(async () => { resolveRun({ status: "error", message: "evaluation unavailable" }); });
    expect(screen.getByRole("alert").textContent).toContain("evaluation unavailable");
    expect(screen.getByRole("checkbox", { name: /private_basic/ }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("checkbox", { name: "Graph off" }).getAttribute("aria-checked")).toBe("true");
  });
});
