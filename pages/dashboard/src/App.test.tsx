import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/pages/GraphPage", () => ({
  GraphPage: () => <div>Graph Page</div>,
}));
vi.mock("@/pages/MemoryPage", () => ({
  MemoryPage: ({ navigationTarget, onDirtyChange }: {
    navigationTarget?: { requestId: number; id: string } | null;
    onDirtyChange?: (dirty: boolean) => void;
  }) => (
    <div>
      <p>Memory Page</p>
      <output data-testid="memory-navigation-target">
        {navigationTarget
          ? `${navigationTarget.requestId}:${navigationTarget.id}`
          : "none"}
      </output>
      <button type="button" data-testid="make-memory-dirty" onClick={() => onDirtyChange?.(true)}>
        Make memory dirty
      </button>
      <button type="button" data-testid="make-memory-clean" onClick={() => onDirtyChange?.(false)}>
        Mark memory clean
      </button>
    </div>
  ),
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
      navigationTarget,
      onDirtyChange,
      showToast,
    }: {
      navigationTarget?: {
        requestId: number;
        path: string;
        query: string;
      } | null;
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
          <output data-testid="config-navigation-target">
            {navigationTarget
              ? `${navigationTarget.requestId}:${navigationTarget.path}:${navigationTarget.query}`
              : "none"}
          </output>
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
  KnowledgePage: ({ navigationTarget }: {
    navigationTarget?: { requestId: number; id: string } | null;
  }) => (
    <div>
      <p>Knowledge Page</p>
      <output data-testid="knowledge-navigation-target">
        {navigationTarget
          ? `${navigationTarget.requestId}:${navigationTarget.id}`
          : "none"}
      </output>
    </div>
  ),
}));
vi.mock("@/pages/NotesPage", () => ({
  NotesPage: ({ navigationTarget }: {
    navigationTarget?: { requestId: number; id: string } | null;
  }) => (
    <div>
      <p>Notes Page</p>
      <output data-testid="notes-navigation-target">
        {navigationTarget
          ? `${navigationTarget.requestId}:${navigationTarget.id}`
          : "none"}
      </output>
    </div>
  ),
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

async function traverseHistory(action: () => void) {
  await act(async () => {
    await new Promise<void>((resolve) => {
      const finish = () => {
        window.clearTimeout(timer);
        window.removeEventListener("popstate", finish);
        resolve();
      };
      const timer = window.setTimeout(finish, 50);
      window.addEventListener("popstate", finish, { once: true });
      action();
    });
  });
}

async function selectGlobalSearchOption(
  query: string,
  optionName: RegExp,
): Promise<void> {
  fireEvent.click(screen.getByRole("button", { name: "Search..." }));
  const input = await screen.findByRole("combobox", { name: "Global search" });
  fireEvent.change(input, { target: { value: query } });
  fireEvent.click(await screen.findByRole("option", { name: optionName }));
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
      apiGet: vi.fn((path: string) => {
        if (path === "page/config/schema") {
          return Promise.resolve({
            status: "ok",
            data: {
              plugin_name: "astrbot_plugin_memora",
              schema: {
                provider_settings: {
                  type: "object",
                  description: "Provider settings",
                  items: {
                    llm_provider_id: {
                      type: "string",
                      description: "LLM provider",
                      hint: "Provider used for memory extraction",
                      default: "",
                    },
                  },
                },
              },
              provider_options: { llm: [], embedding: [] },
              capabilities: { hot_reload: true },
            },
          });
        }
        if (path === "page/memories") {
          return Promise.resolve({
            status: "ok",
            data: {
              items: [{
                id: "memory-1",
                content: "Search memory result",
                importance: 0.8,
              }],
              total: 1,
            },
          });
        }
        if (path === "page/knowledge/search") {
          return Promise.resolve({
            status: "ok",
            data: {
              entries: [{
                entry_id: "knowledge-1",
                title: "Search knowledge result",
                category: "fact",
              }],
              total: 1,
            },
          });
        }
        if (path === "page/notes/search") {
          return Promise.resolve({
            status: "ok",
            data: {
              notes: [{
                note_id: "note-1",
                title: "Search note result",
                status: "active",
              }],
              total: 1,
            },
          });
        }
        return Promise.reject(new Error(`Unexpected GET endpoint: ${path}`));
      }),
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
      screen.getByRole("combobox", { name: "Global search" }),
      { target: { value: "memory" } },
    );
    fireEvent.click(await screen.findByRole("option", {
      name: /Search memory result/,
    }));

    expect(await screen.findByRole("dialog", {
      name: "Leave configuration without saving?",
    })).toBeTruthy();
    expect(window.location.hash).toBe("#/config");
  });

  it("blocks dirty memory sidebar and same-page entity navigation, while preserving beforeunload protection", async () => {
    window.history.replaceState({}, "", "#/memory");
    const addEventListener = vi.spyOn(window, "addEventListener");
    const removeEventListener = vi.spyOn(window, "removeEventListener");
    const view = render(<App />);
    expect(await screen.findByText("Memory Page")).toBeTruthy();

    fireEvent.click(screen.getByTestId("make-memory-dirty"));
    expect(addEventListener.mock.calls.filter(([event]) => event === "beforeunload")).toHaveLength(1);
    const dirtyEvent = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirtyEvent);
    expect(dirtyEvent.defaultPrevented).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Notes" }));
    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(window.location.hash).toBe("#/memory");
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }));

    fireEvent.click(screen.getByRole("button", { name: "Search..." }));
    fireEvent.change(screen.getByRole("combobox", { name: "Global search" }), { target: { value: "memory" } });
    fireEvent.click(await screen.findByRole("option", { name: /Search memory result/ }));
    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(screen.getByTestId("memory-navigation-target").textContent).toBe("none");

    fireEvent.click(screen.getByTestId("make-memory-clean"));
    expect(removeEventListener.mock.calls.filter(([event]) => event === "beforeunload")).toHaveLength(1);
    view.unmount();
  });

  it("allows a dirty same-page intent that keeps the current entity unchanged", async () => {
    window.history.replaceState({}, "", "#/preview");
    render(<App />);
    expect(await screen.findByText("Preview Page")).toBeTruthy();
    await selectGlobalSearchOption("memory", /Search memory result/);
    expect(await screen.findByText("Memory Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-memory-dirty"));

    await selectGlobalSearchOption("memory", /Search memory result/);

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("memory-navigation-target").textContent).toMatch(/^\d+:memory-1$/);
  });

  it("passes a configuration search target across page navigation", async () => {
    window.history.replaceState({}, "", "#/preview");
    render(<App />);
    expect(await screen.findByText("Preview Page")).toBeTruthy();

    await selectGlobalSearchOption("LLM provider", /LLM provider/);

    await waitFor(() => expect(window.location.hash).toBe("#/config"));
    expect(await screen.findByText("Config Page")).toBeTruthy();
    expect(screen.getByTestId("config-navigation-target").textContent)
      .toContain("provider_settings.llm_provider_id:LLM provider");
  });

  it("updates a configuration target on the current page without pushing history", async () => {
    window.history.replaceState({}, "", "#/config");
    const pushSpy = vi.spyOn(window.history, "pushState");
    render(<App />);
    expect(await screen.findByText("Config Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-config-dirty"));
    pushSpy.mockClear();

    await selectGlobalSearchOption("LLM provider", /LLM provider/);

    expect(pushSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog", {
      name: "Leave configuration without saving?",
    })).toBeNull();
    expect(screen.getByTestId("config-navigation-target").textContent)
      .toContain("provider_settings.llm_provider_id:LLM provider");
  });

  it.each([
    {
      query: "memory",
      option: /Search memory result/,
      hash: "#/memory",
      page: "Memory Page",
      targetTestId: "memory-navigation-target",
      id: "memory-1",
    },
    {
      query: "knowledge",
      option: /Search knowledge result/,
      hash: "#/knowledge",
      page: "Knowledge Page",
      targetTestId: "knowledge-navigation-target",
      id: "knowledge-1",
    },
    {
      query: "note",
      option: /Search note result/,
      hash: "#/notes",
      page: "Notes Page",
      targetTestId: "notes-navigation-target",
      id: "note-1",
    },
  ])("passes the exact $page entity target from global search", async ({
    query,
    option,
    hash,
    page,
    targetTestId,
    id,
  }) => {
    window.history.replaceState({}, "", "#/preview");
    render(<App />);
    expect(await screen.findByText("Preview Page")).toBeTruthy();

    await selectGlobalSearchOption(query, option);

    await waitFor(() => expect(window.location.hash).toBe(hash));
    expect(await screen.findByText(page)).toBeTruthy();
    expect(screen.getByTestId(targetTestId).textContent).toMatch(
      new RegExp(`^\\d+:${id}$`),
    );
  });

  it("preserves an entity search target through the dirty-config discard guard", async () => {
    window.history.replaceState({}, "", "#/config");
    render(<App />);
    expect(await screen.findByText("Config Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-config-dirty"));

    await selectGlobalSearchOption("memory", /Search memory result/);
    fireEvent.click(await screen.findByRole("button", {
      name: "Discard changes and leave",
    }));

    await waitFor(() => expect(window.location.hash).toBe("#/memory"));
    expect(await screen.findByText("Memory Page")).toBeTruthy();
    expect(screen.getByTestId("memory-navigation-target").textContent)
      .toMatch(/^\d+:memory-1$/);
  });

  it("clears a one-shot entity target when browser history reopens the page", async () => {
    window.history.replaceState({}, "", "#/preview");
    render(<App />);
    expect(await screen.findByText("Preview Page")).toBeTruthy();

    await selectGlobalSearchOption("memory", /Search memory result/);
    expect(await screen.findByText("Memory Page")).toBeTruthy();
    expect(screen.getByTestId("memory-navigation-target").textContent)
      .toMatch(/^\d+:memory-1$/);

    fireEvent.click(screen.getByRole("button", { name: "Notes" }));
    expect(await screen.findByText("Notes Page")).toBeTruthy();
    await traverseHistory(() => window.history.back());

    expect(await screen.findByText("Memory Page")).toBeTruthy();
    expect(screen.getByTestId("memory-navigation-target").textContent)
      .toBe("none");
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
    expect(await screen.findByRole("dialog", {
      name: "Leave configuration without saving?",
    })).toBeTruthy();
    expect(screen.getByText("Config Page")).toBeTruthy();
  });

  it("keeps the original Back target after Keep editing and a no-op Forward", async () => {
    window.history.replaceState({ route: "preview" }, "", "#/preview");
    window.history.pushState({ route: "graph" }, "", "#/graph");
    render(<App />);
    expect(await screen.findByText("Graph Page")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Configuration" }));
    expect(await screen.findByText("Config Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-config-dirty"));

    await traverseHistory(() => window.history.back());
    fireEvent.click(await screen.findByRole("button", { name: "Keep editing" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog", {
        name: "Leave configuration without saving?",
      })).toBeNull();
    });

    await traverseHistory(() => window.history.forward());
    await traverseHistory(() => window.history.back());
    expect(await screen.findByRole("dialog", {
      name: "Leave configuration without saving?",
    })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {
      name: "Discard changes and leave",
    }));

    expect(await screen.findByText("Graph Page")).toBeTruthy();
    expect(window.location.hash).toBe("#/graph");
  });

  it("returns to Preview when going Back after discarding to the historical Graph entry", async () => {
    window.history.replaceState({ route: "preview" }, "", "#/preview");
    window.history.pushState({ route: "graph" }, "", "#/graph");
    render(<App />);
    expect(await screen.findByText("Graph Page")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Configuration" }));
    expect(await screen.findByText("Config Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-config-dirty"));

    await traverseHistory(() => window.history.back());
    fireEvent.click(await screen.findByRole("button", {
      name: "Discard changes and leave",
    }));
    expect(await screen.findByText("Graph Page")).toBeTruthy();

    await traverseHistory(() => window.history.back());

    expect(await screen.findByText("Preview Page")).toBeTruthy();
    expect(window.location.hash).toBe("#/preview");
  });

  it("preserves Forward from Graph to Config after discarding a Back request", async () => {
    window.history.replaceState({ route: "preview" }, "", "#/preview");
    window.history.pushState({ route: "graph" }, "", "#/graph");
    render(<App />);
    expect(await screen.findByText("Graph Page")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Configuration" }));
    expect(await screen.findByText("Config Page")).toBeTruthy();
    fireEvent.click(screen.getByTestId("make-config-dirty"));

    await traverseHistory(() => window.history.back());
    fireEvent.click(await screen.findByRole("button", {
      name: "Discard changes and leave",
    }));
    expect(await screen.findByText("Graph Page")).toBeTruthy();

    await traverseHistory(() => window.history.forward());

    expect(await screen.findByText("Config Page")).toBeTruthy();
    await waitFor(() => expect(window.location.hash).toBe("#/config"));
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
    await waitFor(() => expect(window.location.hash).toBe("#/config"));
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
