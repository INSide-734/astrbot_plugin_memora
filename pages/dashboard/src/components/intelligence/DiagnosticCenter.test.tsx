import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DiagnosticCenter } from "./DiagnosticCenter";

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

describe("DiagnosticCenter", () => {
  let bridge: BridgeMock;

  beforeEach(() => {
    bridge = {
      apiGet: vi.fn(),
      apiPost: vi.fn(),
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
    };

    bridge.apiGet.mockImplementation((path: string, params?: Record<string, string>) => {
      if (path === "page/diagnostics/health") {
        return Promise.resolve(ok({
          score: 82,
          level: "watch",
          domains: [
            { name: "provider", score: 90, status: "healthy", message: "Provider ready" },
            { name: "recall", score: 77, status: "watch", message: "Recall p95 elevated" },
            { name: "write", score: 96, status: "healthy", message: "Write path stable" },
            { name: "scheduler", score: 72, status: "watch", message: "Backfill has retry history" },
            { name: "index", score: 66, status: "degraded", message: "Index rebuild recommended" },
            { name: "prometheus", score: 100, status: "healthy", message: "Collectors registered" },
          ],
          recommended_actions: ["Review index drift", "Refresh runtime metrics"],
        }));
      }
      if (path === "page/diagnostics/events") {
        expect(params).toEqual({ limit: "50" });
        return Promise.resolve(ok({
          events: [
            {
              event_id: "diag-1",
              created_at: "2026-07-05T08:30:00Z",
              domain: "index",
              severity: "warning",
              title: "Index drift detected",
              message: "Document and vector index counts differ.",
              source: "validator",
              payload: { expected: 128, actual: 126 },
              resolved_at: null,
            },
            {
              event_id: "diag-2",
              created_at: "2026-07-05T07:10:00Z",
              domain: "provider",
              severity: "info",
              title: "Provider recovered",
              message: "Embedding provider became ready.",
              source: "provider_waiter",
              payload: {},
              resolved_at: "2026-07-05T07:12:00Z",
            },
          ],
          total: 2,
        }));
      }
      return Promise.resolve(ok({}));
    });

    bridge.apiPost.mockResolvedValue(ok({ ok: true }));

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

  it("renders health domains, recommended actions, and diagnostic events", async () => {
    render(<DiagnosticCenter showToast={() => undefined} />);

    expect((await screen.findAllByText(/^(Health|健康)$/)).length).toBeGreaterThan(0);
    expect(screen.getByText("82")).toBeTruthy();
    expect(screen.getAllByText(/watch/i).length).toBeGreaterThan(0);
    ["Provider", "Recall", "Write", "Scheduler", "Index", "Prometheus"].forEach((domain) => {
      expect(screen.getAllByText(domain).length).toBeGreaterThan(0);
    });
    expect(screen.getByText("Review index drift")).toBeTruthy();
    expect(screen.getByText("Index drift detected")).toBeTruthy();
    expect(screen.getByText("Document and vector index counts differ.")).toBeTruthy();
    expect(screen.getByText("Provider recovered")).toBeTruthy();
    expect(screen.getByText(/open/i)).toBeTruthy();
    expect(screen.getByText(/resolved/i)).toBeTruthy();
  });

  it("updates cached fallbacks and diagnostic enums after a language change", async () => {
    let locale = "en-US";
    const translations = {
      "en-US": {
        "intelligence.diagnostics.noMessage": "No diagnostic message.",
        "intelligence.diagnostics.untitledEvent": "Untitled diagnostic event",
        "intelligence.diagnostics.noActiveSignal": "No active diagnostic signal.",
        "intelligence.diagnostics.level.watch": "Watch",
        "intelligence.diagnostics.status.healthy": "Healthy",
        "intelligence.diagnostics.severity.warning": "Warning",
        "intelligence.diagnostics.severity.info": "Info",
        "intelligence.diagnostics.state.open": "Open",
        "intelligence.diagnostics.state.resolved": "Resolved",
      },
      "zh-CN": {
        "intelligence.diagnostics.noMessage": "暂无诊断消息。",
        "intelligence.diagnostics.untitledEvent": "未命名诊断事件",
        "intelligence.diagnostics.noActiveSignal": "暂无活跃诊断信号。",
        "intelligence.diagnostics.level.watch": "需关注",
        "intelligence.diagnostics.status.healthy": "正常",
        "intelligence.diagnostics.severity.warning": "警告",
        "intelligence.diagnostics.severity.info": "信息",
        "intelligence.diagnostics.state.open": "未解决",
        "intelligence.diagnostics.state.resolved": "已解决",
      },
    } as const;
    bridge.getLocale.mockImplementation(() => locale);
    bridge.t.mockImplementation((key: string) => {
      const dashboardKey = key.replace(/^dashboard\./, "") as keyof (typeof translations)["en-US"];
      return translations[locale as keyof typeof translations][dashboardKey] ?? key;
    });
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/diagnostics/health") {
        return Promise.resolve(ok({
          score: 82,
          level: "watch",
          domains: [{ name: "provider", score: 90, status: "healthy", message: "" }],
          recommended_actions: [],
        }));
      }
      if (path === "page/diagnostics/events") {
        return Promise.resolve(ok({
          events: [
            {
              event_id: "diag-open",
              created_at: "2026-07-05T08:30:00Z",
              domain: "index",
              severity: "warning",
              title: "",
              message: "",
              source: "validator",
              payload: {},
              resolved_at: null,
            },
            {
              event_id: "diag-resolved",
              created_at: "2026-07-05T07:10:00Z",
              domain: "provider",
              severity: "info",
              title: "Resolved event",
              message: "",
              source: "provider_waiter",
              payload: {},
              resolved_at: "2026-07-05T07:12:00Z",
            },
          ],
          total: 2,
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<DiagnosticCenter showToast={() => undefined} />);

    expect(await screen.findByText("No diagnostic message.")).toBeTruthy();
    expect(screen.getByText("Untitled diagnostic event")).toBeTruthy();
    expect(screen.getAllByText("No active diagnostic signal.").length).toBeGreaterThan(0);
    expect(screen.getByText("Watch")).toBeTruthy();
    expect(screen.getAllByText("Healthy").length).toBeGreaterThan(0);
    expect(screen.getByText("Warning")).toBeTruthy();
    expect(screen.getByText("Open")).toBeTruthy();
    expect(screen.getByText("Resolved")).toBeTruthy();
    expect(bridge.apiGet).toHaveBeenCalledTimes(2);

    locale = "zh-CN";
    window.dispatchEvent(new Event("languagechange"));

    expect(await screen.findByText("暂无诊断消息。")).toBeTruthy();
    expect(screen.getByText("未命名诊断事件")).toBeTruthy();
    expect(screen.getAllByText("暂无活跃诊断信号。").length).toBeGreaterThan(0);
    expect(screen.getByText("需关注")).toBeTruthy();
    expect(screen.getAllByText("正常").length).toBeGreaterThan(0);
    expect(screen.getByText("警告")).toBeTruthy();
    expect(screen.getByText("未解决")).toBeTruthy();
    expect(screen.getByText("已解决")).toBeTruthy();
    expect(bridge.apiGet).toHaveBeenCalledTimes(2);
  });

  it("formats diagnostic event dates with the active dashboard locale", async () => {
    const localeSpy = vi.spyOn(Date.prototype, "toLocaleString").mockImplementation(function (locales) {
      return `formatted:${String(locales)}`;
    });

    render(<DiagnosticCenter showToast={() => undefined} />);

    expect(await screen.findAllByText("formatted:en-US")).toHaveLength(2);
    expect(localeSpy).toHaveBeenCalledWith("en-US");
  });

  it("renders fixed diagnostic chrome from dashboard i18n", async () => {
    bridge.getLocale.mockReturnValue("zh-CN");

    render(<DiagnosticCenter showToast={() => undefined} />);

    expect((await screen.findAllByText("健康")).length).toBeGreaterThan(0);
    expect(screen.getByText("建议操作")).toBeTruthy();
    expect(screen.getByText("诊断操作")).toBeTruthy();
    expect(screen.getByText("运行时检查与受保护的维护命令。")).toBeTruthy();
    expect(screen.getByRole("button", { name: /刷新指标/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /重建索引/ }));
    expect(screen.getByText(/确认现在重建索引/)).toBeTruthy();
    expect(screen.getByText("事件时间线")).toBeTruthy();
    expect(screen.getByText("2 个诊断事件")).toBeTruthy();
    expect(screen.queryByText("Diagnostic actions")).toBe(null);
  });

  it("requires inline confirmation before posting rebuild index action", async () => {
    render(<DiagnosticCenter showToast={() => undefined} />);

    expect(await screen.findByText("Index drift detected")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Rebuild index/i }));

    const confirmMessage = screen.getByText(/Confirm rebuild index/i);
    expect(confirmMessage).toBeTruthy();
    expect(bridge.apiPost).not.toHaveBeenCalled();

    const confirmBar = confirmMessage.closest("div");
    if (!confirmBar) throw new Error("expected rebuild confirmation bar");
    fireEvent.click(within(confirmBar).getByRole("button", { name: /Confirm rebuild/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/diagnostics/actions/run", {
        action: "rebuild_index",
        confirmed: true,
      });
    });
  });

  it("keeps rebuild confirmation open when backend rejects the action", async () => {
    const showToast = vi.fn();
    bridge.apiPost.mockResolvedValue({ status: "error", message: "rebuild_index unavailable" });

    render(<DiagnosticCenter showToast={showToast} />);

    expect(await screen.findByText("Index drift detected")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Rebuild index/i }));
    fireEvent.click(screen.getByRole("button", { name: /Confirm rebuild/i }));

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith("Error: rebuild_index unavailable", true);
    });
    expect(screen.getByText(/Confirm rebuild index/i)).toBeTruthy();
  });
});
