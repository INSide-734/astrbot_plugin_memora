import { render, screen } from "@testing-library/react";
import { Tag } from "lucide-react";
import { describe, expect, it } from "vitest";

import { Badge } from "./Badge";

describe("Badge", () => {
  it("uses Tailwind 3 compatible radius and icon sizing", () => {
    render(<Badge variant="outline"><Tag />Label</Badge>);

    const badge = screen.getByText("Label");
    expect(badge.className).toContain("rounded-full");
    expect(badge.className).toContain("[&>svg]:size-3");
    expect(badge.className).not.toContain("rounded-4xl");
    expect(badge.className).not.toContain("size-3!");
  });
});
