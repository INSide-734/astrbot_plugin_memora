import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";

import { EVALUATION_EFFECT_GATE } from "@/mock/data";

import { EvaluationWorkbench } from "./EvaluationWorkbench";

interface BridgeMock {
  apiGet: Mock;
  apiPost: Mock;
  getLocale: Mock;
  getI18n: Mock;
  t: Mock;
}

function ok<T>(data: T) {
  return { status: "ok", data };
}

const COPY: Record<string, string> = {
  "dashboard.intelligence.evaluation.run": "运行评测",
  "dashboard.intelligence.evaluation.running": "运行中",
  "dashboard.intelligence.evaluation.effectGate.runGate": "运行效果门",
  "dashboard.intelligence.evaluation.effectGate.runGateHint": "固定绑定",
  "dashboard.intelligence.evaluation.effectGate.title": "效果门",
};

describe("EvaluationWorkbench effect gate", () => {
  let bridge: BridgeMock;

  beforeEach(() => {
    bridge = {
      apiGet: vi.fn((path: string) => {
        if (path === "page/evaluation/datasets") {
          return Promise.resolve(ok({
            datasets: [{
              name: "private_basic",
              case_count: 10,
              path: "private_basic.jsonl",
              intents: ["preference"],
              chat_types: ["private"],
            }],
          }));
        }
        if (path === "page/evaluation/reports") {
          return Promise.resolve(ok({ reports: [] }));
        }
        return Promise.resolve(ok({}));
      }),
      apiPost: vi.fn(),
      getLocale: vi.fn().mockReturnValue("zh-CN"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => COPY[key] ?? key),
    };
    bridge.apiPost.mockImplementation((_path: string, body: Record<string, unknown>) => {
      if (body.effect_gate !== undefined) {
        return Promise.resolve(ok({
          report_id: "eval-gate",
          created_at: Date.now() / 1000,
          baseline: "fixed_route_baseline",
          datasets: ["private_basic"],
          summary: { total_cases: 1, k: 5, recall_at_k: 1 },
          variants: {},
          cases: [],
          effect_gate: EVALUATION_EFFECT_GATE,
          manifest: EVALUATION_EFFECT_GATE.manifest,
        }));
      }
      return Promise.resolve(ok({
        report_id: "eval-normal",
        created_at: Date.now() / 1000,
        baseline: "baseline",
        datasets: ["private_basic"],
        summary: { total_cases: 1, k: 5, recall_at_k: 1 },
        variants: {},
        cases: [],
      }));
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

  it("sends only server-bound fields for the gate and renders the gate card", async () => {
    const { container } = render(<EvaluationWorkbench showToast={() => undefined} />);
    await screen.findByText("private_basic");

    fireEvent.click(screen.getByRole("button", { name: "运行效果门" }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/evaluation/run", {
        datasets: ["private_basic"],
        k: 5,
        save_report: true,
        effect_gate: {},
      });
    });
    expect(await screen.findByText("效果门")).toBeTruthy();
    expect(container.querySelector("[data-effect-gate-card]")).toBeTruthy();
    const payload = bridge.apiPost.mock.calls[0][1] as Record<string, unknown>;
    expect(payload).not.toHaveProperty("variants");
    expect(payload).not.toHaveProperty("baseline");
    expect(payload).not.toHaveProperty("quality_loop");
  });

  it("leaves the normal run payload unchanged and gate-free", async () => {
    render(<EvaluationWorkbench showToast={() => undefined} />);
    await screen.findByText("private_basic");

    fireEvent.click(screen.getByRole("button", { name: "运行评测" }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/evaluation/run", {
        datasets: ["private_basic"],
        k: 5,
        variants: ["baseline", "graph_expansion_off", "topic_expansion_off"],
        baseline: "baseline",
        save_report: true,
        quality_loop: {},
        quality_loop_seed: 42,
      });
    });
    const payload = bridge.apiPost.mock.calls[0][1] as Record<string, unknown>;
    expect(payload).not.toHaveProperty("effect_gate");
  });
});
