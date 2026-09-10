import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("@/components/intelligence/RecallTracePanel", () => ({
  RecallTracePanel: ({ navigationTarget }: {
    navigationTarget?: {
      requestId: number;
      tab: string;
      traceId?: string;
    } | null;
  }) => (
    <output data-testid="recall-trace-navigation-target">
      {navigationTarget
        ? `${navigationTarget.requestId}:${navigationTarget.tab}:${navigationTarget.traceId ?? ""}`
        : "none"}
    </output>
  ),
}));

import { IntelligencePage } from "./IntelligencePage";

afterEach(() => {
  cleanup();
});

it("switches intelligence tabs", async () => {
  render(<IntelligencePage showToast={() => undefined} />);

  const page = screen.getByRole("region", { name: /Intelligence|智能/ });
  expect(page.getAttribute("data-layout")).toBe("standard");
  expect(page.querySelector('[data-slot="page-header"]')).toBeTruthy();
  expect(screen.getByRole("tablist", { name: /Intelligence|智能/ })).toBeTruthy();
  expect(screen.getByRole("tab", { name: /Evaluation|评测/ })).toBeTruthy();
  const evaluationTab = screen.getByRole("tab", { name: /Evaluation|评测/ });
  const recallTraceTab = screen.getByRole("tab", { name: /Recall Trace|召回链路/ });
  evaluationTab.focus();
  fireEvent.keyDown(evaluationTab, { key: "ArrowRight" });
  expect(recallTraceTab.getAttribute("tabindex")).toBe("0");

  fireEvent.click(screen.getByRole("tab", { name: /Diagnostics|诊断/ }));
  expect(screen.getByRole("tab", { name: /Diagnostics|诊断/ }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getByRole("tabpanel", { name: /Diagnostics|诊断/ })).toBeTruthy();
  expect(screen.getAllByText(/^(Health|健康)$/).length).toBeGreaterThan(0);
});

it("selects Recall Trace and forwards a persisted trace target", () => {
  render(
    <IntelligencePage
      showToast={() => undefined}
      navigationTarget={{
        requestId: 42,
        tab: "recallTrace",
        traceId: "trace-persisted",
      }}
    />,
  );

  expect(
    screen.getByRole("tab", { name: /Recall Trace|召回链路/ }).getAttribute("aria-selected"),
  ).toBe("true");
  expect(screen.getByTestId("recall-trace-navigation-target").textContent).toBe(
    "42:recallTrace:trace-persisted",
  );
});
