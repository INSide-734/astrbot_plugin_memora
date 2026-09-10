import { StrictMode } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GlobalUpdateDialog } from "./GlobalUpdateDialog";

function updateAvailable() {
  return {
    status: "ok" as const,
    data: {
      enabled: true,
      available: true,
      ignored: false,
      current_version: "1.0.0",
      release: {
        version: "1.1.0",
        notes: "## Runtime update\n\n- Fix update delivery",
      },
    },
  };
}

describe("GlobalUpdateDialog", () => {
  const apiGet = vi.fn();

  beforeEach(() => {
    apiGet.mockReset();
    apiGet.mockResolvedValue(updateAvailable());
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: {
        apiGet,
        getLocale: vi.fn().mockReturnValue("en-US"),
        getI18n: vi.fn().mockReturnValue({}),
        t: vi.fn((key: string) => key),
      },
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

  it("shows release notes once and keeps the dialog closed after dismissal", async () => {
    const { rerender } = render(<StrictMode><GlobalUpdateDialog /></StrictMode>);

    const dialog = await screen.findByRole("dialog", { name: "New version 1.1.0" });
    expect(within(dialog).getByText("Current version 1.0.0")).toBeTruthy();
    expect(within(dialog).getByRole("heading", { name: "Runtime update", level: 2 })).toBeTruthy();
    expect(within(dialog).getByRole("listitem").textContent).toContain("Fix update delivery");

    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "New version 1.1.0" })).toBeNull());
    rerender(<StrictMode><GlobalUpdateDialog /></StrictMode>);
    expect(apiGet).toHaveBeenCalledTimes(1);
  });

  it("does not show disabled, ignored, or unavailable updates", async () => {
    apiGet.mockResolvedValue({
      status: "ok",
      data: { enabled: true, available: false, ignored: true, release: null },
    });

    render(<GlobalUpdateDialog />);

    await waitFor(() => expect(apiGet).toHaveBeenCalledOnce());
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
