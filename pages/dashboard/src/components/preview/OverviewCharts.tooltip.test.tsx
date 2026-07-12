import type { ReactNode } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let tooltipLabel = "";

vi.mock("recharts", () => ({
  Area: () => null,
  AreaChart: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Bar: () => null,
  BarChart: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  CartesianGrid: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));

vi.mock("@/components/ui/chart", () => ({
  ChartContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  ChartTooltip: ({ content }: { content: ReactNode }) => <>{content}</>,
  ChartTooltipContent: ({ formatLabel }: { formatLabel?: (label: string) => ReactNode }) => (
    <span data-testid="tooltip-label">
      {formatLabel ? formatLabel(tooltipLabel) : tooltipLabel}
    </span>
  ),
}));

import { GrowthTrendChart, ImportanceDistribution } from "./OverviewCharts";

describe("overview chart tooltip labels", () => {
  beforeEach(() => {
    tooltipLabel = "";
  });

  afterEach(cleanup);

  it("formats the full trend date with the dashboard locale", () => {
    tooltipLabel = "2026-06-28";

    render(
      <GrowthTrendChart
        ariaLabel="Memory growth"
        locale="ru-RU"
        valueLabel="Memories"
        data={[{ date: tooltipLabel, count: 2 }]}
      />,
    );

    expect(screen.getByTestId("tooltip-label").textContent).toBe("28.06.2026");
  });

  it("keeps importance distribution interval labels unchanged", () => {
    tooltipLabel = "0-1";

    render(
      <ImportanceDistribution
        ariaLabel="Importance distribution"
        valueLabel="Memories"
        items={[{ name: tooltipLabel, count: 2 }]}
      />,
    );

    expect(screen.getByTestId("tooltip-label").textContent).toBe("0-1");
  });
});
