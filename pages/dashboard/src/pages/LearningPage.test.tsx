import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LearningPage } from "./LearningPage";

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

describe("LearningPage", () => {
  let bridge: BridgeMock;
  let showToast: ReturnType<typeof vi.fn>;
  let confirmMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    bridge = {
      apiGet: vi.fn(),
      apiPost: vi.fn(),
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
    };
    showToast = vi.fn();
    confirmMock = vi.fn();

    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: bridge,
    });
    Object.defineProperty(window, "confirm", {
      configurable: true,
      writable: true,
      value: confirmMock,
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

  it("loads learning stats, groups, and expression patterns", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [
            { group_id: "group-1", message_count: 12 },
            { group_id: "group-2", message_count: 8 },
          ],
        }));
      }
      if (path === "page/learning/status") {
        return Promise.resolve(ok({
          hit_rate: 0.83,
          avg_quality: 0.81234,
          total_trials: 18,
          total_corrections: 4,
          parameters: {
            retrieval_weight: 0.8,
            style_bias: 0.35,
          },
          history: [
            { timestamp: "2026-06-28T10:30:00Z", action: "adjusted", detail: "Raised retrieval weight" },
          ],
        }));
      }
      if (path === "page/expression/patterns") {
        return Promise.resolve(ok({
          patterns: [
            {
              pattern_id: 1,
              group_id: params.group_id,
              situation: "Greeting",
              expression: "Formal greeting",
              weight: 0.8,
              usage_count: 6,
            },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<LearningPage showToast={showToast} />);

    const page = screen.getByRole("region", { name: /Learning|学习/ });
    expect(page.getAttribute("data-layout")).toBe("standard");
    expect(page.querySelector('[data-slot="page-header"]')).toBeTruthy();
    expect(page.querySelector('[data-slot="metric-grid"]')).toBeTruthy();

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/groups", {});
      expect(bridge.apiGet).toHaveBeenCalledWith("page/learning/status", {});
      expect(bridge.apiGet).toHaveBeenCalledWith("page/expression/patterns", { group_id: "group-1" });
    });

    expect(await screen.findByText("83.0%")).toBeTruthy();
    expect(screen.getByText("0.812")).toBeTruthy();
    expect(screen.getByText("18")).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
    expect(screen.getByText("Learned Parameters")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Learned Parameters" })).toBeTruthy();
    expect(screen.getByText("retrieval_weight")).toBeTruthy();
    expect(screen.getByText("0.80")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "retrieval_weight" })).toBeTruthy();
    expect(screen.getByText("Learning History")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Learning History" })).toBeTruthy();
    expect(screen.getByText("adjusted")).toBeTruthy();
    expect(screen.getByText("Raised retrieval weight")).toBeTruthy();
    expect(screen.getByText("Expression Patterns")).toBeTruthy();
    expect(screen.getByText("Greeting")).toBeTruthy();
    expect(screen.getByText("Formal greeting")).toBeTruthy();
    expect(screen.getByText("80%")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: /Greeting.*weight/i })).toBeTruthy();
    expect(screen.getByText("6")).toBeTruthy();
  });

  it("requires confirmation before resetting learning and refreshes stats after success", async () => {
    let statusCalls = 0;
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [{ group_id: "group-1", message_count: 12 }],
        }));
      }
      if (path === "page/learning/status") {
        statusCalls += 1;
        if (statusCalls === 1) {
          return Promise.resolve(ok({
            hit_rate: 0.5,
            avg_quality: 0.6,
            total_trials: 10,
            total_corrections: 2,
          }));
        }
        return Promise.resolve(ok({
          hit_rate: 0.92,
          avg_quality: 0.95,
          total_trials: 11,
          total_corrections: 2,
        }));
      }
      if (path === "page/expression/patterns") {
        return Promise.resolve(ok({ patterns: [] }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<LearningPage showToast={showToast} />);

    expect(await screen.findByText("50.0%")).toBeTruthy();
    expect(screen.getByText("No expression patterns")).toBeTruthy();

    confirmMock.mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: /reset learning/i }));

    expect(confirmMock).toHaveBeenCalledWith("Reset all learned parameters to defaults?");
    expect(bridge.apiPost).not.toHaveBeenCalledWith("page/learning/reset", {});

    confirmMock.mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: /reset learning/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/learning/reset", {});
    });
    expect(showToast).toHaveBeenCalledWith("Learning parameters reset!");

    await waitFor(() => {
      expect(screen.getByText("92.0%")).toBeTruthy();
    });
  });

  it("switches groups and refetches expression patterns for the selected group", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [
            { group_id: "group-1", message_count: 12 },
            { group_id: "group-2", message_count: 8 },
          ],
        }));
      }
      if (path === "page/learning/status") {
        return Promise.resolve(ok({
          hit_rate: 0.75,
          avg_quality: 0.81,
          total_trials: 9,
          total_corrections: 1,
        }));
      }
      if (path === "page/expression/patterns" && params.group_id === "group-1") {
        return Promise.resolve(ok({
          patterns: [
            {
              pattern_id: 1,
              group_id: "group-1",
              situation: "Greeting",
              expression: "Formal greeting",
              weight: 0.8,
              usage_count: 6,
            },
          ],
        }));
      }
      if (path === "page/expression/patterns" && params.group_id === "group-2") {
        return Promise.resolve(ok({
          patterns: [
            {
              pattern_id: 2,
              group_id: "group-2",
              situation: "Closing",
              expression: "Casual wrap-up",
              weight: 0.55,
              usage_count: 3,
            },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<LearningPage showToast={showToast} />);

    expect(await screen.findByText("Formal greeting")).toBeTruthy();

    const hiddenSelectInput = document.querySelector('input[aria-hidden="true"]') as HTMLInputElement | null;
    if (!hiddenSelectInput) throw new Error("expected hidden group select input");
    fireEvent.change(hiddenSelectInput, { target: { value: "group-2" } });

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/expression/patterns", { group_id: "group-2" });
    });

    expect(await screen.findByText("Casual wrap-up")).toBeTruthy();
    expect(screen.getByText("Closing")).toBeTruthy();
    expect(screen.getByText("55%")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
  });
});
