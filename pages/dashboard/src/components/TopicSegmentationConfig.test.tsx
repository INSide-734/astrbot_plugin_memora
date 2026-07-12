import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopicSegmentationConfig } from "./TopicSegmentationConfig";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale?: ReturnType<typeof vi.fn>;
  getI18n?: ReturnType<typeof vi.fn>;
  t?: ReturnType<typeof vi.fn>;
}

describe("TopicSegmentationConfig", () => {
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

  it("loads initial backfill status and renders progress details", async () => {
    bridge.apiGet.mockResolvedValue({
      status: "ok",
      data: {
        status: "running",
        processed: 3,
        total: 5,
        errors: 1,
      },
    });

    render(<TopicSegmentationConfig showToast={showToast} />);

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/backfill/status", {});
    });

    expect(await screen.findByText("Running")).toBeTruthy();
    expect(screen.getByText("3 / 5")).toBeTruthy();
    expect(screen.getByText("Errors: 1")).toBeTruthy();
    expect(screen.getByRole("button")).toHaveProperty("disabled", true);
  });

  it.each([
    ["stopping", "Stopping"],
    ["cancelled", "Cancelled"],
    ["completed_with_errors", "Completed with errors"],
  ])("renders the real %s backfill status", async (status, label) => {
    bridge.apiGet.mockResolvedValue({
      status: "ok",
      data: { status, processed: 3, total: 5, errors: 0 },
    });

    render(<TopicSegmentationConfig showToast={showToast} />);

    expect(await screen.findByText(label)).toBeTruthy();
    expect(screen.queryByText("Failed")).toBeNull();
  });

  it("starts backfill, shows success toast, and refreshes status", async () => {
    bridge.apiGet
      .mockResolvedValueOnce({
        status: "ok",
        data: { status: "idle", processed: 0, total: 0, errors: 0 },
      })
      .mockResolvedValueOnce({
        status: "ok",
        data: { status: "completed", processed: 8, total: 8, errors: 0 },
      });
    bridge.apiPost.mockResolvedValue({
      status: "ok",
      data: { accepted: true },
    });

    render(<TopicSegmentationConfig showToast={showToast} />);

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/backfill/start", {});
    });
    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledTimes(2);
    });

    expect(showToast).toHaveBeenCalledWith("Backfill task started");
    expect(await screen.findByText("Completed")).toBeTruthy();
    expect(screen.getByText("8 / 8")).toBeTruthy();
  });

  it("shows an error toast when starting backfill fails", async () => {
    bridge.apiGet.mockResolvedValue({
      status: "ok",
      data: { status: "idle", processed: 0, total: 0, errors: 0 },
    });
    bridge.apiPost.mockRejectedValue(new Error("network boom"));

    render(<TopicSegmentationConfig showToast={showToast} />);

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith("Error: network boom", true);
    });
    expect(bridge.apiGet).toHaveBeenCalledTimes(1);
  });
});
