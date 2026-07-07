import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

import App from "./App";

interface BridgeMock {
  getLocale?: ReturnType<typeof vi.fn>;
  getI18n?: ReturnType<typeof vi.fn>;
  t?: ReturnType<typeof vi.fn>;
  onContextChange?: ReturnType<typeof vi.fn>;
  offContextChange?: ReturnType<typeof vi.fn>;
  subscribeSSE?: ReturnType<typeof vi.fn>;
  unsubscribeSSE?: ReturnType<typeof vi.fn>;
}

describe("App", () => {
  beforeEach(() => {
    window.location.hash = "#/graph";

    const bridge: BridgeMock = {
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
      onContextChange: vi.fn(),
      offContextChange: vi.fn(),
      subscribeSSE: vi.fn().mockReturnValue("sub-1"),
      unsubscribeSSE: vi.fn(),
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
    window.location.hash = "";
  });

  it("renders the page selected by the current hash", async () => {
    window.location.hash = "#/knowledge";

    render(<App />);

    expect(await screen.findByText("Knowledge Page")).toBeTruthy();
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
});
