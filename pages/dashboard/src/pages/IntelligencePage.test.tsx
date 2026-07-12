import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { IntelligencePage } from "./IntelligencePage";

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
