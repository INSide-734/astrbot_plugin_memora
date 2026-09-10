import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  selectionStateVariants,
  type SelectionKind,
} from "./selection-state";

const kinds: SelectionKind[] = [
  "navigation",
  "control",
  "row",
  "surface",
  "current-item",
];

describe("selectionStateVariants", () => {
  it.each(kinds)("returns a distinct selected recipe for %s", (kind) => {
    const unselected = selectionStateVariants({ kind, selected: false });
    const selected = selectionStateVariants({ kind, selected: true });

    expect(selected).not.toBe(unselected);
    expect(selected).toContain("duration-150");
    expect(selected).toContain("motion-reduce:transition-none");
    expect(selected).toContain("transition-[color,background-color,border-color,box-shadow]");
  });

  it("uses the existing primary treatment for navigation", () => {
    const classes = selectionStateVariants({
      kind: "navigation",
      selected: true,
    });

    expect(classes).toContain("bg-primary");
    expect(classes).toContain("text-primary-foreground");
  });

  it("uses a shallow surface and internal indicator for controls", () => {
    const classes = selectionStateVariants({ kind: "control", selected: true });

    expect(classes).toContain("bg-[var(--selection-surface)]");
    expect(classes).toContain("text-[var(--selection-foreground)]");
    expect(classes).toContain(
      "shadow-[inset_0_-2px_0_var(--selection-indicator)]",
    );
  });

  it.each(["row", "current-item"] satisfies SelectionKind[])(
    "uses an inset left indicator for %s without changing layout",
    (kind) => {
      const classes = selectionStateVariants({ kind, selected: true });

      expect(classes).toContain("bg-[var(--selection-surface)]");
      expect(classes).toContain(
        "shadow-[inset_2px_0_0_var(--selection-indicator)]",
      );
      expect(classes).not.toContain("border-l-2");
    },
  );

  it("uses a non-layout inset selection border for selected surfaces", () => {
    const classes = selectionStateVariants({ kind: "surface", selected: true });
    const tokens = classes.split(/\s+/);

    expect(classes).toContain("bg-[var(--selection-surface)]");
    expect(classes).toContain(
      "shadow-[inset_0_0_0_1px_var(--selection-border)]",
    );
    expect(tokens).not.toContain("border");
    expect(tokens.some((token) => token.includes(":border-"))).toBe(false);
  });
});

describe("selection theme tokens", () => {
  const css = readFileSync(
    resolve(process.cwd(), "src/index.css"),
    "utf8",
  );

  it("defines light theme selection tokens from the current primary theme", () => {
    expect(css).toContain(
      "--selection-surface: color-mix(in oklch, var(--primary) 6%, transparent);",
    );
    expect(css).toContain(
      "--selection-border: color-mix(in oklch, var(--primary) 28%, transparent);",
    );
    expect(css).toContain("--selection-indicator: var(--primary);");
    expect(css).toContain("--selection-foreground: var(--foreground);");
  });

  it("provides the restrained dark theme selection overrides", () => {
    expect(css).toContain(
      "--selection-surface: color-mix(in oklch, var(--primary) 10%, transparent);",
    );
    expect(css).toContain(
      "--selection-border: color-mix(in oklch, var(--primary) 24%, transparent);",
    );
  });
});
