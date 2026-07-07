import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

  it("closes the mobile menu from the close button and backdrop", () => {
    const { props, container } = renderSidebar();

    fireEvent.click(container.querySelector(".bg-black\\/30") as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: "" }));

    expect(props.onCloseMobile).toHaveBeenCalledTimes(2);
  });

  it("renders the latest stream event preview when connected", () => {
    renderSidebar();

    expect(screen.getByText(/memory_created:/)).toBeTruthy();
    expect(screen.getByText(/hello world from stream/)).toBeTruthy();
  });
});
