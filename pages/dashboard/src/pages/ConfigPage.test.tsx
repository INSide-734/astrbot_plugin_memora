import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ConfigApiResponse,
  ConfigApplyData,
  ConfigObject,
  ConfigSchemaData,
  ConfigStateData,
} from "@/types/config";
import { toggleLanguage } from "@/hooks/useI18n";

import { ConfigPage } from "./ConfigPage";

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale: ReturnType<typeof vi.fn>;
  getI18n: ReturnType<typeof vi.fn>;
  t: ReturnType<typeof vi.fn>;
  onContextChange: ReturnType<typeof vi.fn>;
  offContextChange: ReturnType<typeof vi.fn>;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function ok<T>(data: T): ConfigApiResponse<T> {
  return { status: "ok", data };
}

const schemaData: ConfigSchemaData = {
  plugin_name: "astrbot_plugin_memora",
  schema: {
    general: {
      type: "object",
      description: "General",
      hint: "Core memory behavior",
      items: {
        enabled: { type: "bool", description: "Memory enabled" },
        mode: {
          type: "string",
          description: "Recall mode",
          options: ["hybrid", "vector"],
        },
        bot_name: {
          type: "string",
          description: "Bot name",
          hint: "Used in generated memories",
        },
        reflection_prompt: {
          type: "text",
          description: "Reflection prompt",
        },
        retry_limit: {
          type: "int",
          description: "Retry limit",
          min: 0,
          max: 10,
        },
        score_threshold: {
          type: "float",
          description: "Score threshold",
          min: 0,
          max: 1,
          step: 0.05,
        },
      },
    },
    provider_settings: {
      type: "object",
      description: "Provider settings",
      items: {
        llm_provider_id: {
          type: "string",
          description: "LLM provider",
          _special: "select_provider",
        },
        embedding_provider_id: {
          type: "string",
          description: "Embedding provider",
        },
      },
    },
  },
  provider_options: {
    llm: [{ id: "llm-primary", label: "GPT Primary" }],
    embedding: [{ id: "embed-primary", label: "Embedding Primary" }],
  },
  capabilities: { hot_reload: true },
};

const baseConfig: ConfigObject = {
  general: {
    enabled: true,
    mode: "hybrid",
    bot_name: "Memora",
    reflection_prompt: "Remember useful details",
    retry_limit: 3,
    score_threshold: 0.65,
  },
  provider_settings: {
    llm_provider_id: "",
    embedding_provider_id: "embed-primary",
  },
};

function state(
  config: ConfigObject = baseConfig,
  revision = "rev-1",
  instanceId = "instance-1",
): ConfigApiResponse<ConfigStateData> {
  return ok({
    changed: true,
    config,
    revision,
    instance_id: instanceId,
  });
}

function applyResult(
  overrides: Partial<ConfigApplyData> = {},
): ConfigApiResponse<ConfigApplyData> {
  return ok({
    revision: "rev-2",
    changed_paths: ["general.bot_name"],
    reload_scheduled: false,
    instance_id: "instance-1",
    ...overrides,
  });
}

function elementRect({
  bottom,
  left = 0,
  right = 200,
  top,
}: {
  bottom: number;
  left?: number;
  right?: number;
  top: number;
}): DOMRect {
  return {
    bottom,
    height: bottom - top,
    left,
    right,
    top,
    width: right - left,
    x: left,
    y: top,
    toJSON: () => ({}),
  };
}

describe("ConfigPage", () => {
  let bridge: BridgeMock;
  let schemaHandler: () => Promise<ApiResponse>;
  let stateHandler: (params: Record<string, string>) => Promise<ApiResponse>;
  let applyHandler: (body: unknown) => Promise<ApiResponse>;

  const flushMicrotasks = async () => {
    await act(async () => {
      for (let index = 0; index < 8; index += 1) {
        await Promise.resolve();
      }
    });
  };

  beforeEach(() => {
    localStorage.clear();
    schemaHandler = async () => ok(schemaData) as ApiResponse;
    stateHandler = async () => state() as ApiResponse;
    applyHandler = async () => applyResult() as ApiResponse;

    bridge = {
      apiGet: vi.fn((endpoint: string, params: Record<string, string> = {}) => {
        if (endpoint === "page/config/schema") return schemaHandler();
        if (endpoint === "page/config/state") return stateHandler(params);
        return Promise.reject(new Error(`Unexpected GET endpoint: ${endpoint}`));
      }),
      apiPost: vi.fn((endpoint: string, body: unknown) => {
        if (endpoint === "page/config/apply") return applyHandler(body);
        return Promise.reject(new Error(`Unexpected POST endpoint: ${endpoint}`));
      }),
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
      onContextChange: vi.fn(),
      offContextChange: vi.fn(),
    };

    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: bridge,
    });
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
    localStorage.clear();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  it("shows stable accessible loading skeletons until schema and state both arrive", async () => {
    const pendingSchema = deferred<ApiResponse>();
    const pendingState = deferred<ApiResponse>();
    schemaHandler = () => pendingSchema.promise;
    stateHandler = () => pendingState.promise;

    const { container } = render(<ConfigPage />);

    const loading = screen.getByRole("status");
    expect(loading.getAttribute("aria-busy")).toBe("true");
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(1);

    pendingSchema.resolve(ok(schemaData) as ApiResponse);
    pendingState.resolve(state() as ApiResponse);

    expect(await screen.findByRole("switch", { name: "Memory enabled" })).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  });

  it("uses the dense shared layout and renders every supported field through ConfigField", async () => {
    const { container } = render(<ConfigPage />);

    expect(await screen.findByRole("switch", { name: "Memory enabled" })).toBeTruthy();
    expect(container.querySelector('[data-layout="dense"]')).toBeTruthy();
    expect(container.querySelector('[data-slot="page-toolbar"]')).toBeTruthy();
    expect(container.querySelector('[data-slot="page-content"]')).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Recall mode" })).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Bot name" })).toHaveProperty("value", "Memora");
    expect(screen.getByRole("textbox", { name: "Reflection prompt" }).tagName).toBe("TEXTAREA");
    expect(screen.getByRole("spinbutton", { name: "Retry limit" })).toHaveProperty("value", "3");
    expect(screen.getByRole("spinbutton", { name: "Score threshold" })).toHaveProperty("value", "0.65");
    expect(screen.getByRole("combobox", { name: "LLM provider" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Embedding provider" })).toBeTruthy();
  });

  it("composes search with modified-only while retaining the dirty field hierarchy", async () => {
    render(<ConfigPage />);

    const name = await screen.findByRole("textbox", { name: "Bot name" });
    fireEvent.change(name, { target: { value: "Archive" } });
    fireEvent.click(screen.getByRole("switch", { name: "Modified only" }));

    expect(screen.getByRole("textbox", { name: "Bot name" })).toHaveProperty("value", "Archive");
    expect(screen.queryByRole("switch", { name: "Memory enabled" })).toBeNull();

    fireEvent.change(screen.getByRole("searchbox", { name: "Search configuration" }), {
      target: { value: "threshold" },
    });
    expect(screen.getByText("No configuration fields match these filters.")).toBeTruthy();

    fireEvent.change(screen.getByRole("searchbox", { name: "Search configuration" }), {
      target: { value: "general.bot_name" },
    });
    expect(screen.getByRole("textbox", { name: "Bot name" })).toHaveProperty("value", "Archive");
  });

  it("drives desktop and mobile group navigation from one section model and one form tree", async () => {
    const { container } = render(<ConfigPage />);

    await screen.findByRole("textbox", { name: "Bot name" });
    const navigation = screen.getByRole("navigation", {
      name: "Configuration groups",
    });
    expect(within(navigation).getByRole("button", { name: "General" })).toBeTruthy();
    expect(
      within(navigation).getByRole("button", { name: "Provider settings" }),
    ).toBeTruthy();

    fireEvent.click(
      within(navigation).getByRole("button", { name: "Provider settings" }),
    );
    const providerSection = screen.getByRole("region", {
      name: "Provider settings",
    });
    expect(document.activeElement).toBe(providerSection);

    const mobileSelect = screen.getByRole("combobox", {
      name: "Configuration group",
    });
    fireEvent.click(mobileSelect);
    const generalOption = await screen.findByRole("option", { name: "General" });
    fireEvent.pointerDown(generalOption, { pointerType: "mouse" });
    fireEvent.click(generalOption);
    await waitFor(() => {
      expect(document.activeElement).toBe(
        screen.getByRole("region", { name: "General" }),
      );
    });

    const technicalControls = Array.from(
      container.querySelectorAll(
        "[data-slot='page-content'] input[id^='config-'], [data-slot='page-content'] textarea[id^='config-'], [data-slot='page-content'] button[id^='config-']",
      ),
    );
    const ids = technicalControls.map((element) => element.id);
    expect(ids.length).toBeGreaterThan(0);
    expect(new Set(ids).size).toBe(ids.length);
    expect(screen.getAllByRole("textbox", { name: "Bot name" })).toHaveLength(1);
  });

  it("tracks the visible desktop form section and keeps its nav item visible", async () => {
    const { container } = render(<ConfigPage />);

    await screen.findByRole("textbox", { name: "Bot name" });
    const navigation = screen.getByRole("navigation", {
      name: "Configuration groups",
    });
    const formScroll = container.querySelector<HTMLElement>(
      "[data-slot='config-form-scroll']",
    )!;
    const generalSection = container.querySelector<HTMLElement>(
      "[data-config-section='general']",
    )!;
    const providerSection = container.querySelector<HTMLElement>(
      "[data-config-section='provider_settings']",
    )!;
    const generalNav = within(navigation).getByRole("button", { name: "General" });
    const providerNav = within(navigation).getByRole("button", {
      name: "Provider settings",
    });

    expect(formScroll).toBeTruthy();
    Object.defineProperties(formScroll, {
      clientHeight: { configurable: true, value: 600 },
      scrollHeight: { configurable: true, value: 2_000 },
      scrollTop: { configurable: true, value: 500, writable: true },
    });
    Object.defineProperty(navigation, "scrollTop", {
      configurable: true,
      value: 25,
      writable: true,
    });
    vi.spyOn(formScroll, "getBoundingClientRect").mockReturnValue(
      elementRect({ top: 100, bottom: 700 }),
    );
    vi.spyOn(generalSection, "getBoundingClientRect").mockReturnValue(
      elementRect({ top: -500, bottom: 90 }),
    );
    vi.spyOn(providerSection, "getBoundingClientRect").mockReturnValue(
      elementRect({ top: 115, bottom: 680 }),
    );
    vi.spyOn(navigation, "getBoundingClientRect").mockReturnValue(
      elementRect({ top: 100, bottom: 300 }),
    );
    vi.spyOn(providerNav, "getBoundingClientRect").mockReturnValue(
      elementRect({ top: 330, bottom: 370 }),
    );

    fireEvent.scroll(formScroll);

    await waitFor(() =>
      expect(providerNav.getAttribute("aria-current")).toBe("true"),
    );
    expect(generalNav.hasAttribute("aria-current")).toBe(false);
    expect(navigation.scrollTop).toBe(95);
  });

  it("pins scroll-spy to the first and last groups and contains nested wheel scrolling", async () => {
    const { container } = render(<ConfigPage />);

    await screen.findByRole("textbox", { name: "Bot name" });
    const navigation = screen.getByRole("navigation", {
      name: "Configuration groups",
    });
    const formScroll = container.querySelector<HTMLElement>(
      "[data-slot='config-form-scroll']",
    )!;
    const generalNav = within(navigation).getByRole("button", { name: "General" });
    const providerNav = within(navigation).getByRole("button", {
      name: "Provider settings",
    });

    expect(formScroll).toBeTruthy();
    expect(navigation.classList.contains("overscroll-contain")).toBe(true);
    expect(formScroll.classList.contains("lg:overscroll-contain")).toBe(true);
    Object.defineProperties(formScroll, {
      clientHeight: { configurable: true, value: 600 },
      scrollHeight: { configurable: true, value: 2_000 },
      scrollTop: { configurable: true, value: 1_400, writable: true },
    });

    fireEvent.scroll(formScroll);
    await waitFor(() =>
      expect(providerNav.getAttribute("aria-current")).toBe("true"),
    );

    Object.defineProperty(formScroll, "scrollHeight", {
      configurable: true,
      value: 600,
    });
    formScroll.scrollTop = 0;
    fireEvent.scroll(formScroll);
    await waitFor(() =>
      expect(generalNav.getAttribute("aria-current")).toBe("true"),
    );
    expect(providerNav.hasAttribute("aria-current")).toBe(false);
  });

  it("replaces a pending mobile section focus with the latest selection", async () => {
    vi.useFakeTimers();
    const extendedSchema: ConfigSchemaData = {
      ...schemaData,
      schema: {
        ...schemaData.schema,
        advanced: {
          type: "object",
          description: "Advanced",
          items: {
            archive_label: {
              type: "string",
              description: "Archive label",
            },
          },
        },
      },
    };
    const extendedConfig: ConfigObject = {
      ...baseConfig,
      advanced: { archive_label: "Long term" },
    };
    schemaHandler = async () => ok(extendedSchema) as ApiResponse;
    stateHandler = async () => state(extendedConfig) as ApiResponse;
    const { container } = render(<ConfigPage />);
    await flushMicrotasks();

    const providerSection = container.querySelector<HTMLElement>(
      "[data-config-section='provider_settings']",
    )!;
    const advancedSection = container.querySelector<HTMLElement>(
      "[data-config-section='advanced']",
    )!;
    const providerFocusTarget =
      providerSection.querySelector<HTMLElement>("[data-slot='config-group']") ??
      providerSection;
    const advancedFocusTarget =
      advancedSection.querySelector<HTMLElement>("[data-slot='config-group']") ??
      advancedSection;
    const providerScroll = vi.spyOn(providerSection, "scrollIntoView");
    const providerFocus = vi.spyOn(providerFocusTarget, "focus");
    const advancedScroll = vi.spyOn(advancedSection, "scrollIntoView");
    const advancedFocus = vi.spyOn(advancedFocusTarget, "focus");
    const mobileSelect = screen.getByRole("combobox", {
      name: "Configuration group",
    });

    fireEvent.click(mobileSelect);
    const providerOption = screen.getByRole("option", {
      name: "Provider settings",
    });
    fireEvent.pointerDown(providerOption, { pointerType: "mouse" });
    fireEvent.click(providerOption);

    fireEvent.click(mobileSelect);
    const advancedOption = screen.getByRole("option", { name: "Advanced" });
    fireEvent.pointerDown(advancedOption, { pointerType: "mouse" });
    fireEvent.click(advancedOption);

    expect(providerScroll).not.toHaveBeenCalled();
    expect(advancedScroll).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(100));

    expect(providerScroll).not.toHaveBeenCalled();
    expect(providerFocus).not.toHaveBeenCalled();
    expect(advancedScroll).toHaveBeenCalledTimes(1);
    expect(advancedFocus).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(advancedFocusTarget);
  });

  it("clears pending mobile section focus when the page unmounts", async () => {
    vi.useFakeTimers();
    const firstView = render(<ConfigPage />);
    await flushMicrotasks();

    const firstProviderSection = firstView.container.querySelector<HTMLElement>(
      "[data-config-section='provider_settings']",
    )!;
    const providerSectionId = firstProviderSection.id;
    const mobileSelect = screen.getByRole("combobox", {
      name: "Configuration group",
    });
    fireEvent.click(mobileSelect);
    const providerOption = screen.getByRole("option", {
      name: "Provider settings",
    });
    fireEvent.pointerDown(providerOption, { pointerType: "mouse" });
    fireEvent.click(providerOption);
    expect(vi.getTimerCount()).toBeGreaterThan(0);

    firstView.unmount();
    const secondView = render(<ConfigPage />);
    await flushMicrotasks();

    const secondProviderSection = secondView.container.querySelector<HTMLElement>(
      "[data-config-section='provider_settings']",
    )!;
    expect(secondProviderSection.id).toBe(providerSectionId);
    const secondProviderFocusTarget =
      secondProviderSection.querySelector<HTMLElement>(
        "[data-slot='config-group']",
      ) ?? secondProviderSection;
    const secondProviderScroll = vi.spyOn(
      secondProviderSection,
      "scrollIntoView",
    );
    const secondProviderFocus = vi.spyOn(secondProviderFocusTarget, "focus");

    await act(async () => vi.advanceTimersByTimeAsync(100));
    expect(secondProviderScroll).not.toHaveBeenCalled();
    expect(secondProviderFocus).not.toHaveBeenCalled();
  });

  it("reports dirty transitions and protects only dirty drafts from browser close", async () => {
    const onDirtyChange = vi.fn();
    const addListener = vi.spyOn(window, "addEventListener");
    const removeListener = vi.spyOn(window, "removeEventListener");
    render(<ConfigPage onDirtyChange={onDirtyChange} />);

    const cleanEvent = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(cleanEvent);
    expect(cleanEvent.defaultPrevented).toBe(false);
    expect(
      addListener.mock.calls.some(([type]) => type === "beforeunload"),
    ).toBe(false);

    const name = await screen.findByRole("textbox", { name: "Bot name" });
    fireEvent.change(name, { target: { value: "Archive" } });

    expect(screen.getByText("Unsaved changes")).toBeTruthy();
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    expect(
      addListener.mock.calls.some(([type]) => type === "beforeunload"),
    ).toBe(true);
    const dirtyEvent = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirtyEvent);
    expect(dirtyEvent.defaultPrevented).toBe(true);

    fireEvent.change(name, { target: { value: "Memora" } });
    await waitFor(() => expect(screen.getByText("Synced")).toBeTruthy());
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    expect(
      removeListener.mock.calls.some(([type]) => type === "beforeunload"),
    ).toBe(true);
  });

  it("cleans dirty close protection and resets the parent dirty state on unmount", async () => {
    const onDirtyChange = vi.fn();
    const removeListener = vi.spyOn(window, "removeEventListener");
    const view = render(<ConfigPage onDirtyChange={onDirtyChange} />);

    fireEvent.change(await screen.findByRole("textbox", { name: "Bot name" }), {
      target: { value: "Archive" },
    });
    await waitFor(() => expect(onDirtyChange).toHaveBeenCalledWith(true));

    view.unmount();

    expect(onDirtyChange.mock.calls).toEqual([[true], [false]]);
    expect(
      removeListener.mock.calls.some(([type]) => type === "beforeunload"),
    ).toBe(true);
  });

  it("transfers dirty notification ownership when the callback changes", async () => {
    const ownerA = vi.fn();
    const ownerB = vi.fn();
    const view = render(<ConfigPage onDirtyChange={ownerA} />);

    fireEvent.change(await screen.findByRole("textbox", { name: "Bot name" }), {
      target: { value: "Archive" },
    });
    await waitFor(() => expect(ownerA.mock.calls).toEqual([[true]]));

    view.rerender(<ConfigPage onDirtyChange={ownerB} />);
    await waitFor(() => {
      expect(ownerA.mock.calls).toEqual([[true], [false]]);
      expect(ownerB.mock.calls).toEqual([[true]]);
    });

    view.rerender(<ConfigPage onDirtyChange={ownerB} />);
    expect(ownerA.mock.calls).toEqual([[true], [false]]);
    expect(ownerB.mock.calls).toEqual([[true]]);

    view.rerender(<ConfigPage />);
    await waitFor(() => expect(ownerB.mock.calls).toEqual([[true], [false]]));

    view.unmount();
    expect(ownerA.mock.calls).toEqual([[true], [false]]);
    expect(ownerB.mock.calls).toEqual([[true], [false]]);
  });

  it("keeps clean callback swaps silent and releases the dirty owner on unmount", async () => {
    const ownerA = vi.fn();
    const ownerB = vi.fn();
    const view = render(<ConfigPage onDirtyChange={ownerA} />);

    const name = await screen.findByRole("textbox", { name: "Bot name" });
    view.rerender(<ConfigPage onDirtyChange={ownerB} />);
    expect(ownerA).not.toHaveBeenCalled();
    expect(ownerB).not.toHaveBeenCalled();

    fireEvent.change(name, { target: { value: "Archive" } });
    await waitFor(() => expect(ownerB.mock.calls).toEqual([[true]]));

    view.unmount();

    expect(ownerA).not.toHaveBeenCalled();
    expect(ownerB.mock.calls).toEqual([[true], [false]]);
  });

  it("does not duplicate stable dirty notifications during StrictMode replay", async () => {
    const onDirtyChange = vi.fn();
    const view = render(
      <StrictMode>
        <ConfigPage onDirtyChange={onDirtyChange} />
      </StrictMode>,
    );

    const name = await screen.findByRole("textbox", { name: "Bot name" });
    expect(onDirtyChange).not.toHaveBeenCalled();

    fireEvent.change(name, { target: { value: "Archive" } });
    await waitFor(() => expect(onDirtyChange.mock.calls).toEqual([[true]]));

    view.rerender(
      <StrictMode>
        <ConfigPage onDirtyChange={onDirtyChange} />
      </StrictMode>,
    );
    expect(onDirtyChange.mock.calls).toEqual([[true]]);

    fireEvent.change(name, { target: { value: "Memora" } });
    await waitFor(() =>
      expect(onDirtyChange.mock.calls).toEqual([[true], [false]]),
    );

    view.unmount();
    expect(onDirtyChange.mock.calls).toEqual([[true], [false]]);
  });

  it("posts exact dotted changes once and keeps fields disabled through applying and reloading", async () => {
    const pendingApply = deferred<ApiResponse>();
    applyHandler = () => pendingApply.promise;
    render(<ConfigPage />);

    const name = await screen.findByRole("textbox", { name: "Bot name" });
    fireEvent.change(name, { target: { value: "Archive" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Apply configuration" }),
    );

    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/config/apply", {
        base_revision: "rev-1",
        changes: { "general.bot_name": "Archive" },
      });
    });
    expect(bridge.apiPost).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole("button", { name: "Applying..." }),
    ).toHaveProperty("disabled", true);
    expect(name).toHaveProperty("disabled", true);

    pendingApply.resolve(
      applyResult({ reload_scheduled: true }) as ApiResponse,
    );
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Reloading..." }),
      ).toHaveProperty("disabled", true);
    });
    expect(name).toHaveProperty("disabled", true);
  });

  it("shows path-indexed validation errors without discarding or disabling the draft", async () => {
    applyHandler = async () => ({
      status: "error",
      code: "validation_failed",
      message: "invalid configuration",
      data: {
        field_errors: { "general.retry_limit": "Must be positive" },
      },
    });
    render(<ConfigPage />);

    const retryLimit = await screen.findByRole("spinbutton", {
      name: "Retry limit",
    });
    fireEvent.change(retryLimit, { target: { value: "-1" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Apply configuration" }),
    );

    expect(await screen.findByText("Must be positive")).toBeTruthy();
    expect(screen.getByText("The configuration operation failed. Your draft is preserved; correct it and retry.")).toBeTruthy();
    expect(retryLimit).toHaveProperty("value", "-1");
    expect(retryLimit).toHaveProperty("disabled", false);
    expect(
      screen.getByRole("button", { name: "Apply configuration" }),
    ).toHaveProperty("disabled", false);
  });

  it("automatically adopts a clean external update and refreshes technical metadata", async () => {
    const remoteConfig: ConfigObject = {
      ...baseConfig,
      general: {
        ...(baseConfig.general as ConfigObject),
        bot_name: "AstrBot copy",
      },
    };
    let stateAttempts = 0;
    stateHandler = async () => {
      stateAttempts += 1;
      return (stateAttempts === 1
        ? state()
        : state(remoteConfig, "rev-2", "instance-2")) as ApiResponse;
    };
    render(<ConfigPage />);

    const name = await screen.findByRole("textbox", { name: "Bot name" });
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh configuration" }),
    );

    await waitFor(() => expect(name).toHaveProperty("value", "AstrBot copy"));
    expect(screen.getByText("rev-2")).toBeTruthy();
    expect(screen.getByText("instance-2")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens a dirty external-update conflict and can discard the draft for AstrBot", async () => {
    const remoteConfig: ConfigObject = {
      ...baseConfig,
      general: {
        ...(baseConfig.general as ConfigObject),
        bot_name: "AstrBot copy",
      },
    };
    let stateAttempts = 0;
    stateHandler = async () => {
      stateAttempts += 1;
      return (stateAttempts === 1
        ? state()
        : state(remoteConfig, "rev-2")) as ApiResponse;
    };
    render(<ConfigPage />);

    const name = await screen.findByRole("textbox", { name: "Bot name" });
    fireEvent.change(name, { target: { value: "Local copy" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh configuration" }),
    );

    const dialog = await screen.findByRole("dialog", {
      name: "Configuration changed in AstrBot",
    });
    expect(
      within(dialog).getAllByText("general.bot_name", { selector: "code" }),
    ).toHaveLength(3);
    expect(
      screen.getByRole("button", {
        name: "Apply configuration",
        hidden: true,
      }),
    ).toHaveProperty("disabled", true);

    fireEvent.click(
      within(dialog).getByRole("button", { name: "Load AstrBot version" }),
    );

    await waitFor(() => expect(name).toHaveProperty("value", "AstrBot copy"));
    expect(screen.getByText("Synced")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(bridge.apiPost).not.toHaveBeenCalled();
  });

  it("rebases a dirty conflict without saving and posts only after explicit Apply", async () => {
    const remoteConfig: ConfigObject = {
      ...baseConfig,
      general: {
        ...(baseConfig.general as ConfigObject),
        bot_name: "AstrBot copy",
        score_threshold: 0.9,
      },
    };
    let stateAttempts = 0;
    stateHandler = async () => {
      stateAttempts += 1;
      return (stateAttempts === 1
        ? state()
        : state(remoteConfig, "rev-2")) as ApiResponse;
    };
    render(<ConfigPage />);

    const name = await screen.findByRole("textbox", { name: "Bot name" });
    fireEvent.change(name, { target: { value: "Local copy" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh configuration" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Configuration changed in AstrBot",
    });

    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Reapply my changes on latest version",
      }),
    );

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(name).toHaveProperty("value", "Local copy");
    expect(
      screen.getByRole("spinbutton", { name: "Score threshold" }),
    ).toHaveProperty("value", "0.9");
    expect(screen.getByText("Unsaved changes")).toBeTruthy();
    expect(bridge.apiPost).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getByRole("button", { name: "Apply configuration" }),
    );
    await waitFor(() => {
      expect(bridge.apiPost).toHaveBeenCalledWith("page/config/apply", {
        base_revision: "rev-2",
        changes: { "general.bot_name": "Local copy" },
      });
    });
  });

  it("recovers the full page when initial offline Retry succeeds", async () => {
    let schemaAttempts = 0;
    schemaHandler = async () => {
      schemaAttempts += 1;
      if (schemaAttempts === 1) throw new Error("bridge disconnected");
      return ok(schemaData) as ApiResponse;
    };
    render(<ConfigPage />);

    expect(await screen.findByText("Cannot reach AstrBot")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Bot name" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("textbox", { name: "Bot name" })).toHaveProperty(
      "value",
      "Memora",
    );
    expect(schemaAttempts).toBe(2);
    expect(
      bridge.apiGet.mock.calls.filter(
        ([endpoint]) => endpoint === "page/config/state",
      ),
    ).toHaveLength(2);
  });

  it("keeps an enabled dirty draft visible when a loaded refresh goes offline", async () => {
    let stateAttempts = 0;
    stateHandler = async () => {
      stateAttempts += 1;
      if (stateAttempts === 1) return state() as ApiResponse;
      throw new Error("network down");
    };
    render(<ConfigPage />);

    const name = await screen.findByRole("textbox", { name: "Bot name" });
    fireEvent.change(name, { target: { value: "Local copy" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh configuration" }),
    );

    expect(
      await screen.findByText(
        "The connection was lost. Your draft is preserved; refresh or retry applying after reconnecting.",
      ),
    ).toBeTruthy();
    expect(name).toHaveProperty("value", "Local copy");
    expect(name).toHaveProperty("disabled", false);
    expect(
      screen.getByRole("button", { name: "Apply configuration" }),
    ).toHaveProperty("disabled", false);
  });

  it("renders core page and conflict commands in English, Russian, and Chinese", async () => {
    const remoteConfig: ConfigObject = {
      ...baseConfig,
      general: {
        ...(baseConfig.general as ConfigObject),
        bot_name: "AstrBot copy",
      },
    };
    let stateAttempts = 0;
    stateHandler = async () => {
      stateAttempts += 1;
      return (stateAttempts === 1
        ? state()
        : state(remoteConfig, "rev-2")) as ApiResponse;
    };
    render(<ConfigPage />);

    const name = await screen.findByRole("textbox", { name: "Bot name" });
    expect(
      screen.getByRole("button", { name: "Apply configuration" }),
    ).toBeTruthy();
    fireEvent.change(name, { target: { value: "Local copy" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh configuration" }),
    );

    expect(
      await screen.findByRole("button", { name: "Load AstrBot version" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", {
        name: "Reapply my changes on latest version",
      }),
    ).toBeTruthy();

    act(() => {
      toggleLanguage();
    });
    expect(await screen.findByText("Конфигурация")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Загрузить версию AstrBot" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", {
        name: "Повторно применить мои изменения к последней версии",
      }),
    ).toBeTruthy();

    act(() => {
      toggleLanguage();
    });
    expect(await screen.findByText("配置")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "载入 AstrBot 版本" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", {
        name: "在最新版本上重新应用我的更改",
      }),
    ).toBeTruthy();
  });

  it("uses the page toast convention for a successful explicit apply without replacing sync status", async () => {
    const showToast = vi.fn();
    render(<ConfigPage showToast={showToast} />);

    const name = await screen.findByRole("textbox", { name: "Bot name" });
    for (
      let attempt = 0;
      attempt < 3 &&
      !screen.queryByRole("button", { name: "Apply configuration" });
      attempt += 1
    ) {
      act(() => {
        toggleLanguage();
      });
    }

    fireEvent.change(name, {
      target: { value: "Archive" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Apply configuration" }),
    );

    await waitFor(() => expect(screen.getByText("Synced")).toBeTruthy());
    expect(showToast).toHaveBeenCalledWith(
      "Configuration applied",
      "success",
    );
  });
});
