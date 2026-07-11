import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/TopicSegmentationConfig", () => ({
  TopicSegmentationConfig: () => <div>Topic Segmentation Config</div>,
}));

import { SystemPage } from "./SystemPage";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale?: ReturnType<typeof vi.fn>;
  getI18n?: ReturnType<typeof vi.fn>;
  t?: ReturnType<typeof vi.fn>;
}

function ok<T>(data: T) {
  return { status: "ok", data };
}

describe("SystemPage", () => {
  let bridge: BridgeMock;
  let showToast: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    bridge = {
      apiGet: vi.fn(),
      apiPost: vi.fn(),
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
    };
    showToast = vi.fn();

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

  it("loads stats and backups on mount and renders overview data", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") {
        return Promise.resolve(ok({
          total_memories: 12,
          active_count: 8,
          archived_count: 3,
          deleted_count: 1,
          graph_nodes: 7,
          atom_count: 10,
          importance_distribution: { "1": 2, "2": 5 },
          atom_types: { fact: 4, note: 6 },
          sessions: { "session-a": { turns: 2 } },
        }));
      }
      if (path === "page/backup/list") {
        return Promise.resolve(ok({
          backups: [
            {
              name: "backup-2026-06-28",
              file_count: 4,
              backup_timestamp: "2026-06-28T12:00:00Z",
            },
          ],
        }));
      }
      if (path === "page/metrics/summary") {
        return Promise.resolve(ok({
          recall: {
            sample_count: 7,
            p50_total_ms: 42.4,
            p95_total_ms: 123.4,
            avg_total_ms: 80.2,
          },
          background_tasks: {
            tracked: 5,
            active: 2,
            completed: 3,
            failed: 1,
            cancelled: 1,
            failed_tasks: [
              {
                name: "provider-retry",
                error: "TimeoutError",
                message: "provider retry timed out",
                suggestion: "检查 LLM/Embedding provider 配置与网络状态，然后等待重试或重启插件初始化。",
              },
            ],
            schedulers: {
              backfill: {
                job_id: "bf_1783140000",
                status: "failed",
                errors: 2,
                last_error: "topic split failed",
                started_at: 1783139900,
                last_finished_at: 1783140000,
                retry_count: 2,
                suggestion: "检查话题分割配置和最近的错误详情；修复后可重新启动存量回填。",
              },
              decay: {
                check_hour: 3,
                check_minute: 15,
                next_run_in_seconds: 7200.25,
                last_decay_date: "2026-07-04",
                last_completed_at: 1783140000.5,
                retry_count: 1,
              },
            },
          },
          provider: {
            status: "waiting",
            attempts: 4,
            max_attempts: 60,
            missing_provider: ["embedding"],
          },
          index: {
            validator_available: true,
            last_rebuild_success: false,
            last_rebuild_errors: 3,
            last_rebuild_total: 9,
            last_rebuild_duration_seconds: 1.25,
          },
          write_coordinator: {
            operations_total: 20,
            lock_retries_total: 4,
            failures_total: 1,
          },
          prometheus: {
            available: true,
            collector_count: 9,
          },
        }));
      }
      return Promise.resolve(ok({}));
    });

    const { container } = render(<SystemPage showToast={showToast} />);

    expect(container.querySelector('[data-layout="standard"]')).toBeTruthy();
    expect(screen.getByRole("tab", { name: "System" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Quality Monitor" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Companion Plugins" })).toBeTruthy();

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/stats", {});
      expect(bridge.apiGet).toHaveBeenCalledWith("page/backup/list", {});
      expect(bridge.apiGet).toHaveBeenCalledWith("page/metrics/summary", {});
    });

    expect(await screen.findByText("12")).toBeTruthy();
    expect(screen.getByText("Runtime Observability")).toBeTruthy();
    expect(screen.getByText("Recall p95")).toBeTruthy();
    expect(screen.getByText("123.4 ms")).toBeTruthy();
    expect(screen.getByText("Background Active")).toBeTruthy();
    expect(screen.getByText("Background Failures")).toBeTruthy();
    expect(screen.getByText("Backfill Status")).toBeTruthy();
    expect(screen.getByText("failed")).toBeTruthy();
    expect(screen.getByText("Backfill Retries")).toBeTruthy();
    expect(screen.getByText("Decay Next Run")).toBeTruthy();
    expect(screen.getByText("7200.3 s")).toBeTruthy();
    expect(screen.getByText("Decay Last Date")).toBeTruthy();
    expect(screen.getByText("2026-07-04")).toBeTruthy();
    expect(screen.getByText("Decay Retries")).toBeTruthy();
    expect(screen.getByText("provider-retry")).toBeTruthy();
    expect(screen.getByText("TimeoutError")).toBeTruthy();
    expect(screen.getByText("检查 LLM/Embedding provider 配置与网络状态，然后等待重试或重启插件初始化。")).toBeTruthy();
    expect(screen.getByText("检查话题分割配置和最近的错误详情；修复后可重新启动存量回填。")).toBeTruthy();
    expect(screen.getByText("Provider Status")).toBeTruthy();
    expect(screen.getByText("waiting")).toBeTruthy();
    expect(screen.getByText("Provider Attempts")).toBeTruthy();
    expect(screen.getByText("4 / 60")).toBeTruthy();
    expect(screen.getByText("Index Rebuild Errors")).toBeTruthy();
    expect(screen.getByText("3 / 9")).toBeTruthy();
    expect(screen.getByText("Index Rebuild Duration")).toBeTruthy();
    expect(screen.getByText("1.3 s")).toBeTruthy();
    expect(screen.getByText("Write Failures")).toBeTruthy();
    expect(screen.getByText("Lock Retries")).toBeTruthy();
    expect(screen.getByText("Prometheus Collectors")).toBeTruthy();
    expect(screen.getByText("Importance Distribution")).toBeTruthy();
    expect(screen.getByText("backup-2026-06-28")).toBeTruthy();
    expect(screen.getByText("session-a")).toBeTruthy();
    expect(screen.getByText("Topic Segmentation Config")).toBeTruthy();
  });

  it("confirms restore actions before posting the backup restore request", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ total_memories: 1 }));
      if (path === "page/backup/list") {
        return Promise.resolve(ok({
          backups: [{ name: "backup-alpha", file_count: 2 }],
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({ message: "restore complete" }));

    render(<SystemPage showToast={showToast} />);

    expect(await screen.findByText("backup-alpha")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /restore backup/i }));

    const confirmMessage = screen.getByText("Restore data from backup-alpha? This will overwrite current data.");
    expect(confirmMessage).toBeTruthy();
    expect(bridge.apiPost).not.toHaveBeenCalledWith("page/backup/restore", { name: "backup-alpha" });

    const confirmBar = confirmMessage.closest("div");
    if (!confirmBar) throw new Error("expected restore confirmation bar");

    fireEvent.click(within(confirmBar).getByRole("button", { name: /restore backup/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/backup/restore", { name: "backup-alpha" });
    });
    expect(showToast).toHaveBeenCalledWith("restore complete");
  });

  it("supports selecting multiple backups and batch deletion through inline confirmation", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ total_memories: 2 }));
      if (path === "page/backup/list") {
        return Promise.resolve(ok({
          backups: [
            { name: "backup-a", file_count: 2 },
            { name: "backup-b", file_count: 3 },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({ message: "deleted 2 backups" }));

    render(<SystemPage showToast={showToast} />);

    expect(await screen.findByText("backup-a")).toBeTruthy();
    expect(screen.getByText("backup-b")).toBeTruthy();

    fireEvent.click(screen.getByText("Select all"));

    expect(screen.getByText("2 selected")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));

    const confirmMessage = screen.getByText("Delete 2 backups? This cannot be undone.");
    expect(confirmMessage).toBeTruthy();

    const confirmBar = confirmMessage.closest("div");
    if (!confirmBar) throw new Error("expected batch delete confirmation bar");

    fireEvent.click(within(confirmBar).getByRole("button", { name: /delete selected/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/backup/batch-delete", {
        names: ["backup-a", "backup-b"],
      });
    });
    expect(showToast).toHaveBeenCalledWith("deleted 2 backups");
  });

  it("requires confirmation before dashboard dependency install and build actions", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ total_memories: 2 }));
      if (path === "page/backup/list") return Promise.resolve(ok({ backups: [] }));
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({
      stdout: "completed",
      stderr: "",
      exit_code: 0,
      success: true,
    }));

    render(<SystemPage showToast={showToast} />);

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/stats", {});
    });

    fireEvent.click(screen.getByRole("button", { name: /install dependencies/i }));

    const installConfirm = screen.getByText("Install Dashboard dependencies now?");
    expect(installConfirm).toBeTruthy();
    expect(bridge.apiPost).not.toHaveBeenCalledWith("page/dashboard/install", {});

    let confirmBar = installConfirm.closest("div");
    if (!confirmBar) throw new Error("expected install confirmation bar");
    fireEvent.click(within(confirmBar).getByRole("button", { name: /install dependencies/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/dashboard/install", {});
    });

    fireEvent.click(screen.getByRole("button", { name: /build page/i }));

    const buildConfirm = screen.getByText("Build Dashboard production assets now?");
    expect(buildConfirm).toBeTruthy();

    confirmBar = buildConfirm.closest("div");
    if (!confirmBar) throw new Error("expected build confirmation bar");
    fireEvent.click(within(confirmBar).getByRole("button", { name: /build page/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/dashboard/build", {});
    });
  });

  it("loads quality and delegation tabs and supports resetting quality monitoring", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ total_memories: 5 }));
      if (path === "page/backup/list") return Promise.resolve(ok({ backups: [] }));
      if (path === "page/quality/stats") {
        return Promise.resolve(ok({
          avg_consistency: 0.8,
          avg_coherence: 0.7,
          avg_relevance: 0.9,
          avg_freshness: 0.6,
          avg_accuracy: 0.5,
          avg_overall: 0.7,
          paused: true,
          pause_reason: "manual pause",
        }));
      }
      if (path === "page/quality/recent") {
        return Promise.resolve(ok({
          scores: [
            {
              atom_id: "atom-1",
              consistency: 0.8,
              coherence: 0.7,
              relevance: 0.9,
              freshness: 0.6,
              accuracy: 0.5,
              overall: 0.7,
            },
          ],
        }));
      }
      if (path === "page/quality/alerts") {
        return Promise.resolve(ok({
          alerts: [
            {
              id: 1,
              level: "high",
              dimension: "accuracy",
              score: 0.4,
              threshold: 0.5,
              message: "Accuracy drift",
              suggestion: "Review the latest imports",
              timestamp: 1719571200,
            },
          ],
        }));
      }
      if (path === "page/delegation/status") {
        return Promise.resolve(ok({
          self_learning_active: true,
          self_learning_label: "Self Learning plugin",
          chatplus_active: false,
          delegated_jargon: true,
          delegated_expression: false,
          delegated_affection: true,
          delegated_reply: false,
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<SystemPage showToast={showToast} />);

    fireEvent.click(screen.getByRole("tab", { name: /quality monitor/i }));

    expect(await screen.findByText("Quality scoring paused: manual pause")).toBeTruthy();
    expect(screen.getByText("Accuracy drift")).toBeTruthy();
    expect(screen.getByText("atom-1")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /reset monitor/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/quality/reset", {});
    });
    expect(showToast).toHaveBeenCalledWith("Quality monitor reset");

    fireEvent.click(screen.getByRole("tab", { name: /companion plugins/i }));

    expect(await screen.findByText("Self Learning plugin")).toBeTruthy();
    expect(screen.getByText("Feature Delegation")).toBeTruthy();
    expect(screen.getAllByText("Delegated").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Local").length).toBeGreaterThan(0);
  });
});
