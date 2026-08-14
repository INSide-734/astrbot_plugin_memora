import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Sidebar } from "./Sidebar";

interface BridgeMock {
  getLocale?: ReturnType<typeof vi.fn>;
  getI18n?: ReturnType<typeof vi.fn>;
  t?: ReturnType<typeof vi.fn>;
}

function renderSidebar(overrides: Partial<React.ComponentProps<typeof Sidebar>> = {}) {
  const props: React.ComponentProps<typeof Sidebar> = {
    currentPage: "graph",
    onNavigate: vi.fn(),
    theme: "light",
    onToggleTheme: vi.fn(),
    onCycleLanguage: vi.fn(),
    mobileOpen: true,
    onCloseMobile: vi.fn(),
    sseConnected: true,
    unreadCount: 3,
    lastEvent: {
      event: "memory_created",
      data: { content: "hello world from stream" },
      ts: 1,
    },
    onMarkSeen: vi.fn(),
    ...overrides,
  };

  return {
    ...render(<Sidebar {...props} />),
    props,
  };
}

describe("Sidebar", () => {
  beforeEach(() => {
    const bridge: BridgeMock = {
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
    };

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

  it("navigates when a nav item is clicked", () => {
    const { props } = renderSidebar();

    fireEvent.click(screen.getByText("Knowledge Base"));

    expect(props.onNavigate).toHaveBeenCalledWith("knowledge");
  });

  it("uses the shared navigation selection treatment for the current page", () => {
    renderSidebar({ currentPage: "graph" });

    const currentItem = screen.getByRole("button", { name: "Knowledge Graph" });
    const otherItem = screen.getByRole("button", { name: "Memories" });

    expect(currentItem.getAttribute("aria-current")).toBe("page");
    expect(currentItem.classList.contains("border-primary")).toBe(true);
    expect(currentItem.classList.contains("bg-primary")).toBe(true);
    expect(currentItem.classList.contains("text-primary-foreground")).toBe(true);
    expect(otherItem.hasAttribute("aria-current")).toBe(false);
    expect(otherItem.classList.contains("border-primary")).toBe(false);
  });

  it("marks unread realtime events as seen when the badge is clicked", () => {
    const { props } = renderSidebar();

    fireEvent.click(screen.getByText("3"));

    expect(props.onMarkSeen).toHaveBeenCalledTimes(1);
  });

  it("invokes theme and language callbacks from footer actions", () => {
    const { props } = renderSidebar();

    fireEvent.click(screen.getByText("Theme"));
    fireEvent.click(screen.getByText("Language"));

    expect(props.onToggleTheme).toHaveBeenCalledTimes(1);
    expect(props.onCycleLanguage).toHaveBeenCalledTimes(1);
  });

  it("crossfades both theme icons inside a stable motion-safe slot", () => {
    const { props, rerender } = renderSidebar({ theme: "light" });
    const themeButton = screen.getByRole("button", { name: "Theme" });
    const iconSlot = themeButton.querySelector('[data-slot="theme-icon"]');
    const sun = themeButton.querySelector('[data-theme-icon="sun"]');
    const moon = themeButton.querySelector('[data-theme-icon="moon"]');

    expect(iconSlot).not.toBeNull();
    expect(sun).not.toBeNull();
    expect(moon).not.toBeNull();
    expect(iconSlot?.getAttribute("class") ?? "").toContain("size-4");
    expect(iconSlot?.getAttribute("class") ?? "").toContain("relative");
    expect(sun?.getAttribute("class") ?? "").toContain("opacity-0");
    expect(moon?.getAttribute("class") ?? "").toContain("opacity-100");
    expect(sun?.getAttribute("class") ?? "").toContain("duration-200");
    expect(sun?.getAttribute("class") ?? "").toContain("motion-reduce:transition-none");

    rerender(<Sidebar {...props} theme="dark" />);

    expect(sun?.getAttribute("class") ?? "").toContain("opacity-100");
    expect(moon?.getAttribute("class") ?? "").toContain("opacity-0");
  });

  it("closes the mobile menu from the close button and backdrop", () => {
    const { props, container } = renderSidebar();

    fireEvent.click(container.querySelector(".bg-black\\/30") as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "Close navigation" }));

    expect(props.onCloseMobile).toHaveBeenCalledTimes(2);
  });

  it("renders the latest stream event preview when connected", () => {
    renderSidebar();

    expect(screen.getByText(/memory_created:/)).toBeTruthy();
    expect(screen.getByText(/hello world from stream/)).toBeTruthy();
  });

  it("organizes routes into five collapsible navigation groups", () => {
    renderSidebar();

    expect(screen.getAllByRole("button", { expanded: true })).toHaveLength(5);
    expect(screen.getByRole("button", { name: "Overview", expanded: true })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Memory", expanded: true })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Insights", expanded: true })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Relationships", expanded: true })).toBeTruthy();
    expect(screen.getByRole("button", { name: "System", expanded: true })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Relationships", expanded: true }));
    expect(screen.queryByRole("button", { name: "User Profiles" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Affection" })).toBeNull();
  });

  it("keeps System and localized Configuration entries in order", () => {
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: {
        getLocale: vi.fn().mockReturnValue("ru-RU"),
        getI18n: vi.fn().mockReturnValue({}),
        t: vi.fn((key: string) => key),
      },
    });
    renderSidebar();

    const systemGroup = screen.getByRole("button", {
      name: "Система",
      expanded: true,
    });
    const groupId = systemGroup.getAttribute("aria-controls");
    const group = document.getElementById(groupId ?? "");
    expect(group).not.toBeNull();
    expect(
      within(group as HTMLElement)
        .getAllByRole("button")
        .map((button) => button.getAttribute("aria-label")),
    ).toEqual(["Система", "Конфигурация", "Ворота"]);
  });

  it("navigates to config through the shared onNavigate contract", () => {
    const { props } = renderSidebar();

    fireEvent.click(screen.getByRole("button", { name: "Configuration" }));

    expect(props.onNavigate).toHaveBeenCalledWith("config");
  });

  it("collapses the desktop sidebar to labelled icon navigation", () => {
    const { container } = renderSidebar();

    fireEvent.click(screen.getByRole("button", { name: "Collapse navigation" }));

    expect(container.querySelector("aside")?.getAttribute("data-collapsed")).toBe("true");
    expect(screen.getByRole("button", { name: "Expand navigation" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Knowledge Graph" })).toBeTruthy();
  });
});
