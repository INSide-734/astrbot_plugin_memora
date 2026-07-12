import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReviewItemDetail } from "./ReviewItemDetail";

describe("ReviewItemDetail", () => {
  const timestamp = 1783150200;

  beforeEach(() => {
    const translations: Record<string, string> = {
      "dashboard.intelligence.review.status.open": "Открыто",
      "dashboard.severity.medium": "Средняя",
      "dashboard.intelligence.review.reason.duplicate": "Дубликат",
      "dashboard.intelligence.review.action.approved": "Одобрено",
    };
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: {
        getLocale: vi.fn().mockReturnValue("ru-RU"),
        getI18n: vi.fn().mockReturnValue({}),
        t: vi.fn((key: string) => translations[key] ?? key),
      },
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    Object.defineProperty(window, "AstrBotPluginPage", { configurable: true, value: undefined });
  });

  it("localizes known detail enums and date while keeping an unknown action raw", () => {
    render(
      <ReviewItemDetail
        item={{
          item_id: "review-1",
          memory_id: "memory-1",
          reasons: ["duplicate"],
          severity: "medium",
          status: "open",
          content_preview: "content",
          metadata: {},
          created_at: timestamp,
          updated_at: timestamp,
        }}
        actions={[
          { action_id: "action-1", item_id: "review-1", action: "approved", actor_id: "operator", payload: {}, created_at: timestamp },
          { action_id: "action-2", item_id: "review-1", action: "custom_action", actor_id: "operator", payload: {}, created_at: timestamp },
        ]}
        loading={false}
        submitting={false}
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByText("Средняя")).toBeTruthy();
    expect(screen.getByText("Открыто")).toBeTruthy();
    expect(screen.getByText("Дубликат")).toBeTruthy();
    expect(screen.getByText("Одобрено")).toBeTruthy();
    expect(screen.getByText("custom_action")).toBeTruthy();
    expect(screen.getAllByText(new Date(timestamp * 1000).toLocaleString("ru-RU")).length).toBe(2);
    expect(screen.queryByText("medium")).toBe(null);
    expect(screen.queryByText("open")).toBe(null);
    expect(screen.queryByText("duplicate")).toBe(null);
  });
});
