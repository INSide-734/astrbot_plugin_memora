import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useTheme } from "./useTheme";

interface BridgeMock {
  getContext: ReturnType<typeof vi.fn>;
  onContextChange: ReturnType<typeof vi.fn>;
  offContextChange: ReturnType<typeof vi.fn>;
}

function Harness() {
  const { theme, toggleTheme } = useTheme();

  return (
    <div>
      <aside data-testid="sidebar-surface" />
      <main data-testid="main-surface">
        <span data-testid="theme">{theme}</span>
        <button onClick={toggleTheme}>toggle</button>
      </main>
    </div>
  );
}

function dispatchThemeTokenTransitionEnd(propertyName: string) {
  const event = new Event("transitionend", { bubbles: true });
  Object.defineProperty(event, "propertyName", {
    configurable: true,
    value: propertyName,
  });
  fireEvent(document.documentElement, event);
}

function setReducedMotion(matches: boolean) {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({
    matches,
    media: "(prefers-reduced-motion: reduce)",
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

describe("useTheme", () => {
  let bridge: BridgeMock;
  let animationFrames: Map<number, FrameRequestCallback>;
  let nextAnimationFrameId: number;

  function flushNextAnimationFrame() {
    const next = animationFrames.entries().next().value as
      | [number, FrameRequestCallback]
      | undefined;
    expect(next).toBeTruthy();
    if (!next) return;
    animationFrames.delete(next[0]);
    act(() => next[1](performance.now()));
  }

  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.className = "";

    animationFrames = new Map();
    nextAnimationFrameId = 1;
    vi.stubGlobal("requestAnimationFrame", vi.fn((callback: FrameRequestCallback) => {
      const id = nextAnimationFrameId++;
      animationFrames.set(id, callback);
      return id;
    }));
    vi.stubGlobal("cancelAnimationFrame", vi.fn((id: number) => {
      animationFrames.delete(id);
    }));
    setReducedMotion(false);

    bridge = {
      getContext: vi.fn().mockReturnValue({ isDark: false }),
      onContextChange: vi.fn(),
      offContextChange: vi.fn(),
    };

    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: bridge,
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.className = "";
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  it("initializes from bridge context and syncs the DOM theme", () => {
    bridge.getContext.mockReturnValue({ isDark: true });

    render(<Harness />);

    expect(screen.getByTestId("theme").textContent).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem("lmem_theme")).toBe("dark");
    expect(localStorage.getItem("lmem_theme_override")).toBeNull();
  });

  it("gives a persisted manual override priority over the bridge theme", () => {
    localStorage.setItem("lmem_theme", "dark");
    localStorage.setItem("lmem_theme_override", "1");
    bridge.getContext.mockReturnValue({ isDark: false });

    render(<Harness />);

    expect(screen.getByTestId("theme").textContent).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("keeps theme transitions active until the root background tokens finish", () => {
    render(<Harness />);

    fireEvent.click(screen.getByText("toggle"));

    expect(document.documentElement.classList.contains("theme-transitioning")).toBe(true);
    expect(screen.getByTestId("theme").textContent).toBe("light");

    flushNextAnimationFrame();

    expect(screen.getByTestId("theme").textContent).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem("lmem_theme")).toBe("dark");
    expect(localStorage.getItem("lmem_theme_override")).toBe("1");

    act(() => vi.advanceTimersByTime(220));
    expect(document.documentElement.classList.contains("theme-transitioning")).toBe(true);

    flushNextAnimationFrame();

    act(() => vi.advanceTimersByTime(219));
    expect(document.documentElement.classList.contains("theme-transitioning")).toBe(true);

    dispatchThemeTokenTransitionEnd("--background");
    expect(document.documentElement.classList.contains("theme-transitioning")).toBe(true);

    dispatchThemeTokenTransitionEnd("--sidebar");
    expect(document.documentElement.classList.contains("theme-transitioning")).toBe(true);

    flushNextAnimationFrame();
    expect(document.documentElement.classList.contains("theme-transitioning")).toBe(false);
  });

  it("falls back to timed cleanup when transition events are unavailable", () => {
    render(<Harness />);

    fireEvent.click(screen.getByText("toggle"));
    flushNextAnimationFrame();
    flushNextAnimationFrame();

    act(() => vi.advanceTimersByTime(219));
    expect(document.documentElement.classList.contains("theme-transitioning")).toBe(true);
    act(() => vi.advanceTimersByTime(1));
    expect(document.documentElement.classList.contains("theme-transitioning")).toBe(true);

    flushNextAnimationFrame();
    expect(document.documentElement.classList.contains("theme-transitioning")).toBe(false);
  });

  it("reverses rapid toggles and clears pending animation work on unmount", () => {
    const view = render(<Harness />);

    fireEvent.click(screen.getByText("toggle"));
    flushNextAnimationFrame();
    expect(screen.getByTestId("theme").textContent).toBe("dark");

    fireEvent.click(screen.getByText("toggle"));
    flushNextAnimationFrame();
    expect(screen.getByTestId("theme").textContent).toBe("light");

    view.unmount();

    expect(document.documentElement.classList.contains("theme-transitioning")).toBe(false);
    expect(animationFrames.size).toBe(0);
  });

  it("switches immediately when reduced motion is requested", () => {
    setReducedMotion(true);
    render(<Harness />);

    fireEvent.click(screen.getByText("toggle"));

    expect(screen.getByTestId("theme").textContent).toBe("dark");
    expect(document.documentElement.classList.contains("theme-transitioning")).toBe(false);
    expect(animationFrames.size).toBe(0);
    expect(localStorage.getItem("lmem_theme_override")).toBe("1");
  });

  it("reacts to DOM attribute changes observed on documentElement", async () => {
    render(<Harness />);

    await act(async () => {
      document.documentElement.setAttribute("data-theme", "dark");
    });

    expect(screen.getByTestId("theme").textContent).toBe("dark");
  });

  it("treats a class-only host mutation as the latest theme signal", async () => {
    bridge.getContext.mockReturnValue({ isDark: true });
    render(<Harness />);
    expect(screen.getByTestId("theme").textContent).toBe("dark");

    await act(async () => {
      document.documentElement.classList.remove("dark");
    });

    expect(screen.getByTestId("theme").textContent).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("ignores bridge theme changes after a manual override and unregisters on cleanup", async () => {
    let contextHandler: ((ctx: { isDark?: boolean }) => void) | undefined;
    bridge.onContextChange.mockImplementation((handler) => {
      contextHandler = handler;
    });

    const view = render(<Harness />);

    fireEvent.click(screen.getByText("toggle"));
    flushNextAnimationFrame();
    expect(screen.getByTestId("theme").textContent).toBe("dark");

    await act(async () => {
      contextHandler?.({ isDark: false });
    });

    expect(screen.getByTestId("theme").textContent).toBe("dark");

    view.unmount();

    expect(bridge.offContextChange).toHaveBeenCalledTimes(1);
    expect(bridge.offContextChange).toHaveBeenCalledWith(expect.any(Function));
  });
});
