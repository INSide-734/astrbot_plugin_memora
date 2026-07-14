import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TimelinePage } from "./TimelinePage";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost?: ReturnType<typeof vi.fn>;
  getLocale?: ReturnType<typeof vi.fn>;
  getI18n?: ReturnType<typeof vi.fn>;
  t?: ReturnType<typeof vi.fn>;
}

function ok<T>(data: T) {
  return { status: "ok", data };
}

describe("TimelinePage", () => {
  let bridge: BridgeMock;
  let showToast: ReturnType<typeof vi.fn>;
  let dateNowSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    dateNowSpy = vi.spyOn(Date, "now").mockReturnValue(new Date("2026-06-28T12:00:00Z").getTime());

    bridge = {
      apiGet: vi.fn(),
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
    dateNowSpy.mockRestore();
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  it("loads memories, applies the default week filter, and renders the filtered count", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      items: [
        {
          id: "mem-newest",
          content: "Newest memory in range",
          importance: 0.82,
          type: "fact",
          created_at: "2026-06-28T10:00:00Z",
        },
        {
          id: "mem-week",
          summary: "Still in the last week",
          importance: 0.45,
          type: "note",
          created_at: "2026-06-24T08:00:00Z",
        },
        {
          id: "mem-old",
          content: "Older than the default week filter",
          importance: 0.12,
          type: "summary",
          created_at: "2026-06-10T08:00:00Z",
        },
      ],
    }));

    const { container } = render(<TimelinePage showToast={showToast} />);

    expect(container.querySelector('[data-slot="page-frame"]')?.getAttribute("data-layout")).toBe("standard");
    expect(screen.getByRole("heading", { level: 1, name: "Timeline" })).toBeTruthy();

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/memories", { page_size: "200" });
    });

    expect(await screen.findByText("Newest memory in range")).toBeTruthy();
    expect(screen.getByText("Still in the last week")).toBeTruthy();
    expect(screen.queryByText("Older than the default week filter")).toBeNull();
    expect(screen.getByText("2 items")).toBeTruthy();
    expect(screen.getAllByText("Week").length).toBeGreaterThan(0);
  });

  it("switches zoom levels and toggles the expanded memory detail panel", async () => {
    bridge.t?.mockImplementation((key: string) => key === "dashboard.memory.type.fact" ? "Fact memory" : key);
    bridge.apiGet.mockResolvedValue(ok({
      items: [
        {
          id: "mem-day",
          content: "Within the day window",
          importance: 0.91,
          type: "fact",
          created_at: "2026-06-28T09:30:00Z",
        },
        {
          id: "mem-month",
          summary: "Only visible in the month window",
          importance: 0.38,
          type: "summary",
          created_at: "2026-06-03T12:00:00Z",
        },
      ],
    }));

    render(<TimelinePage showToast={showToast} />);

    expect(await screen.findByText("Within the day window")).toBeTruthy();
    expect(screen.queryByText("Only visible in the month window")).toBeNull();
    const dayMemory = screen.getByRole("button", { name: /Within the day window/ });
    const detailId = dayMemory.getAttribute("aria-controls");
    expect(dayMemory.getAttribute("aria-expanded")).toBe("false");
    expect(detailId).toBe("timeline-detail-mem-day");

    fireEvent.click(screen.getByRole("button", { name: "Month" }));

    expect(await screen.findByText("Only visible in the month window")).toBeTruthy();
    expect(screen.getByText("2 items")).toBeTruthy();

    fireEvent.click(dayMemory);

    expect(await screen.findByText("0.91")).toBeTruthy();
    expect(screen.getByText("Fact memory")).toBeTruthy();
    expect(dayMemory.getAttribute("aria-expanded")).toBe("true");
    expect(dayMemory.className).toContain(
      "shadow-[inset_2px_0_0_var(--selection-indicator)]",
    );
    expect(document.getElementById(detailId ?? "")).toBeTruthy();

    fireEvent.click(dayMemory);

    await waitFor(() => {
      expect(screen.queryByText("0.91")).toBeNull();
    });
    expect(dayMemory.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(screen.getByRole("button", { name: "Day" }));

    await waitFor(() => {
      expect(screen.queryByText("Only visible in the month window")).toBeNull();
    });
    expect(screen.getByText("1 items")).toBeTruthy();
  });

  it("uses collision-free controls ids for punctuated memory identifiers", async () => {
    bridge.apiGet.mockResolvedValue(ok({
      items: [
        {
          id: "memory/a",
          content: "Slash memory",
          importance: 0.7,
          type: "fact",
          created_at: "2026-06-28T10:00:00Z",
        },
        {
          id: "memory?a",
          content: "Query memory",
          importance: 0.6,
          type: "fact",
          created_at: "2026-06-28T09:00:00Z",
        },
      ],
    }));

    render(<TimelinePage showToast={showToast} />);

    const slashButton = await screen.findByRole("button", { name: /Slash memory/ });
    const queryButton = screen.getByRole("button", { name: /Query memory/ });
    const slashDetailId = slashButton.getAttribute("aria-controls");
    const queryDetailId = queryButton.getAttribute("aria-controls");

    expect(slashDetailId).not.toBe(queryDetailId);

    fireEvent.click(slashButton);
    expect(slashButton.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById(slashDetailId ?? "")).toBeTruthy();

    fireEvent.click(queryButton);
    expect(slashButton.getAttribute("aria-expanded")).toBe("false");
    expect(queryButton.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById(slashDetailId ?? "")).toBeNull();
    expect(document.getElementById(queryDetailId ?? "")).toBeTruthy();
  });

  it("shows a toast and falls back to the empty state when memory loading fails", async () => {
    bridge.apiGet.mockRejectedValue(new Error("backend offline"));

    render(<TimelinePage showToast={showToast} />);

    await waitFor(() => {
      expect(bridge.apiGet).toHaveBeenCalledWith("page/memories", { page_size: "200" });
    });

    expect(showToast).toHaveBeenCalledWith("Failed to load memories", true);
    expect(await screen.findByText("No memories in this time range")).toBeTruthy();
  });
});
