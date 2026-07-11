import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Sheet, SheetContent, SheetDescription, SheetTitle } from "./sheet";

afterEach(cleanup);

describe("Sheet", () => {
  it("renders an accessible right-side detail surface and closes it", () => {
    render(
      <Sheet defaultOpen>
        <SheetContent side="right" aria-label="Memory details">
          <SheetTitle>Memory details</SheetTitle>
          <SheetDescription>Inspect and edit the selected memory.</SheetDescription>
        </SheetContent>
      </Sheet>,
    );

    const sheet = screen.getByRole("dialog", { name: "Memory details" });
    expect(sheet.getAttribute("data-side")).toBe("right");
    fireEvent.click(screen.getByRole("button", { name: "Close panel" }));
    expect(screen.queryByRole("dialog", { name: "Memory details" })).toBeNull();
  });
});
