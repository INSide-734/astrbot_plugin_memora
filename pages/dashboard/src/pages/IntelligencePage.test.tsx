import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { IntelligencePage } from "./IntelligencePage";

it("switches intelligence tabs", async () => {
  render(<IntelligencePage showToast={() => undefined} />);

  expect(screen.getByRole("tab", { name: /Evaluation|评测/ })).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: /Diagnostics|诊断/ }));
  expect(screen.getByText(/Health|健康/)).toBeTruthy();
});
