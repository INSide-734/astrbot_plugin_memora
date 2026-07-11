import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SocialPage } from "./SocialPage";

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

describe("SocialPage", () => {
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

  it("loads social relations for the first group and renders relation details", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [{ group_id: "group-1", message_count: 12 }],
        }));
      }
      if (path === "page/social/relations") {
        expect(params).toEqual({ group_id: "group-1" });
        return Promise.resolve(ok({
          relations: [
            {
              from_user: "alice",
              to_user: "bob",
              relation_type: "friend",
              strength: 0.76,
              frequency: 9,
              last_interaction: 1,
              group_id: "group-1",
              tags: ["pair", "project"],
              category: "emotional",
            },
          ],
        }));
      }
      return Promise.resolve(ok({}));
    });

    render(<SocialPage showToast={showToast} />);

    const page = screen.getByRole("region", { name: /Social|社交/ });
    expect(page.getAttribute("data-layout")).toBe("standard");
    expect(page.querySelector('[data-slot="page-header"]')).toBeTruthy();
    expect(screen.getByRole("tablist", { name: /categor/i })).toBeTruthy();
    expect(screen.getByRole("tab", { name: /all/i }).getAttribute("aria-selected")).toBe("true");

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/social/relations", { group_id: "group-1" });
    });

    expect(await screen.findByText("alice")).toBeTruthy();
    expect(screen.getByText("bob")).toBeTruthy();
    expect(screen.getByText("friend")).toBeTruthy();
    expect(screen.getAllByText("情感").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("76%")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: /alice to bob.*strength/i })).toBeTruthy();
    expect(screen.getByText("pair")).toBeTruthy();
    expect(screen.getByText("project")).toBeTruthy();
  });

  it("filters by category and refetches with the selected category", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [{ group_id: "group-1", message_count: 12 }],
        }));
      }
      if (path === "page/social/relations" && params.category === "career") {
        return Promise.resolve(ok({
          relations: [
            {
              from_user: "mentor",
              to_user: "student",
              relation_type: "mentor",
              strength: 0.88,
              frequency: 4,
              last_interaction: 2,
              group_id: "group-1",
              tags: ["work"],
              category: "career",
            },
          ],
        }));
      }
      if (path === "page/social/relations") {
        return Promise.resolve(ok({ relations: [] }));
      }
      return Promise.resolve(ok({}));
    });

    render(<SocialPage showToast={showToast} />);

    expect(await screen.findByText("No relations found")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "职业" }));

    expect(screen.getByRole("tab", { name: "职业" }).getAttribute("aria-selected")).toBe("true");

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/social/relations", {
        group_id: "group-1",
        category: "career",
      });
    });
    expect((await screen.findAllByText("mentor")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("student")).toBeTruthy();
    expect(screen.getByText("88%")).toBeTruthy();
  });

  it("shows an empty state and sends relation fetch errors to the toast handler", async () => {
    bridge.apiGet.mockImplementation((path: string) => {
      if (path === "page/groups") {
        return Promise.resolve(ok({
          groups: [{ group_id: "group-1", message_count: 12 }],
        }));
      }
      if (path === "page/social/relations") {
        return Promise.reject(new Error("relations unavailable"));
      }
      return Promise.resolve(ok({}));
    });

    render(<SocialPage showToast={showToast} />);

    await waitFor(() => {
      expect(showToast).toHaveBeenCalledWith("Error: relations unavailable", true);
    });
    expect(screen.getByText("No relations found")).toBeTruthy();
  });
});
