import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

interface BridgeMock {
  getLocale: ReturnType<typeof vi.fn>;
  getI18n: ReturnType<typeof vi.fn>;
  t: ReturnType<typeof vi.fn>;
  onContextChange: ReturnType<typeof vi.fn>;
  offContextChange: ReturnType<typeof vi.fn>;
}

interface I18nSnapshot {
  t: (key: string, ...args: string[]) => string;
  currentLang: () => string;
}

async function loadHarness(onRender?: (snapshot: I18nSnapshot) => void) {
  const mod = await import("./useI18n");

  function Harness() {
    const { t, currentLang } = mod.useI18n();
    onRender?.({ t, currentLang });

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
    localStorage.clear();
    Reflect.deleteProperty(window, "setLanguage");
    document.documentElement.lang = "en";
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
    Reflect.deleteProperty(window, "setLanguage");
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
    expect(document.documentElement.lang).toBe("zh-CN");
  });

  it("cycles languages through zh -> en -> ru and triggers rerender", async () => {
    const { Harness } = await loadHarness();

    render(<Harness />);

    await act(async () => {
      fireEvent.click(screen.getByText("cycle"));
    });
    expect(screen.getByTestId("lang").textContent).toBe("en");
    expect(screen.getByTestId("text").textContent).toBe("Knowledge Graph");
    expect(localStorage.getItem("memora_lang")).toBe("en");
    expect(document.documentElement.lang).toBe("en-US");

    await act(async () => {
      fireEvent.click(screen.getByText("cycle"));
    });
    expect(screen.getByTestId("lang").textContent).toBe("ru");
    expect(screen.getByTestId("text").textContent).toBe("Граф знаний");
    expect(localStorage.getItem("memora_lang")).toBe("ru");
    expect(document.documentElement.lang).toBe("ru-RU");

    await act(async () => {
      fireEvent.click(screen.getByText("cycle"));
    });
    expect(screen.getByTestId("lang").textContent).toBe("zh");
    expect(screen.getByTestId("text").textContent).toBe("知识图谱");
    expect(localStorage.getItem("memora_lang")).toBe("zh");
    expect(document.documentElement.lang).toBe("zh-CN");
  });

  it("preserves dollar replacement tokens in interpolated API content", async () => {
    let snapshot: I18nSnapshot | undefined;
    const { Harness } = await loadHarness((value) => { snapshot = value; });

    render(<Harness />);

    expect(snapshot?.t("timeline.count", "$& $$")).toBe("$& $$ 条");
  });

  it("restores a persisted dashboard language ahead of the bridge locale", async () => {
    localStorage.setItem("memora_lang", "ru");
    const { Harness } = await loadHarness();

    render(<Harness />);

    expect(screen.getByTestId("lang").textContent).toBe("ru");
    expect(screen.getByTestId("text").textContent).toBe("Граф знаний");
    expect(document.documentElement.lang).toBe("ru-RU");
  });

  it("refreshes translation function identities when the language version changes", async () => {
    const snapshots: I18nSnapshot[] = [];
    const { Harness } = await loadHarness((snapshot) => snapshots.push(snapshot));

    render(<Harness />);
    const initial = snapshots[snapshots.length - 1];
    expect(initial).toBeDefined();

    await act(async () => {
      window.dispatchEvent(new Event("languagechange"));
    });

    const updated = snapshots[snapshots.length - 1];
    expect(updated).toBeDefined();
    expect(updated?.t).not.toBe(initial?.t);
    expect(updated?.currentLang).not.toBe(initial?.currentLang);
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
    expect(document.documentElement.lang).toBe("en-US");
  });

  it("unsubscribes the bridge context listener on unmount", async () => {
    let contextHandler: (() => void) | undefined;
    bridge.onContextChange.mockImplementation((handler) => {
      contextHandler = handler;
    });
    const { Harness } = await loadHarness();

    const { unmount } = render(<Harness />);
    unmount();

    expect(contextHandler).toBeDefined();
    expect(bridge.offContextChange).toHaveBeenCalledWith(contextHandler);
  });
});
