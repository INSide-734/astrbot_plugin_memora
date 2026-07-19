import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    const localeSpy = vi.spyOn(Date.prototype, "toLocaleString");
    const localeDateSpy = vi.spyOn(Date.prototype, "toLocaleDateString").mockImplementation(
      (_locales, options) => options?.timeZone === "UTC" ? "July 4, 2026" : "July 3, 2026",
    );
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
          atom_types: { FACTUAL: 4, vendor_type: 6 },
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
    expect(screen.getByText("Failed")).toBeTruthy();
    expect(screen.getByText("Backfill Retries")).toBeTruthy();
    expect(screen.getByText("Decay Next Run")).toBeTruthy();
    expect(screen.getByText("7,200.3 s")).toBeTruthy();
    expect(screen.getByText("Decay Last Date")).toBeTruthy();
    expect(screen.getByText("July 4, 2026")).toBeTruthy();
    expect(screen.getByText("Decay Retries")).toBeTruthy();
    expect(screen.getByText("provider-retry")).toBeTruthy();
    expect(screen.getByText("TimeoutError")).toBeTruthy();
    expect(screen.getByText("检查 LLM/Embedding provider 配置与网络状态，然后等待重试或重启插件初始化。")).toBeTruthy();
    expect(screen.getByText("检查话题分割配置和最近的错误详情；修复后可重新启动存量回填。")).toBeTruthy();
    expect(screen.getByText("Provider Status")).toBeTruthy();
    expect(screen.getByText("Waiting")).toBeTruthy();
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
    expect(screen.getByText("Factual")).toBeTruthy();
    expect(screen.getByText("vendor_type")).toBeTruthy();
    expect(screen.getByText("backup-2026-06-28")).toBeTruthy();
    expect(localeSpy).toHaveBeenCalledWith("en-US");
    expect(localeDateSpy).toHaveBeenCalledWith("en-US", { timeZone: "UTC" });
    expect(screen.getByText("session-a")).toBeTruthy();
    expect(screen.getByText("Topic Segmentation Config")).toBeTruthy();
  });

  it("preserves unknown runtime statuses instead of hiding them as unavailable", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ total_memories: 1 }));
      if (path === "page/backup/list") return Promise.resolve(ok({ backups: [] }));
      if (path === "page/metrics/summary") {
        return Promise.resolve(ok({
          background_tasks: {
            schedulers: { backfill: { status: "vendor_backfill" } },
          },
          provider: { status: "vendor_provider" },
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<SystemPage showToast={showToast} />);

    expect(await screen.findByText("vendor_backfill")).toBeTruthy();
    expect(screen.getByText("vendor_provider")).toBeTruthy();
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
    expect(bridge.apiPost).not.toHaveBeenCalledWith("page/backup/restore", { name: "backup-alpha", apply_mode: "restart" });

    const confirmDialog = screen.getByRole("dialog", { name: "Restore Backup" });
    expect(within(confirmDialog).getByRole("button", { name: /restore backup/i }).className).toContain("bg-destructive/10");
    fireEvent.click(within(confirmDialog).getByRole("button", { name: /restore backup/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/backup/restore", { name: "backup-alpha", apply_mode: "restart" });
    });
    expect(showToast).toHaveBeenCalledWith("restore complete");
  });

  it("posts reload mode and polls until restore succeeds", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ total_memories: 1 }));
      if (path === "page/backup/list") {
        return Promise.resolve(ok({
          backups: [{
            name: "backup-hot",
            file_count: 2,
            integrity: "verified",
            can_restore: true,
            can_hot_restore: true,
          }],
          capabilities: { hot_reload: true },
          pending_restore: null,
        }));
      }
      if (path === "page/backup/status") {
        return Promise.resolve(ok({
          operation_id: "op-hot",
          restore_status: "succeeded",
          requires_manual_restart: false,
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({
      operation_id: "op-hot",
      restore_status: "reload_scheduled",
      apply_mode: "reload",
      reload_scheduled: true,
    }));

    render(<SystemPage showToast={showToast} />);
    expect(await screen.findByText("backup-hot")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /restore backup/i }));
    const dialog = screen.getByRole("dialog", { name: /restore backup/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /restore/i }));

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith(
      "page/backup/restore",
      { name: "backup-hot", apply_mode: "reload" },
    ));
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith(
      "page/backup/status",
      { operation_id: "op-hot" },
    ));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith("Restore succeeded", false));
  });

  it("keeps a manual restart restore visible and allows cancelling it", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ total_memories: 1 }));
      if (path === "page/backup/list") {
        return Promise.resolve(ok({
          backups: [{
            name: "backup-manual",
            file_count: 1,
            integrity: "legacy_unverified",
            can_restore: true,
          }],
          capabilities: { hot_reload: false },
          pending_restore: null,
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost
      .mockResolvedValueOnce(ok({
        operation_id: "op-manual",
        restore_status: "staged",
        apply_mode: "restart",
        requires_manual_restart: true,
      }))
      .mockResolvedValueOnce(ok({
        operation_id: "op-manual",
        restore_status: "cancelled",
      }));

    render(<SystemPage showToast={showToast} />);
    expect(await screen.findByText("backup-manual")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /restore backup/i }));
    const dialog = screen.getByRole("dialog", { name: /restore backup/i });
    expect(dialog.textContent).toContain("legacy backup");
    fireEvent.click(within(dialog).getByRole("button", { name: /restore backup/i }));

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith(
      "page/backup/restore",
      { name: "backup-manual", apply_mode: "restart" },
    ));
    expect(await screen.findByText(/restart AstrBot manually/i)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /cancel staged restore/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith(
      "page/backup/restore/cancel",
      { operation_id: "op-manual" },
    ));
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
    expect(
      screen.getByText("backup-a").closest('[data-state="selected"]'),
    ).toBeTruthy();
    expect(
      screen.getByText("backup-b").closest('[data-state="selected"]'),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));

    const confirmMessage = screen.getByText("Delete 2 backups? This cannot be undone.");
    expect(confirmMessage).toBeTruthy();
    expect(bridge.apiPost).not.toHaveBeenCalledWith("page/backup/batch-delete", {
      names: ["backup-a", "backup-b"],
    });

    const confirmDialog = screen.getByRole("dialog", { name: "Delete Selected" });
    fireEvent.click(within(confirmDialog).getByRole("button", { name: /delete selected/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/backup/batch-delete", {
        names: ["backup-a", "backup-b"],
      });
    });
    expect(showToast).toHaveBeenCalledWith("deleted 2 backups");
  });

  it("keeps failed batch delete selections", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ total_memories: 2 }));
      if (path === "page/backup/list") {
        return Promise.resolve(ok({
          backups: [{ name: "backup-partial-a" }, { name: "backup-partial-b" }],
          capabilities: { hot_reload: true },
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({
      deleted: 1,
      failed: 1,
      deleted_names: ["backup-partial-a"],
      failed_items: [{ name: "backup-partial-b", reason_code: "backup_in_use" }],
    }));

    render(<SystemPage showToast={showToast} />);
    expect(await screen.findByText("backup-partial-a")).toBeTruthy();
    fireEvent.click(screen.getByText("Select all"));
    fireEvent.click(screen.getByRole("button", { name: /delete selected/i }));
    const dialog = screen.getByRole("dialog", { name: /delete selected/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /delete selected/i }));

    await waitFor(() => expect(
      screen.getByText("backup-partial-b").closest('[data-state="selected"]'),
    ).toBeTruthy());
    expect(screen.getByText("backup-partial-a").closest('[data-state="selected"]')).toBeNull();
  });

  it("preserves other selected backups after an individual delete succeeds", async () => {
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
    bridge.apiPost.mockResolvedValue(ok({ message: "deleted backup-a" }));

    render(<SystemPage showToast={showToast} />);
    expect(await screen.findByText("backup-a")).toBeTruthy();

    fireEvent.click(screen.getByRole("checkbox", { name: /backup-b/i }));
    const rowA = screen.getByText("backup-a").closest("div.flex.items-center.justify-between");
    if (!rowA) throw new Error("expected backup-a row");
    fireEvent.click(within(rowA as HTMLElement).getAllByRole("button")[1]);
    const dialog = screen.getByRole("dialog", { name: /delete/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /delete/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/backup/delete", { name: "backup-a" });
      expect(screen.getByText("1 selected")).toBeTruthy();
    });
    expect(screen.getByText("backup-b").closest('[data-state="selected"]')).toBeTruthy();
    expect(screen.getByText("backup-a").closest('[data-state="selected"]')).toBeNull();
  });

  it("confirms purge while a non-overview system tab is active", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ total_memories: 1 }));
      if (path === "page/backup/list") return Promise.resolve(ok({ backups: [] }));
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({ message: "purged" }));

    render(<SystemPage showToast={showToast} />);
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith("page/stats", {}));
    fireEvent.click(screen.getByRole("tab", { name: /quality monitor/i }));
    fireEvent.click(screen.getByRole("button", { name: /purge deleted/i }));

    const dialog = screen.getByRole("dialog", { name: /purge deleted/i });
    fireEvent.click(within(dialog).getByRole("button", { name: /purge deleted/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/system/purge", {}));
  });

  it("keeps command failure details and confirmation open without success cleanup", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ total_memories: 2 }));
      if (path === "page/backup/list") return Promise.resolve(ok({ backups: [] }));
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({
      stdout: "install output",
      stderr: "dependency error",
      exit_code: 9,
      success: false,
    }));

    render(<SystemPage showToast={showToast} />);
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith("page/stats", {}));
    const statsFetches = bridge.apiGet.mock.calls.filter(([path]) => path === "page/stats").length;
    const backupFetches = bridge.apiGet.mock.calls.filter(([path]) => path === "page/backup/list").length;

    fireEvent.click(screen.getByRole("button", { name: /install dependencies/i }));
    const dialog = screen.getByRole("dialog", { name: "Install Dependencies" });
    fireEvent.click(within(dialog).getByRole("button", { name: /install dependencies/i }));

    await waitFor(() => expect(within(dialog).getByRole("alert").textContent).toContain("Command failed (exit code: 9)"));
    expect(screen.getByRole("dialog", { name: "Install Dependencies" })).toBeTruthy();
    const commandOutput = document.querySelector("pre");
    expect(commandOutput?.textContent).toContain("install output\n--- stderr ---\ndependency error");
    expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/stats")).toHaveLength(statsFetches);
    expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/backup/list")).toHaveLength(backupFetches);
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

    let confirmDialog = screen.getByRole("dialog", { name: "Install Dependencies" });
    fireEvent.click(within(confirmDialog).getByRole("button", { name: /install dependencies/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/dashboard/install", {});
    });
    expect(screen.getByText("completed")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /build page/i }));

    const buildConfirm = screen.getByText("Build Dashboard production assets now?");
    expect(buildConfirm).toBeTruthy();
    expect(bridge.apiPost).not.toHaveBeenCalledWith("page/dashboard/build", {});

    confirmDialog = screen.getByRole("dialog", { name: "Build Page" });
    fireEvent.click(within(confirmDialog).getByRole("button", { name: /build page/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/dashboard/build", {});
    });
  });

  it("confirms quality reset, guards duplicate submits, preserves errors, and refreshes quality data", async () => {
    let resolveReset!: (value: { status: "error"; message: string }) => void;
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
          scores: [{
            atom_id: "atom-1",
            consistency: 0.8,
            coherence: 0.7,
            relevance: 0.9,
            freshness: 0.6,
            accuracy: 0.5,
            overall: 0.7,
          }],
        }));
      }
      if (path === "page/quality/alerts") {
        return Promise.resolve(ok({
          alerts: [{
            id: 1,
            level: "high",
            dimension: "accuracy",
            score: 0.4,
            threshold: 0.5,
            message: "Accuracy drift",
            suggestion: "Review the latest imports",
            timestamp: 1719571200,
          }],
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockImplementation((path: string) => {
      if (path === "page/quality/reset") {
        return new Promise((resolve) => { resolveReset = resolve; });
      }
      return Promise.resolve(ok({}));
    });

    render(<SystemPage showToast={showToast} />);
    fireEvent.click(screen.getByRole("tab", { name: /quality monitor/i }));

    expect(await screen.findByText("Quality scoring paused: manual pause")).toBeTruthy();
    expect(screen.getByText("Accuracy drift")).toBeTruthy();
    expect(screen.getByText("atom-1")).toBeTruthy();
    await waitFor(() => expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/quality/stats")).toHaveLength(1));

    const resetTrigger = screen.getByRole("button", { name: /reset monitor/i });
    fireEvent.click(resetTrigger);
    expect(bridge.apiPost).not.toHaveBeenCalled();

    const dialog = screen.getByRole("dialog", { name: "Reset Monitor" });
    expect(within(dialog).getByText("Quality monitor reset")).toBeTruthy();
    const cancel = within(dialog).getByRole("button", { name: /cancel/i });
    const confirm = within(dialog).getByRole("button", { name: /reset monitor/i });
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
    expect(bridge.apiPost).toHaveBeenCalledWith("page/quality/reset", {});
    expect(cancel).toHaveProperty("disabled", true);
    expect(confirm).toHaveProperty("disabled", true);
    expect(resetTrigger).toHaveProperty("disabled", true);

    await act(async () => { resolveReset({ status: "error", message: "quality reset failed" }); });
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("quality reset failed"));
    expect(screen.getByRole("dialog", { name: "Reset Monitor" })).toBeTruthy();

    bridge.apiPost.mockResolvedValueOnce(ok({}));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Reset Monitor" })).getByRole("button", { name: /reset monitor/i }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Reset Monitor" })).toBe(null));
    await waitFor(() => expect(bridge.apiGet.mock.calls.filter(([path]) => path === "page/quality/stats")).toHaveLength(2));
    expect(showToast).toHaveBeenCalledWith("Quality monitor reset");
  });

  it("keeps the frozen restore operation visible and locked when the request fails", async () => {
    let resolveRestore!: (value: { status: "error"; message: string }) => void;
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ total_memories: 7 }));
      if (path === "page/backup/list") return Promise.resolve(ok({ backups: [{ name: "backup-frozen", file_count: 2 }] }));
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockReturnValue(new Promise((resolve) => { resolveRestore = resolve; }));

    render(<SystemPage showToast={showToast} />);
    expect(await screen.findByText("backup-frozen")).toBeTruthy();
    const trigger = screen.getByRole("button", { name: /restore backup/i });
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Restore Backup" });
    const confirm = within(dialog).getByRole("button", { name: /restore backup/i });
    const cancel = within(dialog).getByRole("button", { name: /cancel/i });

    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
    expect(bridge.apiPost).toHaveBeenCalledWith("page/backup/restore", { name: "backup-frozen", apply_mode: "restart" });
    expect(trigger).toHaveProperty("disabled", true);
    expect(confirm).toHaveProperty("disabled", true);
    expect(cancel).toHaveProperty("disabled", true);
    expect(confirm.textContent).toMatch(/restore backup…/i);

    await act(async () => { resolveRestore({ status: "error", message: "restore failed" }); });
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("restore failed"));
    expect(screen.getByRole("dialog", { name: "Restore Backup" }).textContent).toContain("backup-frozen");
    expect(screen.getAllByText("7").length).toBeGreaterThan(0);
  });

  it("keeps direct maintenance actions direct while confirming purge and individual delete", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/stats") return Promise.resolve(ok({ total_memories: 1 }));
      if (path === "page/backup/list") return Promise.resolve(ok({ backups: [{ name: "backup-direct", file_count: 1 }] }));
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({ message: "done" }));
    render(<SystemPage showToast={showToast} />);
    expect(await screen.findByText("backup-direct")).toBeTruthy();

    const rebuild = screen.getByRole("button", { name: /rebuild index/i });
    const compact = screen.getByRole("button", { name: /compact/i });
    const createBackup = screen.getAllByRole("button", { name: /create backup/i })[0];
    fireEvent.click(rebuild);
    fireEvent.click(compact);
    fireEvent.click(createBackup);
    fireEvent.click(screen.getByRole("button", { name: "JSONL" }));
    fireEvent.click(screen.getByRole("button", { name: "Markdown" }));
    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/system/rebuild", {});
      expect(bridge.apiPost).toHaveBeenCalledWith("page/system/compact", {});
      expect(bridge.apiPost).toHaveBeenCalledWith("page/backup/create", {});
      expect(bridge.apiPost).toHaveBeenCalledWith("page/export/memories", { format: "jsonl" });
      expect(bridge.apiPost).toHaveBeenCalledWith("page/export/memories", { format: "markdown" });
    });

    fireEvent.click(screen.getByRole("button", { name: /purge deleted/i }));
    expect(bridge.apiPost).not.toHaveBeenCalledWith("page/system/purge", {});
    const purgeDialog = screen.getByRole("dialog", { name: /purge deleted/i });
    fireEvent.click(within(purgeDialog).getByRole("button", { name: /purge deleted/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/system/purge", {}));

    const row = screen.getByText("backup-direct").closest("div.flex.items-center.justify-between");
    if (!row) throw new Error("expected backup row");
    fireEvent.click(within(row as HTMLElement).getAllByRole("button")[1]);
    expect(bridge.apiPost).not.toHaveBeenCalledWith("page/backup/delete", { name: "backup-direct" });
    const deleteDialog = screen.getByRole("dialog", { name: /delete/i });
    fireEvent.click(within(deleteDialog).getByRole("button", { name: /delete/i }));
    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith("page/backup/delete", { name: "backup-direct" }));
  });
});
