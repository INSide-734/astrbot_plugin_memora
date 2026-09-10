import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { QualityMonitorTab } from "./QualityMonitorTab";

function ok<T>(data: T) {
  return { status: "ok", data };
}

describe("QualityMonitorTab", () => {
  const timestamp = 1783150200;
  let bridge: {
    apiGet: ReturnType<typeof vi.fn>;
    apiPost: ReturnType<typeof vi.fn>;
    getLocale: ReturnType<typeof vi.fn>;
    getI18n: ReturnType<typeof vi.fn>;
    t: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    bridge = {
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
        if (path === "page/quality/recent") return Promise.resolve(ok({ scores: [{ atom_id: "atom-1", consistency: 0.8, coherence: 0.8, relevance: 0.8, freshness: 0.8, accuracy: 0.8, overall: 0.8 }] }));
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
    render(<QualityMonitorTab showToast={() => undefined} onResetRequested={() => undefined} refreshToken={0} resetPending={false} />);

    expect(await screen.findByText("高")).toBeTruthy();
    expect(screen.getAllByText("准确性").length).toBeGreaterThan(0);
    expect(screen.getByText("custom")).toBeTruthy();
    expect(screen.getByText("custom_dimension")).toBeTruthy();
    expect(screen.getAllByText(new Date(timestamp * 1000).toLocaleString("zh-CN")).length).toBe(2);
    expect(screen.queryByText("high")).toBe(null);
    expect(screen.queryByText("accuracy")).toBe(null);
  });

  it("delegates reset requests and refetches only when the parent refresh token advances", async () => {
    const onResetRequested = vi.fn();
    const { rerender } = render(
      <QualityMonitorTab showToast={() => undefined} onResetRequested={onResetRequested} refreshToken={0} resetPending={false} />,
    );

    expect(await screen.findByText("atom-1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /重置监控|Reset Monitor/i }));

    expect(onResetRequested).toHaveBeenCalledTimes(1);
    expect(bridge.apiPost).not.toHaveBeenCalled();
    expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/quality/stats")).toHaveLength(1);

    rerender(<QualityMonitorTab showToast={() => undefined} onResetRequested={onResetRequested} refreshToken={1} resetPending={false} />);

    await waitFor(() => {
      expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/quality/stats")).toHaveLength(2);
      expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/quality/recent")).toHaveLength(2);
      expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/quality/alerts")).toHaveLength(2);
    });
  });

  it("无评分样本时显示明确空态而不是指标占位符", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/quality/stats") {
        return Promise.resolve(ok({
          status: "no_samples",
          total_scored: 0,
          paused: false,
          pause_reason: "",
          alert_counts: {},
        }));
      }
      if (path === "page/quality/recent") {
        return Promise.resolve(ok({ scores: [], total_scores: 0 }));
      }
      if (path === "page/quality/alerts") {
        return Promise.resolve(ok({ alerts: [], total_alerts: 0 }));
      }
      return Promise.resolve(ok({}));
    });

    render(
      <QualityMonitorTab
        showToast={() => undefined}
        onResetRequested={() => undefined}
        refreshToken={0}
        resetPending={false}
      />,
    );

    expect(await screen.findByText("暂无质量数据")).toBeTruthy();
    expect(screen.queryByText("—")).toBe(null);
  });
});
