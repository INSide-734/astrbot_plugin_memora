import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Progress } from "./Progress";

describe("Progress", () => {
  it("renders a visible, accessible meter and clamps its fill", () => {
    const { rerender } = render(<Progress aria-label="Quality" value={0.72} />);

    const meter = screen.getByRole("progressbar", { name: "Quality" });
    expect(meter.getAttribute("aria-valuenow")).toBe("0.72");
    expect(meter.className).toContain("h-2.5");
    expect(meter.className).toContain("bg-muted");
    expect(meter.className).toContain("border-border");
    expect(meter.className).not.toContain("ring-foreground/10");
    expect(meter.firstElementChild?.getAttribute("style")).toContain("72%");

    rerender(<Progress aria-label="Quality" value={4} />);
    expect(screen.getByRole("progressbar", { name: "Quality" }).firstElementChild?.getAttribute("style")).toContain("100%");
  });
});
