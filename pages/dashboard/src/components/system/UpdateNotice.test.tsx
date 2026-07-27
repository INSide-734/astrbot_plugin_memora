import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UpdateNotice } from "./UpdateNotice";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale: ReturnType<typeof vi.fn>;
  getI18n: ReturnType<typeof vi.fn>;
  t: ReturnType<typeof vi.fn>;
}

function ok<T>(data: T): { status: "ok"; data: T } {
  return { status: "ok", data };
}

describe("UpdateNotice", () => {
  let bridge: BridgeMock;
  let showToast: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    window.localStorage.removeItem("memora_lang");
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

  it("shows update details and persists the ignored release", async () => {
    let ignored = false;
    bridge.apiGet.mockImplementation(() => Promise.resolve(ok({
      enabled: true,
      current_version: "1.0.0",
      capabilities: { auto_apply: true },
      available: !ignored,
      ignored,
      ignored_version: ignored ? "1.1.0" : null,
      release: {
        version: "1.1.0",
        tag: "v1.1.0",
        notes: "Fix runtime update flow",
        runtime_filename: "astrbot_plugin_memora-1.1.0-runtime.zip",
        source: "mirror",
      },
    })));
    bridge.apiPost.mockImplementation((path: string) => {
      if (path === "page/update/ignore") ignored = true;
      return Promise.resolve(ok({ ignored_version: "1.1.0" }));
    });

    render(<UpdateNotice showToast={showToast} />);

    expect(await screen.findByText("Plugin update available")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "View update details" }));
    expect(screen.getByText("Fix runtime update flow")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Ignore this version" }));

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledWith(
      "page/update/ignore",
      { version: "1.1.0" },
    ));
    await waitFor(() => expect(screen.queryByText("Plugin update available")).toBeNull());
    expect(showToast).toHaveBeenCalledWith("Version ignored");
  });

  it("downloads the selected runtime update once and reports the result", async () => {
    let resolveDownload!: (value: { status: "ok"; data: { version: string } }) => void;
    bridge.apiGet.mockResolvedValue(ok({
      enabled: true,
      current_version: "1.0.0",
      capabilities: { auto_apply: false },
      available: true,
      ignored: false,
      release: {
        version: "1.1.0",
        notes: "Downloadable runtime",
        runtime_filename: "astrbot_plugin_memora-1.1.0-runtime.zip",
        source: "official",
      },
    }));
    bridge.apiPost.mockImplementation((path: string) => {
      if (path === "page/update/download") {
        return new Promise((resolve) => { resolveDownload = resolve; });
      }
      return Promise.resolve(ok({}));
    });

    render(<UpdateNotice showToast={showToast} />);
    expect(await screen.findByText("Plugin update available")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "View update details" }));
    const download = screen.getByRole("button", { name: "Download update" });
    fireEvent.click(download);
    fireEvent.click(download);

    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
    expect(bridge.apiPost).toHaveBeenCalledWith("page/update/download", {});
    expect(download).toHaveProperty("disabled", true);

    await act(async () => { resolveDownload(ok({ version: "1.1.0" })); });
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      expect.stringContaining("1.1.0"),
    ));
    expect(download).toHaveProperty("disabled", false);
  });

  it("confirms, applies, and polls an automatic runtime update", async () => {
    let resolveApply!: (value: {
      status: "ok";
      data: { operation_id: string; version: string; status: string };
    }) => void;
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/update/status") {
        return Promise.resolve(ok({
          operation_id: "a".repeat(32),
          version: "1.1.0",
          status: "succeeded",
          rollback_performed: false,
          requires_manual_restart: false,
        }));
      }
      return Promise.resolve(ok({
        enabled: true,
        current_version: "1.0.0",
        capabilities: { auto_apply: true },
        available: true,
        ignored: false,
        release: {
          version: "1.1.0",
          notes: "Automatic update",
          runtime_filename: "astrbot_plugin_memora-1.1.0-runtime.zip",
          source: "mirror",
        },
      }));
    });
    bridge.apiPost.mockImplementation((path: string) => {
      if (path === "page/update/apply") {
        return new Promise((resolve) => { resolveApply = resolve; });
      }
      return Promise.resolve(ok({}));
    });

    render(<UpdateNotice showToast={showToast} />);
    expect(await screen.findByText("Plugin update available")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "View update details" }));
    fireEvent.click(screen.getByRole("button", { name: "Install update" }));

    expect(screen.getByRole("dialog", { name: "Install Memora 1.1.0?" })).toBeTruthy();
    const confirm = screen.getByRole("button", { name: "Install and reload" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
    expect(bridge.apiPost).toHaveBeenCalledWith("page/update/apply", {});

    await act(async () => {
      resolveApply(ok({
        operation_id: "a".repeat(32),
        version: "1.1.0",
        status: "reload_scheduled",
      }));
    });
    await waitFor(() => expect(bridge.apiGet).toHaveBeenCalledWith(
      "page/update/status",
      { operation_id: "a".repeat(32) },
    ));
    await waitFor(() => expect(showToast).toHaveBeenCalledWith(
      "Version 1.1.0 was installed and reloaded.",
      false,
    ));
  });
});
