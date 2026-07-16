import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useInjectionDecisions } from "@/hooks/useInjectionDecisions";
import { useInjectionStrategyConfig } from "@/hooks/useInjectionStrategyConfig";
import { useInjectionStrategySummary } from "@/hooks/useInjectionStrategySummary";
import {
  DEFAULT_INJECTION_FILTERS,
  DEFAULT_INJECTION_STRATEGY,
  type InjectionContentLevel,
  type InjectionDecisionDetail,
  type InjectionDecisionListItem,
  type InjectionDecisionPage,
  type InjectionPresetName,
  type InjectionRecentEvent,
  type InjectionStrategyCatalog,
  type InjectionStrategyDraft,
  type InjectionStrategySummary,
} from "@/types/injection";

import { InjectionStrategyPage } from "./InjectionStrategyPage";

const hooks = vi.hoisted(() => ({
  config: null as unknown as ReturnType<typeof useInjectionStrategyConfig>,
  summary: null as unknown as ReturnType<typeof useInjectionStrategySummary>,
  decisions: null as unknown as ReturnType<typeof useInjectionDecisions>,
}));

vi.mock("@/hooks/useInjectionStrategyConfig", () => ({
  useInjectionStrategyConfig: () => hooks.config,
}));
vi.mock("@/hooks/useInjectionStrategySummary", () => ({
  useInjectionStrategySummary: () => hooks.summary,
}));
vi.mock("@/hooks/useInjectionDecisions", () => ({
  useInjectionDecisions: () => hooks.decisions,
}));
vi.mock("@/hooks/useI18n", () => ({
  useI18n: () => ({
    t: (key: string) => key,
    currentLang: () => "en",
  }),
}));

function draftFixture(
  overrides: Partial<InjectionStrategyDraft> = {},
): InjectionStrategyDraft {
  return { ...DEFAULT_INJECTION_STRATEGY, ...overrides };
}

function decisionEvent(
  overrides: Partial<InjectionRecentEvent> = {},
): InjectionRecentEvent {
  return {
    decision_id: "00000000-0000-4000-8000-000000000001",
    created_at_ms: Date.UTC(2026, 6, 15, 8),
    trace_id: null,
    routing_mode: "manual",
    resolved_preset: "balanced",
    outcome: "injected",
    primary_reason: "MANUAL_SELECTED",
    fallback_applied: false,
    actual_payload_chars: 600,
    ...overrides,
  };
}

function decisionDetail(
  overrides: Partial<InjectionDecisionDetail> = {},
): InjectionDecisionDetail {
  return {
    ...decisionEvent(),
    configured_preset: "balanced",
    recommended_preset: "balanced",
    preferred_delivery: "extra_user_content",
    resolved_delivery: "extra_user_content",
    provider_type: "openai",
    provider_model: "mock-model",
    error_code: null,
    candidate_count: 4,
    selected_count: 2,
    dropped_count: 2,
    truncated_count: 0,
    configured_budget_chars: 1_740,
    effective_budget_chars: 1_740,
    context_headroom_chars: 8_000,
    decision_ms: 0.5,
    format_ms: 1.2,
    inject_ms: 0.3,
    reason_codes: ["MANUAL_SELECTED"],
    ...overrides,
  };
}

function decisionRow(
  overrides: Partial<InjectionDecisionListItem> = {},
): InjectionDecisionListItem {
  const { reason_codes: _reasonCodes, ...row } = decisionDetail(overrides);
  return row;
}

function decisionPageFixture(
  overrides: Partial<InjectionDecisionPage> = {},
): InjectionDecisionPage {
  return {
    items: [decisionRow()],
    total: 1,
    offset: 0,
    limit: 25,
    ...overrides,
  };
}

function summaryFixture(
  overrides: Partial<InjectionStrategySummary> = {},
): InjectionStrategySummary {
  return {
    window: "24h",
    decision_count: 1,
    payload_chars_p95: 600,
    provider_fallback_rate: 0,
    preset_distribution: { balanced: 1 },
    cost_trend: [{
      bucket_ms: Date.UTC(2026, 6, 15, 8),
      decision_count: 1,
      payload_chars_p95: 600,
      provider_fallback_rate: 0,
    }],
    recent_events: [decisionEvent()],
    ...overrides,
  };
}

function catalogFixture(): InjectionStrategyCatalog {
  const definitions: Array<[
    InjectionPresetName,
    number,
    boolean,
    number,
    number,
    InjectionContentLevel,
  ]> = [
    ["tool_first", 0, false, 0, 0, "NONE"],
    ["low_cost", 1, true, 800, 2, "FACTS"],
    ["balanced", 2, true, 1200, 4, "COMPACT"],
    ["quality", 3, true, 2400, 6, "DETAILED"],
  ];
  return {
    routing_modes: ["manual", "auto", "hybrid"],
    presets: definitions.map(([
      name,
      rank,
      autoInject,
      budget,
      maxMemories,
      contentLevel,
    ]) => ({
      name,
      rank,
      auto_inject: autoInject,
      memory_budget_chars: budget,
      max_memories: maxMemories,
      content_level: contentLevel,
      cost_penalty_weight: name === "tool_first"
        ? 1
        : name === "low_cost"
          ? 0.3
          : name === "balanced"
            ? 0.18
            : 0.08,
      minimum_utility: name === "tool_first"
        ? 1
        : name === "low_cost"
          ? 0.45
          : name === "balanced"
            ? 0.3
            : 0.2,
      allow_tool_fallback: true,
      preferred_delivery: "extra_user_content",
    })),
    deliveries: [
      "auto",
      "extra_user_content",
      "user_message_before",
      "user_message_after",
      "fake_tool_call",
      "fake_tool_call_deepseek_v4",
    ],
    retention_options: [7, 30, 90, 180, 0],
    provider_tools_supported: true,
    memory_tool_available: true,
    recall_trace_available: true,
    effective_default_delivery: "extra_user_content",
  };
}

function resetHookHarness(): void {
  hooks.config = {
    catalog: catalogFixture(),
    catalogStatus: "success",
    catalogError: null,
    retryCatalog: vi.fn(),
    draft: draftFixture(),
    base: draftFixture(),
    errors: {},
    serverFieldErrors: {},
    status: "synced",
    revision: "rev-1",
    dirty: false,
    dirtyPaths: [],
    canSave: false,
    change: vi.fn(),
    restoreDefaults: vi.fn(),
    discard: vi.fn(),
    save: vi.fn(),
    acceptRemote: vi.fn(),
    rebaseRemote: vi.fn(),
    refresh: vi.fn(),
    localPaths: [],
    remotePaths: [],
    overlapPaths: [],
    remoteReady: false,
  };
  hooks.summary = {
    windowValue: "24h",
    setWindowValue: vi.fn(),
    status: "success",
    data: summaryFixture(),
    error: null,
    refresh: vi.fn(),
  };
  hooks.decisions = {
    filters: DEFAULT_INJECTION_FILTERS,
    page: decisionPageFixture(),
    offset: 0,
    limit: 25,
    status: "success",
    error: null,
    setFilter: vi.fn(),
    setFilters: vi.fn(),
    setOffset: vi.fn(),
    setLimit: vi.fn(),
    refresh: vi.fn(),
    detailStatus: "idle",
    detail: null,
    detailError: null,
    loadDetail: vi.fn(),
    clearDetail: vi.fn(),
  };
}

let view: ReturnType<typeof render>;
const showToast = vi.fn();
const onNavigate = vi.fn();

function renderPage() {
  view = render(
    <InjectionStrategyPage showToast={showToast} onNavigate={onNavigate} />,
  );
  return view;
}

function rerenderPage() {
  view.rerender(
    <InjectionStrategyPage showToast={showToast} onNavigate={onNavigate} />,
  );
}

describe("InjectionStrategyPage", () => {
  beforeEach(() => {
    resetHookHarness();
    showToast.mockReset();
    onNavigate.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("uses the standard page frame and protects a dirty configuration tab", () => {
    renderPage();
    expect(
      screen.getByLabelText("injection.title").getAttribute("data-layout"),
    ).toBe("standard");

    fireEvent.click(screen.getByRole("tab", { name: "injection.tabs.config" }));
    hooks.config.dirty = true;
    rerenderPage();
    fireEvent.click(screen.getByRole("tab", { name: "injection.tabs.decisions" }));

    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(
      document.getElementById("injection-tab-config")
        ?.getAttribute("aria-selected"),
    ).toBe("true");
    fireEvent.click(
      screen.getByRole("button", { name: "config.unsaved.discard" }),
    );
    expect(hooks.config.discard).toHaveBeenCalledOnce();
    expect(
      document.getElementById("injection-tab-decisions")
        ?.getAttribute("aria-selected"),
    ).toBe("true");
  });

  it("renders minimal real state boundaries for all three tabs", () => {
    renderPage();

    expect(
      screen.getByRole("region", { name: "injection.overview.title" }).textContent,
    ).toContain("manual");

    fireEvent.click(screen.getByRole("tab", { name: "injection.tabs.config" }));
    expect(
      screen.getByRole("region", { name: "injection.config.title" }).textContent,
    ).toContain("manual");

    fireEvent.click(screen.getByRole("tab", { name: "injection.tabs.decisions" }));
    expect(
      screen.getByRole("region", { name: "injection.decisions.title" }).textContent,
    ).toContain("1");
  });

  it("consumes a one-shot target and reports dirty ownership through unmount", () => {
    const onDirtyChange = vi.fn();
    const { rerender, unmount } = render(
      <InjectionStrategyPage
        showToast={showToast}
        onNavigate={onNavigate}
        onDirtyChange={onDirtyChange}
        navigationTarget={{ requestId: 7, tab: "decisions" }}
      />,
    );

    expect(
      screen.getByRole("tab", { name: "injection.tabs.decisions" })
        .getAttribute("aria-selected"),
    ).toBe("true");

    hooks.config.dirty = true;
    rerender(
      <InjectionStrategyPage
        showToast={showToast}
        onNavigate={onNavigate}
        onDirtyChange={onDirtyChange}
        navigationTarget={{ requestId: 7, tab: "decisions" }}
      />,
    );
    expect(onDirtyChange).toHaveBeenCalledWith(true);

    unmount();
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });
});
