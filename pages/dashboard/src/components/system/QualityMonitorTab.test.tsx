import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { QualityMonitorTab } from "./QualityMonitorTab";

function ok<T>(data: T) {
  return { status: "ok", data };
}

describe("QualityMonitorTab", () => {
  const timestamp = 1783150200;

  beforeEach(() => {
    const bridge = {
      apiGet: vi.fn((path: string) => {
        if (path === "page/quality/stats") {
          return Promise.resolve(ok({
            paused: false,
            avg_consistency: 0.8,
            avg_coherence: 0.8,
            avg_relevance: 0.8,
            avg_freshness: 0.8,
            avg_accuracy: 0.8,
            avg_overall: 0.8,
          }));
        }
        if (path === "page/quality/recent") return Promise.resolve(ok({ scores: [] }));
        if (path === "page/quality/alerts") {
          return Promise.resolve(ok({ alerts: [
            { id: 1, level: "high", dimension: "accuracy", message: "message", suggestion: "suggestion", timestamp },
            { id: 2, level: "custom", dimension: "custom_dimension", message: "custom message", suggestion: "custom suggestion", timestamp },
          ] }));
        }
        return Promise.resolve(ok({}));
      }),
      apiPost: vi.fn(),
      getLocale: vi.fn().mockReturnValue("zh-CN"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key === "dashboard.severity.high" ? "高" : key),
    };
    Object.defineProperty(window, "AstrBotPluginPage", { configurable: true, value: bridge });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", { configurable: true, value: undefined });
  });

  it("localizes known alert enums and dates while preserving unknown values", async () => {
    render(<QualityMonitorTab showToast={() => undefined} />);

    expect(await screen.findByText("高")).toBeTruthy();
    expect(screen.getAllByText("准确性").length).toBeGreaterThan(0);
    expect(screen.getByText("custom")).toBeTruthy();
    expect(screen.getByText("custom_dimension")).toBeTruthy();
    expect(screen.getAllByText(new Date(timestamp * 1000).toLocaleString("zh-CN")).length).toBe(2);
    expect(screen.queryByText("high")).toBe(null);
    expect(screen.queryByText("accuracy")).toBe(null);
  });
});
