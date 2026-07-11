import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  MetricGrid,
  PageContent,
  PageFrame,
  PageHeader,
  PageToolbar,
} from "./PageLayout";

afterEach(cleanup);

describe("PageLayout", () => {
  it("provides a semantic page shell with header, toolbar, and constrained content", () => {
    render(
      <PageFrame variant="standard" aria-label="Memory workspace">
        <PageHeader
          title="Memories"
          description="Browse and manage stored memories"
          actions={<button type="button">Create</button>}
        />
        <PageToolbar aria-label="Memory filters">Filters</PageToolbar>
        <PageContent>Results</PageContent>
      </PageFrame>,
    );

    const frame = screen.getByRole("region", { name: "Memory workspace" });
    expect(frame.getAttribute("data-layout")).toBe("standard");
    expect(screen.getByRole("heading", { name: "Memories", level: 1 })).toBeTruthy();
    expect(screen.getByText("Browse and manage stored memories")).toBeTruthy();
    expect(screen.getByRole("toolbar", { name: "Memory filters" })).toBeTruthy();
    expect(screen.getByText("Results").getAttribute("data-slot")).toBe("page-content");
    const actions = screen.getByRole("button", { name: "Create" }).parentElement;
    expect(actions?.getAttribute("data-slot")).toBe("page-header-actions");
    expect(actions?.className).toContain("w-full");
    expect(actions?.className).toContain("min-w-0");
    expect(actions?.className).toContain("sm:w-auto");
  });

  it("supports stable workspace and dense variants", () => {
    const { rerender } = render(<PageFrame variant="workspace">Canvas</PageFrame>);
    expect(screen.getByText("Canvas").getAttribute("data-layout")).toBe("workspace");

    rerender(<PageFrame variant="dense">Rows</PageFrame>);
    expect(screen.getByText("Rows").getAttribute("data-layout")).toBe("dense");
  });

  it("uses a responsive metric grid without card nesting", () => {
    render(
      <MetricGrid minItemWidth="12rem">
        <div>One</div>
        <div>Two</div>
      </MetricGrid>,
    );

    const grid = screen.getByText("One").parentElement;
    expect(grid?.getAttribute("data-slot")).toBe("metric-grid");
    expect(grid?.getAttribute("style")).toContain("12rem");
  });
});
