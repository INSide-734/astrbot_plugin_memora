import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/pages/GraphPage", () => ({
  GraphPage: () => <div>Graph Page</div>,
}));
vi.mock("@/pages/MemoryPage", () => ({
  MemoryPage: () => <div>Memory Page</div>,
}));
vi.mock("@/pages/RecallPage", () => ({
  RecallPage: () => <div>Recall Page</div>,
}));
vi.mock("@/pages/SystemPage", () => ({
  SystemPage: () => <div>System Page</div>,
}));
vi.mock("@/pages/ConfigPage", async () => {
  const React = await vi.importActual<typeof import("react")>("react");

  return {
    ConfigPage: ({
      onDirtyChange,
      showToast,
    }: {
      onDirtyChange?: (dirty: boolean) => void;
      showToast?: (
        message: string,
        type?: "success" | "error" | "info",
      ) => void;
    }) => {
      const initialDirtyCallback = React.useRef(onDirtyChange);
      return (
        <div>
          <p>Config Page</p>
          <span data-testid="config-dirty-callback-stability">
            {initialDirtyCallback.current === onDirtyChange
              ? "stable"
              : "changed"}
          </span>
          <button
            type="button"
            data-testid="make-config-dirty"
            onClick={() => onDirtyChange?.(true)}
          >
            Make config dirty
          </button>
          <button
            type="button"
            data-testid="make-config-clean"
            onClick={() => onDirtyChange?.(false)}
          >
            Mark config clean
          </button>
          <button
            type="button"
            onClick={() => showToast?.("Config toast", "error")}
          >
            Trigger config toast
          </button>
        </div>
      );
    },
  };
});
vi.mock("@/pages/ProfilesPage", () => ({
  ProfilesPage: () => <div>Profiles Page</div>,
}));
vi.mock("@/pages/KnowledgePage", () => ({
  KnowledgePage: () => <div>Knowledge Page</div>,
}));
vi.mock("@/pages/NotesPage", () => ({
  NotesPage: () => <div>Notes Page</div>,
}));
vi.mock("@/pages/LearningPage", () => ({
  LearningPage: () => <div>Learning Page</div>,
}));
vi.mock("@/pages/PreviewPage", () => ({
  PreviewPage: () => <div>Preview Page</div>,
}));
vi.mock("@/pages/TimelinePage", () => ({
  TimelinePage: () => <div>Timeline Page</div>,
}));
vi.mock("@/pages/JargonPage", () => ({
  JargonPage: () => <div>Jargon Page</div>,
}));
vi.mock("@/pages/AffectionPage", () => ({
  AffectionPage: () => <div>Affection Page</div>,
}));
vi.mock("@/pages/SocialPage", () => ({
  SocialPage: () => <div>Social Page</div>,
}));
vi.mock("@/pages/IntelligencePage", () => ({
  IntelligencePage: () => <div>Intelligence Page</div>,
}));

import App from "./App";

interface BridgeMock {
  getLocale?: ReturnType<typeof vi.fn>;
  getI18n?: ReturnType<typeof vi.fn>;
  t?: ReturnType<typeof vi.fn>;
  onContextChange?: ReturnType<typeof vi.fn>;
  offContextChange?: ReturnType<typeof vi.fn>;
  subscribeSSE?: ReturnType<typeof vi.fn>;
  unsubscribeSSE?: ReturnType<typeof vi.fn>;
  apiGet?: ReturnType<typeof vi.fn>;
}

describe("App", () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.replaceState({}, "", "#/graph");

    const bridge: BridgeMock = {
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
      onContextChange: vi.fn(),
      offContextChange: vi.fn(),
      subscribeSSE: vi.fn().mockReturnValue("sub-1"),
      unsubscribeSSE: vi.fn(),
      apiGet: vi.fn((path: string) => Promise.resolve({
        status: "ok",
        data: path === "page/memories"
          ? [{ id: "memory-1", content: "Search memory result", importance: 0.8 }]
          : [],
      })),
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
    localStorage.clear();
    window.history.replaceState({}, "", window.location.pathname);
  });

  it("renders the page selected by the current hash", async () => {
    window.location.hash = "#/knowledge";

    render(<App />);

    expect(await screen.findByText("Knowledge Page")).toBeTruthy();
  });

  it("renders the lazy configuration route and localized header from #/config", async () => {
    window.location.hash = "#/config";

    const { container } = render(<App />);

    expect(await screen.findByText("Config Page")).toBeTruthy();
    const header = container.querySelector('[data-slot="app-header"]') as HTMLElement;
    expect(within(header).getByText("Configuration")).toBeTruthy();
  });

  it("passes a stable dirty callback and compatible toast command to ConfigPage", async () => {
    window.location.hash = "#/config";
    render(<App />);

    expect(await screen.findByText("Config Page")).toBeTruthy();
    expect(screen.getByTestId("config-dirty-callback-stability").textContent).toBe("stable");

    fireEvent.click(screen.getByLabelText("Open menu"));
    await waitFor(() => {
      expect(screen.getByTestId("config-dirty-callback-stability").textContent).toBe("stable");
    });

    fireEvent.click(screen.getByRole("button", { name: "Trigger config toast" }));
    const toast = screen.getByRole("alert");
    expect(toast.textContent).toBe("Config toast");
    expect(toast.className).toContain("color-danger");
  });

  it("renders a shared application header with global search and connection state", async () => {
    const { container } = render(<App />);

    expect(await screen.findByText("Graph Page")).toBeTruthy();
    const header = container.querySelector('[data-slot="app-header"]') as HTMLElement;
    expect(header).toBeTruthy();
    expect(screen.getByRole("button", { name: /search/i })).toBeTruthy();
    expect(within(header).getByText(/live|实时|offline|离线/i)).toBeTruthy();
  });

  it("updates the rendered page after a hashchange event", async () => {
    render(<App />);

    expect(await screen.findByText("Graph Page")).toBeTruthy();

    window.location.hash = "#/social";
    window.dispatchEvent(new HashChangeEvent("hashchange"));

    expect(await screen.findByText("Social Page")).toBeTruthy();
  });

  it("opens the mobile menu button and navigates to the selected page", async () => {
    render(<App />);

    fireEvent.click(screen.getByLabelText("Open menu"));
    fireEvent.click(await screen.findByText("Notes"));

    await waitFor(() => {
      expect(window.location.hash).toBe("#/notes");
    });
    expect(await screen.findByText("Notes Page")).toBeTruthy();
  });

  it("navigates away from a clean configuration page immediately", async () => {
    window.history.replaceState({}, "", "#/config");
    render(<App />);
    expect(await screen.findByText("Config Page")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Notes" }));

    await waitFor(() => expect(window.location.hash).toBe("#/notes"));
    expect(await screen.findByText("Notes Page")).toBeTruthy();
    expect(screen.queryByRole("dialog", {
      name: "Leave configuration without saving?",
    })).toBeNull();
  });

  it("blocks dirty Sidebar navigation and keeps the visible config hash", async () => {
    window.history.replaceState({}, "", "#/config");
    render(<App />);
    expect(await screen.findByText("Config Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-config-dirty"));

    fireEvent.click(screen.getByRole("button", { name: "Notes" }));

    expect(await screen.findByRole("dialog", {
      name: "Leave configuration without saving?",
    })).toBeTruthy();
    expect(window.location.hash).toBe("#/config");
    expect(screen.getByText("Config Page")).toBeTruthy();
  });

  it("uses the same dirty guard for global search result navigation", async () => {
    window.history.replaceState({}, "", "#/config");
    render(<App />);
    expect(await screen.findByText("Config Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-config-dirty"));

    fireEvent.click(screen.getByRole("button", { name: "Search..." }));
    fireEvent.change(
      screen.getByPlaceholderText("Search memories, knowledge, notes..."),
      { target: { value: "memory" } },
    );
    fireEvent.click(await screen.findByText("Search memory result"));

    expect(await screen.findByRole("dialog", {
      name: "Leave configuration without saving?",
    })).toBeTruthy();
    expect(window.location.hash).toBe("#/config");
  });

  it("captures dirty direct hash navigation and synchronously restores #/config", async () => {
    window.history.replaceState({}, "", "#/config");
    render(<App />);
    expect(await screen.findByText("Config Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-config-dirty"));

    act(() => {
      window.location.hash = "#/memory";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });

    expect(window.location.hash).toBe("#/config");
    expect(screen.getByRole("dialog", {
      name: "Leave configuration without saving?",
    })).toBeTruthy();
    expect(screen.getByText("Config Page")).toBeTruthy();
  });

  it("keeps editing after cancelling the pending navigation", async () => {
    window.history.replaceState({}, "", "#/config");
    render(<App />);
    expect(await screen.findByText("Config Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-config-dirty"));
    fireEvent.click(screen.getByRole("button", { name: "Notes" }));

    fireEvent.click(await screen.findByRole("button", { name: "Keep editing" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", {
        name: "Leave configuration without saving?",
      })).toBeNull();
    });
    expect(window.location.hash).toBe("#/config");
    expect(screen.getByText("Config Page")).toBeTruthy();
  });

  it("maps Escape dismissal to keeping the dirty draft", async () => {
    window.history.replaceState({}, "", "#/config");
    render(<App />);
    expect(await screen.findByText("Config Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-config-dirty"));
    fireEvent.click(screen.getByRole("button", { name: "Notes" }));
    expect(await screen.findByRole("dialog", {
      name: "Leave configuration without saving?",
    })).toBeTruthy();

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByRole("dialog", {
        name: "Leave configuration without saving?",
      })).toBeNull();
    });
    expect(window.location.hash).toBe("#/config");
    expect(screen.getByText("Config Page")).toBeTruthy();
  });

  it("discards once, commits the pending route, and does not reopen", async () => {
    window.history.replaceState({}, "", "#/config");
    render(<App />);
    expect(await screen.findByText("Config Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-config-dirty"));
    fireEvent.click(screen.getByRole("button", { name: "Notes" }));

    fireEvent.click(await screen.findByRole("button", {
      name: "Discard changes and leave",
    }));

    await waitFor(() => expect(window.location.hash).toBe("#/notes"));
    expect(await screen.findByText("Notes Page")).toBeTruthy();
    act(() => window.dispatchEvent(new HashChangeEvent("hashchange")));
    expect(screen.queryByRole("dialog", {
      name: "Leave configuration without saving?",
    })).toBeNull();
  });

  it("updates an open confirmation to the latest requested target", async () => {
    window.history.replaceState({}, "", "#/config");
    render(<App />);
    expect(await screen.findByText("Config Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-config-dirty"));
    fireEvent.click(screen.getByRole("button", { name: "Notes" }));
    expect(await screen.findByRole("dialog", {
      name: "Leave configuration without saving?",
    })).toBeTruthy();

    act(() => {
      window.location.hash = "#/system";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(window.location.hash).toBe("#/config");
    fireEvent.click(screen.getByRole("button", {
      name: "Discard changes and leave",
    }));

    await waitFor(() => expect(window.location.hash).toBe("#/system"));
    expect(await screen.findByText("System Page")).toBeTruthy();
  });

  it("treats navigation to the current config route as a no-op", async () => {
    window.history.replaceState({}, "", "#/config");
    render(<App />);
    expect(await screen.findByText("Config Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-config-dirty"));

    fireEvent.click(screen.getByRole("button", { name: "Configuration" }));

    expect(window.location.hash).toBe("#/config");
    expect(screen.queryByRole("dialog", {
      name: "Leave configuration without saving?",
    })).toBeNull();
  });

  it("cancels a stale pending confirmation when ConfigPage reports clean", async () => {
    window.history.replaceState({}, "", "#/config");
    render(<App />);
    expect(await screen.findByText("Config Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-config-dirty"));
    fireEvent.click(screen.getByRole("button", { name: "Notes" }));
    expect(await screen.findByRole("dialog", {
      name: "Leave configuration without saving?",
    })).toBeTruthy();

    fireEvent.click(screen.getByTestId("make-config-clean"));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", {
        name: "Leave configuration without saving?",
      })).toBeNull();
    });
    expect(window.location.hash).toBe("#/config");
    expect(screen.getByText("Config Page")).toBeTruthy();
  });

  it("does not register a duplicate beforeunload guard in App", async () => {
    window.history.replaceState({}, "", "#/config");
    const addEventListener = vi.spyOn(window, "addEventListener");

    render(<App />);
    expect(await screen.findByText("Config Page")).toBeTruthy();

    expect(
      addEventListener.mock.calls.filter(([event]) => event === "beforeunload"),
    ).toHaveLength(0);
  });
});
