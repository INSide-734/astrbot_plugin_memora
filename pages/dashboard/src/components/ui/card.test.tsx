import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Card, CardContent, CardHeader, CardTitle } from "./card";

describe("Card", () => {
  it("uses Tailwind 3 compatible spacing classes", () => {
    render(
      <Card>
        <CardHeader><CardTitle>Title</CardTitle></CardHeader>
        <CardContent>Content</CardContent>
      </Card>,
    );

    const card = screen.getByText("Title").closest('[data-slot="card"]');
    expect(card?.className).toContain("gap-4");
    expect(card?.className).toContain("py-4");
    expect(card?.className).not.toContain("gap-(--card-spacing)");
    expect(screen.getByText("Title").closest('[data-slot="card-header"]')?.className).toContain("px-4");
    expect(screen.getByText("Content").className).toContain("px-4");
  });
});
