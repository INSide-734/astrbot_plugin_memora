import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChartTooltipContent } from "./chart";

describe("ChartTooltipContent", () => {
  beforeEach(() => {
    Object.defineProperty(window, "AstrBotPluginPage", {
      configurable: true,
      value: {
        getLocale: vi.fn().mockReturnValue("en-US"),
        getI18n: vi.fn().mockReturnValue({}),
        t: vi.fn((key: string) => key),
      },
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

  it("formats numeric values with the dashboard locale", () => {
    const localeSpy = vi.spyOn(Number.prototype, "toLocaleString");

    render(
      <ChartTooltipContent
        active
        label="2026-07-12"
        valueLabel="Memories"
        payload={[{ value: 1234, graphicalItemId: "memories" }]}
      />,
    );

    expect(screen.getByText("Memories")).toBeTruthy();
    expect(localeSpy).toHaveBeenCalledWith("en-US");
  });

  it("formats tooltip labels when the chart supplies a locale-aware formatter", () => {
    render(
      <ChartTooltipContent
        active
        label="2026-07-12"
        valueLabel="Memories"
        formatLabel={() => "July 12, 2026"}
        payload={[{ value: 12, graphicalItemId: "memories" }]}
      />,
    );

    expect(screen.getByText("July 12, 2026")).toBeTruthy();
    expect(screen.queryByText("2026-07-12")).toBeNull();
  });
});
