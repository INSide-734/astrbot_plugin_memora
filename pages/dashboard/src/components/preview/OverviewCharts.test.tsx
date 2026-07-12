import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GrowthTrendChart, RankedBars, StatusComposition } from "./OverviewCharts";

afterEach(() => vi.restoreAllMocks());

describe("overview chart number formatting", () => {
  it("formats textual chart values with the dashboard locale", () => {
    const localeSpy = vi.spyOn(Number.prototype, "toLocaleString");

    render(<>
      <StatusComposition
        ariaLabel="Status composition"
        locale="ru-RU"
        items={[{ name: "Active", count: 1234, colorClass: "bg-primary" }]}
      />
      <RankedBars
        ariaLabel="Sessions"
        locale="ru-RU"
        items={[{ name: "session", count: 5678 }]}
      />
    </>);

    const statusComposition = screen.getByRole("img", { name: "Status composition" });
    expect(statusComposition).toBeTruthy();
    expect(localeSpy).toHaveBeenCalledWith("ru-RU");
    expect(statusComposition.querySelector(".text-xs.font-normal")?.textContent).toBe(
      new Intl.NumberFormat("ru-RU", {
      style: "percent",
      maximumFractionDigits: 0,
      }).format(1),
    );
  });

  it("formats trend axis dates with the dashboard locale", () => {
    const localeSpy = vi.spyOn(Date.prototype, "toLocaleDateString").mockReturnValue("28.06");

    render(
      <GrowthTrendChart
        ariaLabel="Memory growth"
        locale="ru-RU"
        valueLabel="Memories"
        data={[
          { date: "2026-06-27", count: 1 },
          { date: "2026-06-28", count: 2 },
        ]}
      />,
    );

    expect(localeSpy).toHaveBeenCalledWith("ru-RU", {
      day: "numeric",
      month: "numeric",
      timeZone: "UTC",
    });
  });
});
