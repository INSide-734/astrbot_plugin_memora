import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MOCK_GATE_CONFIG } from "@/mock/data";
import type {
  ConfigApiResponse,
  ConfigApplyData,
  ConfigObject,
  ConfigSchemaData,
  ConfigStateData,
  GateConfigData,
} from "@/types/config";
import { GatePage } from "./GatePage";

interface ApiResponse {
  status: string;
  data?: unknown;
  message?: string;
  code?: string;
  field_errors?: Record<string, string>;
}

interface BridgeMock {
  apiGet: ReturnType<typeof vi.fn>;
  apiPost: ReturnType<typeof vi.fn>;
  getLocale: ReturnType<typeof vi.fn>;
  getI18n: ReturnType<typeof vi.fn>;
  t: ReturnType<typeof vi.fn>;
  onContext: ReturnType<typeof vi.fn>;
}

function ok<T>(data: T): ConfigApiResponse<T> {
  return { status: "ok", data };
}

const schemaData: ConfigSchemaData = {
  plugin_name: "astrbot_plugin_memora",
  schema: {},
  provider_options: { llm: [], embedding: [] },
  capabilities: { hot_reload: true },
};

function gateStateConfig(): ConfigObject {
  return {
    quality: { gate: structuredClone(MOCK_GATE_CONFIG) },
  };
}

function state(
  config: ConfigObject = gateStateConfig(),
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
    changed_paths: ["quality.gate.enabled"],
    reload_scheduled: false,
    restart_required: true,
    rebuild_required: false,
    instance_id: "instance-1",
    ...overrides,
  });
}


describe("GatePage", () => {
  let bridge: BridgeMock;
  let schemaHandler: () => Promise<ApiResponse>;
  let stateHandler: (params: Record<string, string>) => Promise<ApiResponse>;
  let applyHandler: (body: unknown) => Promise<ApiResponse>;
  let dryRunHandler: (body: unknown) => Promise<ApiResponse>;

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
    dryRunHandler = async () =>
      ok({
        profile: "private",
        quality: "normal",
        matched_rules: [],
        disposition: "quarantine",
      }) as ApiResponse;

    bridge = {
      apiGet: vi.fn((endpoint: string, params: Record<string, string> = {}) => {
        if (endpoint === "page/config/schema") return schemaHandler();
        if (endpoint === "page/config/state") return stateHandler(params);
        return Promise.reject(new Error(`Unexpected GET endpoint: ${endpoint}`));
      }),
      apiPost: vi.fn((endpoint: string, body: unknown) => {
        if (endpoint === "page/config/apply") return applyHandler(body);
        if (endpoint === "page/gate/dry-run") return dryRunHandler(body);
        return Promise.reject(new Error(`Unexpected POST endpoint: ${endpoint}`));
      }),
      getLocale: vi.fn().mockReturnValue("en-US"),
      getI18n: vi.fn().mockReturnValue({}),
      t: vi.fn((key: string) => key),
      onContext: vi.fn().mockReturnValue(vi.fn()),
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
    vi.restoreAllMocks();
    vi.useRealTimers();
    localStorage.clear();
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: undefined,
    });
  });

  const renderPage = async () => {
    const view = render(<GatePage />);
    await flushMicrotasks();
    return view;
  };

  it("renders the eight gate sections after the config loads", async () => {
    await renderPage();

    expect(
      await screen.findByRole("heading", { name: "Memory Write Gate" }),
    ).toBeTruthy();
    for (const section of [
      "Profiles & bindings",
      "Checks",
      "Thresholds & scoring",
      "Word lists",
      "Disposition",
      "Judge",
      "Rules",
      "Dry-run test",
    ]) {
      expect(screen.getByRole("group", { name: section })).toBeTruthy();
    }
  });

  it("saves only the quality.gate.enabled leaf when the master switch flips", async () => {
    await renderPage();

    const masterSwitch = await screen.findByRole("switch", {
      name: "Gate master switch",
    });
    expect(masterSwitch.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(masterSwitch);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Save configuration" }),
      ).toHaveProperty("disabled", false);
    });
    fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    const applyCall = bridge.apiPost.mock.calls[0] as unknown[];
    expect(applyCall[0]).toBe("page/config/apply");
    const body = applyCall[1] as {
      base_revision: string;
      changes: Record<string, unknown>;
    };
    expect(Object.keys(body.changes).sort()).toEqual(["quality.gate.enabled"]);
    expect(body.changes["quality.gate.enabled"]).toBe(false);
  });

  it("saves only the quality.gate.profiles leaf for a profile-internal edit", async () => {
    await renderPage();

    const numericCheck = await screen.findByRole("checkbox", {
      name: "Numeric conflict check",
    });
    fireEvent.click(numericCheck);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Save configuration" }),
      ).toHaveProperty("disabled", false);
    });
    fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));

    await waitFor(() => expect(bridge.apiPost).toHaveBeenCalledTimes(1));
    const body = bridge.apiPost.mock.calls[0][1] as {
      changes: Record<string, unknown>;
    };
    expect(Object.keys(body.changes).sort()).toEqual(["quality.gate.profiles"]);
    const profiles = body.changes["quality.gate.profiles"] as GateConfigData["profiles"];
    expect(profiles[0].checks.numeric_check).toBe(false);
    expect(profiles[1].checks.numeric_check).toBe(true);
  });

  it("disables saving when min_judge exceeds min_deterministic", async () => {
    await renderPage();

    const judgeSlider = await screen.findByRole("slider", {
      name: "Judge support score",
    });
    fireEvent.change(judgeSlider, { target: { value: "0.9" } });

    expect(
      screen.getByText(
        "Judge support score must not exceed the deterministic pass score",
      ),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Save configuration" }),
    ).toHaveProperty("disabled", true);
    expect(
      screen.getByText(
        "The configuration has validation errors and cannot be saved",
      ),
    ).toBeTruthy();
  });

  it("reports a judge template missing placeholders and disables saving", async () => {
    await renderPage();

    fireEvent.click(
      await screen.findByRole("switch", { name: "Enable Judge review" }),
    );
    const template = await screen.findByRole("textbox", {
      name: "Prompt template (empty = built-in)",
    });
    fireEvent.change(template, { target: { value: "Check: {claim_text}" } });

    expect(
      screen.getByText("Template must contain {claim_text} and {source_text}"),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Save configuration" }),
    ).toHaveProperty("disabled", true);
  });

  it("adds, edits, and deletes a rule through the controlled sheet", async () => {
    await renderPage();

    // 新增
    fireEvent.click(await screen.findByRole("button", { name: "New rule" }));
    const createSheet = await screen.findByRole("dialog", {
      name: "New rule",
    });
    fireEvent.change(within(createSheet).getByRole("textbox", { name: "Rule ID" }), {
      target: { value: "r2" },
    });
    fireEvent.change(
      within(createSheet).getByRole("textbox", { name: "Description" }),
      { target: { value: "my rule" } },
    );
    fireEvent.change(within(createSheet).getByRole("textbox", { name: "Pattern" }), {
      target: { value: "abc" },
    });
    await waitFor(() => {
      expect(
        within(createSheet).getByRole("button", { name: "Save rule" }),
      ).toHaveProperty("disabled", false);
    });
    fireEvent.click(within(createSheet).getByRole("button", { name: "Save rule" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "New rule" })).toBeNull(),
    );
    let ruleRow = screen.getByText("r2").closest("li");
    if (!ruleRow) throw new Error("expected rule row");
    expect(within(ruleRow).getByText("my rule")).toBeTruthy();

    // 编辑
    fireEvent.click(within(ruleRow).getByRole("button", { name: "Edit rule" }));
    const editSheet = await screen.findByRole("dialog", { name: "Edit rule" });
    fireEvent.change(
      within(editSheet).getByRole("textbox", { name: "Description" }),
      { target: { value: "updated rule" } },
    );
    fireEvent.click(within(editSheet).getByRole("button", { name: "Save rule" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Edit rule" })).toBeNull(),
    );
    ruleRow = screen.getByText("r2").closest("li");
    if (!ruleRow) throw new Error("expected rule row");
    expect(within(ruleRow).getByText("updated rule")).toBeTruthy();

    // 删除
    fireEvent.click(within(ruleRow).getByRole("button", { name: "Delete rule" }));
    await waitFor(() => expect(screen.queryByText("r2")).toBeNull());
  });

  it("runs a dry-run and renders the deterministic mock result", async () => {
    await renderPage();

    const content = await screen.findByRole("textbox", { name: "Content" });
    fireEvent.change(content, { target: { value: "今天天气不错" } });
    fireEvent.click(screen.getByRole("button", { name: "Run dry-run" }));

    const resultPanel = await screen.findByRole("status");
    expect(within(resultPanel).getByText("private")).toBeTruthy();
    expect(within(resultPanel).getByText("Normal")).toBeTruthy();
    expect(within(resultPanel).getByText("Quarantine (manual review)")).toBeTruthy();
    expect(within(resultPanel).getByText("None")).toBeTruthy();

    expect(bridge.apiPost).toHaveBeenCalledWith(
      "page/gate/dry-run",
      expect.objectContaining({ content: "今天天气不错", chat_type: "private" }),
    );
  });

  it("keeps dry-run controls enabled while the page is dirty", async () => {
    await renderPage();

    const numericCheck = await screen.findByRole("checkbox", {
      name: "Numeric conflict check",
    });
    fireEvent.click(numericCheck);
    fireEvent.change(screen.getByRole("textbox", { name: "Content" }), {
      target: { value: "draft content" },
    });

    expect(
      screen.getByRole("button", { name: "Run dry-run" }),
    ).toHaveProperty("disabled", false);
  });
});
