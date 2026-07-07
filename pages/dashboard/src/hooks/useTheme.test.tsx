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
      <span data-testid="theme">{theme}</span>
      <button onClick={toggleTheme}>toggle</button>
    </div>
  );
}

describe("useTheme", () => {
  let bridge: BridgeMock;

  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.className = "";

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
    expect(localStorage.getItem("memora_theme")).toBe("dark");
  });

  it("toggles theme and persists the new value", () => {
    render(<Harness />);

    fireEvent.click(screen.getByText("toggle"));

    expect(screen.getByTestId("theme").textContent).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem("memora_theme")).toBe("dark");
  });

  it("reacts to DOM attribute changes observed on documentElement", async () => {
    render(<Harness />);

    await act(async () => {
      document.documentElement.setAttribute("data-theme", "dark");
    });

    expect(screen.getByTestId("theme").textContent).toBe("dark");
  });

  it("reacts to bridge context-change callbacks and unregisters on cleanup", async () => {
    let contextHandler: ((ctx: { isDark?: boolean }) => void) | undefined;
    bridge.onContextChange.mockImplementation((handler) => {
      contextHandler = handler;
    });

    const view = render(<Harness />);

    await act(async () => {
      contextHandler?.({ isDark: true });
    });

    expect(screen.getByTestId("theme").textContent).toBe("dark");

    view.unmount();

    expect(bridge.offContextChange).toHaveBeenCalledTimes(1);
    expect(bridge.offContextChange).toHaveBeenCalledWith(expect.any(Function));
  });
});
