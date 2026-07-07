import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

interface BridgeMock {
  getLocale: ReturnType<typeof vi.fn>;
  getI18n: ReturnType<typeof vi.fn>;
  t: ReturnType<typeof vi.fn>;
  onContextChange: ReturnType<typeof vi.fn>;
}

async function loadHarness() {
  const mod = await import("./useI18n");

  function Harness() {
    const { t, currentLang } = mod.useI18n();

    return (
      <div>
        <span data-testid="lang">{currentLang()}</span>
        <span data-testid="text">{t("nav.graph")}</span>
        <span data-testid="args">{t("timeline.count", "5")}</span>
        <button onClick={() => mod.toggleLanguage()}>cycle</button>
      </div>
    );
  }

  return { Harness };
}

describe("useI18n", () => {
  let bridge: BridgeMock;

  beforeEach(() => {
    vi.resetModules();
    bridge = {
      getLocale: vi.fn().mockReturnValue("zh-CN"),
      getI18n: vi.fn().mockReturnValue({
        dashboard: {
          nav: { graph: "图谱" },
          timeline: { count: "{0} 条" },
        },
      }),
      t: vi.fn((key: string) => key),
      onContextChange: vi.fn(),
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

  it("reads translated values from bridge i18n data and applies placeholders", async () => {
    const { Harness } = await loadHarness();

    render(<Harness />);

    expect(screen.getByTestId("lang").textContent).toBe("zh");
    expect(screen.getByTestId("text").textContent).toBe("图谱");
    expect(screen.getByTestId("args").textContent).toBe("5 条");
  });

  it("cycles languages through zh -> en -> ru and triggers rerender", async () => {
    const { Harness } = await loadHarness();

    render(<Harness />);

    await act(async () => {
      fireEvent.click(screen.getByText("cycle"));
    });
    expect(screen.getByTestId("lang").textContent).toBe("en");

    await act(async () => {
      fireEvent.click(screen.getByText("cycle"));
    });
    expect(screen.getByTestId("lang").textContent).toBe("ru");

    await act(async () => {
      fireEvent.click(screen.getByText("cycle"));
    });
    expect(screen.getByTestId("lang").textContent).toBe("zh");
  });

  it("refreshes when bridge context change listeners fire", async () => {
    let contextHandler: (() => void) | undefined;
    bridge.onContextChange.mockImplementation((handler) => {
      contextHandler = handler;
    });
    bridge.getLocale.mockReturnValue("en-US");
    bridge.getI18n.mockReturnValue({
      dashboard: {
        nav: { graph: "Knowledge Graph" },
        timeline: { count: "{0} items" },
      },
    });

    const { Harness } = await loadHarness();

    render(<Harness />);

    await act(async () => {
      contextHandler?.();
    });

    expect(screen.getByTestId("lang").textContent).toBe("en");
    expect(screen.getByTestId("text").textContent).toBe("Knowledge Graph");
    expect(screen.getByTestId("args").textContent).toBe("5 items");
  });
});
