import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AffectionPage } from "./AffectionPage";

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

describe("AffectionPage", () => {
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

  it("loads group affection status and renders mood plus leaderboard data", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [{ group_id: "group-1", message_count: 12 }],
        }));
      }
      if (path === "page/affection/status") {
        expect(params).toEqual({ group_id: "group-1" });
        return Promise.resolve(ok({
          group_id: "group-1",
          total_affection: 48,
          max_total_affection: 100,
          user_count: 3,
          current_mood: {
            mood_type: "happy",
            intensity: 0.72,
            description: "Group has been upbeat today.",
            is_active: true,
          },
          top_users: [
            {
              user_id: "alice",
              group_id: "group-1",
              affection_score: 42,
              affection_level: "FRIENDLY",
              level_name: "友好",
              interaction_count: 8,
              last_interaction: 1,
            },
            {
              user_id: "bob",
              group_id: "group-1",
              affection_score: 6,
              affection_level: "NEUTRAL",
              level_name: "Neutral",
              interaction_count: 3,
              last_interaction: 2,
            },
            {
              user_id: "carol",
              group_id: "group-1",
              affection_score: 88,
              affection_level: "VIP",
              level_name: "贵宾",
              interaction_count: 12,
              last_interaction: 3,
            },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<AffectionPage showToast={showToast} />);

    const page = screen.getByRole("region", { name: /Affection|好感/ });
    expect(page.getAttribute("data-layout")).toBe("standard");
    expect(page.querySelector('[data-slot="page-header"]')).toBeTruthy();

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/groups", {});
      expect(bridge.apiGet).toHaveBeenCalledWith("page/affection/status", { group_id: "group-1" });
    });

    expect(await screen.findByText("Group has been upbeat today.")).toBeTruthy();
    expect(page.querySelector('[data-slot="metric-grid"]')).toBeTruthy();
    expect(screen.getByText("72%")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: /intensity/i })).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: /alice.*score/i })).toBeTruthy();
    expect(screen.getAllByRole("progressbar").every((meter) => meter.getAttribute("data-slot") === "progress")).toBe(true);
    expect(screen.getByText("alice")).toBeTruthy();
    expect(screen.getByText("Friendly")).toBeTruthy();
    expect(screen.getByText("bob")).toBeTruthy();
    expect(screen.getByText("Neutral")).toBeTruthy();
    expect(screen.getByText("carol")).toBeTruthy();
    expect(screen.getByText("VIP")).toBeTruthy();
    expect(screen.queryByText("贵宾")).toBeNull();
    expect(screen.getByText("Active")).toBeTruthy();
    expect(showToast).not.toHaveBeenCalled();
  });

  it("shows an empty state and sends failures to the toast handler", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [{ group_id: "group-1", message_count: 12 }],
        }));
      }
      if (path === "page/affection/status") {
        return Promise.reject(new Error("affection unavailable"));
      }
      return Promise.resolve(ok({}));
    });

    render(<AffectionPage showToast={showToast} />);

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith("Error: affection unavailable", true);
    });
    expect(screen.getByText("No affection data")).toBeTruthy();
  });

  it("refreshes affection status from the selected group", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [
            { group_id: "group-1", message_count: 12 },
            { group_id: "group-2", message_count: 4 },
          ],
        }));
      }
      if (path === "page/affection/status" && params.group_id === "group-2") {
        return Promise.resolve(ok({
          group_id: "group-2",
          total_affection: 1,
          max_total_affection: 10,
          user_count: 1,
          current_mood: {
            mood_type: "CURIOUS",
            intensity: 0.4,
            description: "Curious about a new topic.",
            is_active: true,
          },
          top_users: [],
        }));
      }
      return Promise.resolve(ok({
        group_id: "group-1",
        total_affection: 0,
        max_total_affection: 10,
        user_count: 0,
        current_mood: {
          mood_type: "CALM",
          intensity: 0.1,
          description: "Calm baseline.",
          is_active: true,
        },
        top_users: [],
      }));
    });

    render(<AffectionPage showToast={showToast} />);

    expect(await screen.findByText("Calm baseline.")).toBeTruthy();

    const hiddenSelectInput = document.querySelector('input[aria-hidden="true"]') as HTMLInputElement | null;
    if (!hiddenSelectInput) throw new Error("expected hidden group select input");
    fireEvent.change(hiddenSelectInput, { target: { value: "group-2" } });

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/affection/status", { group_id: "group-2" });
    });
    expect(await screen.findByText("Curious about a new topic.")).toBeTruthy();
  });
});
