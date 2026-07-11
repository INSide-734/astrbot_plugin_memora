import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProfilesPage } from "./ProfilesPage";

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

describe("ProfilesPage", () => {
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

  it("loads profile statistics and renders aggregated tag totals", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      total: 2,
      profiles: [
        {
          user_id: "user-1",
          display_name: "Alice",
          tag_count: 3,
          top_interests: ["testing", "python"],
          last_seen: "2026-06-28T12:00:00Z",
        },
        {
          user_id: "user-2",
          display_name: "Bob",
          tags: [
            { name: "ops", confidence: 0.8 },
            { name: "graphs", confidence: 0.5 },
          ],
          top_interests: ["ops"],
          last_seen: "2026-06-27T12:00:00Z",
        },
      ],
    }));

    render(<ProfilesPage showToast={showToast} />);

    expect(screen.getByRole("region").getAttribute("data-layout")).toBe("dense");

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/profiles", { limit: "100", offset: "0" });
    });

    expect(await screen.findByText("Alice")).toBeTruthy();
    expect(screen.getByText("Bob")).toBeTruthy();
    expect(screen.getByText("Profiles")).toBeTruthy();
    expect(screen.getAllByText("Tags").length).toBeGreaterThan(1);
    expect(screen.getByText("User ID")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Tags" })).toBeTruthy();
    expect(screen.getAllByText("2").length).toBeGreaterThan(0);
    expect(screen.getByText((content) => content.trim() === "5")).toBeTruthy();
    expect(screen.getByText("testing")).toBeTruthy();
    expect(screen.getByText("2026-06-28")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Profiles pagination" })).toBeTruthy();
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("pages through profile results using backend offsets", async () => {
    bridge.apiGet.mockImplementation((_path: string, params: Record<string, string>) => Promise.resolve(ok({
      total: 201,
      profiles: [{
        user_id: `user-${params.offset}`,
        display_name: `Offset ${params.offset}`,
      }],
    })));

    render(<ProfilesPage showToast={showToast} />);

    expect(await screen.findByText("Offset 0")).toBeTruthy();
    expect(screen.getByText("Page 1 of 3")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select profile Offset 0" }));
    expect(screen.getByText("1 selected")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));

    expect(screen.queryByText("1 selected")).toBeNull();

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/profiles", { limit: "100", offset: "100" });
    });
    expect(await screen.findByText("Offset 100")).toBeTruthy();
    expect(screen.getByText("Page 2 of 3")).toBeTruthy();
  });

  it("returns to the previous valid page when deletion empties the current page", async () => {
    let offsetPageReads = 0;
    let deleted = false;
    bridge.apiGet.mockImplementation((_path: string, params: Record<string, string>) => {
      if (params.offset === "100") {
        offsetPageReads += 1;
        return Promise.resolve(ok(offsetPageReads === 1
          ? { total: 101, profiles: [{ user_id: "user-last", display_name: "Last profile" }] }
          : { total: 100, profiles: [] }));
      }
      return Promise.resolve(ok({ total: deleted ? 100 : 101, profiles: [{ user_id: "user-first", display_name: "First profile" }] }));
    });
    bridge.apiPost.mockImplementation(() => { deleted = true; return Promise.resolve(ok({})); });

    render(<ProfilesPage showToast={showToast} />);
    expect(await screen.findByText("First profile")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Last profile")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select profile Last profile" }));
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(screen.getByText("Page 1 of 1")).toBeTruthy();
      expect(screen.getByText("First profile")).toBeTruthy();
    });
    expect(screen.queryByText("1 selected")).toBeNull();
  });

  it("shows the batch bar and deletes selected profiles", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      total: 2,
      profiles: [
        {
          user_id: "user-1",
          display_name: "Alice",
          tag_count: 2,
          top_interests: ["testing"],
          last_seen: "2026-06-28T12:00:00Z",
        },
        {
          user_id: "user-2",
          display_name: "Bob",
          tag_count: 1,
          top_interests: ["ops"],
          last_seen: "2026-06-27T12:00:00Z",
        },
      ],
    }));
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<ProfilesPage showToast={showToast} />);

    expect(await screen.findByText("Alice")).toBeTruthy();
    expect(screen.getByText("Bob")).toBeTruthy();

    fireEvent.click(screen.getByRole("checkbox", { name: "Select profile Alice" }));

    await waitFor(() => {
      expect(screen.getByText("1 selected")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("checkbox", { name: "Select profile Bob" }));

    await waitFor(() => {
      expect(screen.getByText("2 selected")).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/profiles/batch", {
        user_ids: ["user-1", "user-2"],
        action: "delete",
      });
    });
    expect(showToast).toHaveBeenCalledWith("Deleted 2 profiles");
  });

  it("opens profile detail and deletes the profile from the side panel", async () => {
    bridge.apiGet.mockImplementation((path: string, params: Record<string, string>) => {
      if (path === "page/profiles") {
        return Promise.resolve(ok({
          total: 1,
          profiles: [
            {
              user_id: "user-9",
              display_name: "Gamma",
              tag_count: 2,
              top_interests: ["graphs"],
              last_seen: "2026-06-28T12:00:00Z",
            },
          ],
        }));
      }
      if (path === "page/profiles/detail") {
        return Promise.resolve(ok({
          profile: {
            user_id: params.user_id,
            display_name: "Gamma",
            message_count: 12,
            tags: [
              { name: "graphs", confidence: 0.92 },
              { name: "testing", confidence: 0.61 },
            ],
          },
        }));
      }
      return Promise.resolve(ok({}));
    });
    bridge.apiPost.mockResolvedValue(ok({}));

    render(<ProfilesPage showToast={showToast} />);

    fireEvent.click(await screen.findByRole("button", { name: "Open profile Gamma" }));

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/profiles/detail", { user_id: "user-9" });
    });

    const drawer = await screen.findByRole("dialog", { name: "Profile: Gamma" });

    expect(within(drawer).getByText("user-9")).toBeTruthy();
    expect(within(drawer).getByText("12")).toBeTruthy();
    expect(within(drawer).getByText("graphs")).toBeTruthy();
    expect(within(drawer).getByText("92%")).toBeTruthy();

    fireEvent.click(within(drawer).getByRole("button", { name: /delete profile/i }));

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/profiles/delete", {
        user_id: "user-9",
      });
    });
    expect(showToast).toHaveBeenCalledWith("Profile deleted");
  });
});
