import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoraLogo } from "./MemoraLogo";

describe("MemoraLogo", () => {
  it("renders an accessible scalable svg with the requested size", () => {
    render(<MemoraLogo size={32} className="text-sidebar-primary-foreground" />);

    const logo = screen.getByRole("img", { name: "Memora" });
    expect(logo.tagName).toBe("svg");
    expect(logo.getAttribute("width")).toBe("32");
    expect(logo.getAttribute("height")).toBe("32");
    expect(logo.getAttribute("class")).toContain("text-sidebar-primary-foreground");
    expect(logo.getAttribute("viewBox")).toBe("0 0 24 24");
  });
});
