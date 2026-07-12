import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JargonPage } from "./JargonPage";

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

describe("JargonPage", () => {
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

  it("loads stats and candidate rows, then confirms a candidate", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [{ group_id: "group-1", message_count: 12 }],
        }));
      }
      if (path === "page/jargon/stats") {
        expect(params).toEqual({ group_id: "group-1" });
        return Promise.resolve(ok({
          total_terms: 3,
          candidate_count: 1,
          store_confirmed: 2,
        }));
      }
      if (path === "page/jargon/candidates") {
        expect(params).toEqual({ group_id: "group-1", limit: "50" });
        return Promise.resolve(ok({
          candidates: [
            {
              term: "$& $$",
              group_id: "group-1",
              score: 0.82,
              frequency: 7,
              unique_users: 3,
              idf_score: 0.1,
              burst_score: 0.2,
              concentration_score: 0.3,
              first_seen: 1,
              context_examples: ["$& $$ means a deployment shortcut"],
            },
          ],
        }));
      }
      if (path === "page/jargon/meanings") {
        return Promise.resolve(ok({ meanings: [] }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<JargonPage showToast={showToast} />);

    expect(screen.getByRole("region").getAttribute("data-layout")).toBe("dense");

    expect(await screen.findByText("$& $$")).toBeTruthy();
    expect(screen.getByText("82%")).toBeTruthy();
    expect(screen.getByText("$& $$ means a deployment shortcut")).toBeTruthy();
    expect(screen.getAllByText("3").length).toBeGreaterThanOrEqual(1);
    const tabs = screen.getByRole("tablist", { name: "Jargon views" });
    expect(tabs).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Candidates" }).getAttribute("aria-selected")).toBe("true");

    fireEvent.click(screen.getByTitle("Confirm"));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/jargon/confirm", {
        term: "$& $$",
        group_id: "group-1",
        confirmed: true,
      });
    });
    expect(showToast).toHaveBeenCalledWith("Confirmed '$& $$' as jargon");
  });

  it("switches to meanings and renders confirmed meaning rows", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [{ group_id: "group-1", message_count: 12 }],
        }));
      }
      if (path === "page/jargon/stats") {
        return Promise.resolve(ok({ total_terms: 1, candidate_count: 0, store_confirmed: 1 }));
      }
      if (path === "page/jargon/candidates") {
        return Promise.resolve(ok({ candidates: [] }));
      }
      if (path === "page/jargon/meanings") {
        expect(params).toEqual({ group_id: "group-1", confirmed_only: "false" });
        return Promise.resolve(ok({
          meanings: [
            {
              term: "灰度",
              group_id: "group-1",
              meaning: "Gradual rollout",
              confidence: 0.91,
              is_jargon: true,
              is_confirmed: true,
              is_global: true,
              is_complete: true,
              count: 3,
              last_inference_count: 2,
              created_at: 1,
              updated_at: 2,
            },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<JargonPage showToast={showToast} />);

    expect(await screen.findByText("No candidates found")).toBeTruthy();
    const candidatesTab = screen.getByRole("tab", { name: "Candidates" });
    const meaningsTab = screen.getByRole("tab", { name: "Confirmed" });
    const candidatesPanel = screen.getByRole("tabpanel", { name: "Candidates" });
    const meaningsPanel = document.getElementById("jargon-meanings-panel") as HTMLElement;

    expect(candidatesTab.id).toBe("jargon-candidates-tab");
    expect(meaningsTab.id).toBe("jargon-meanings-tab");
    expect(candidatesTab.getAttribute("aria-controls")).toBe(candidatesPanel.id);
    expect(candidatesPanel.getAttribute("aria-labelledby")).toBe(candidatesTab.id);
    expect(meaningsPanel.getAttribute("aria-labelledby")).toBe(meaningsTab.id);
    expect(candidatesTab.tabIndex).toBe(0);
    expect(meaningsTab.tabIndex).toBe(-1);
    expect(meaningsPanel.hidden).toBe(true);

    candidatesTab.focus();
    fireEvent.keyDown(candidatesTab, { key: "ArrowRight" });

    expect(document.activeElement).toBe(meaningsTab);
    expect(meaningsTab.getAttribute("aria-selected")).toBe("true");
    expect(candidatesPanel.hidden).toBe(true);
    expect(meaningsPanel.hidden).toBe(false);

    expect(await screen.findByText("灰度")).toBeTruthy();
    expect(screen.getByText("Gradual rollout")).toBeTruthy();
    expect(screen.getByText("91%")).toBeTruthy();
  });

  it("starts mining and reports API failures through the toast handler", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [{ group_id: "group-1", message_count: 12 }],
        }));
      }
      if (path === "page/jargon/stats") {
        return Promise.resolve(ok({ total_terms: 0, candidate_count: 0, store_confirmed: 0 }));
      }
      if (path === "page/jargon/candidates") {
        return Promise.resolve(ok({ candidates: [] }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockRejectedValue(new Error("mining failed"));

    render(<JargonPage showToast={showToast} />);

    expect(await screen.findByText("No candidates found")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Discover" }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/jargon/mine", {
        group_id: "group-1",
        limit: 5,
      });
      expect(showToast).toHaveBeenCalledWith("Error: mining failed", true);
    });
  });
});
