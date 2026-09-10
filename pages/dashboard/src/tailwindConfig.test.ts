import { describe, expect, it } from "vitest";

// The Tailwind configuration is intentionally JavaScript and has no declaration file.
// @ts-expect-error -- Vitest loads the ESM config directly at runtime.
import tailwindConfig from "../tailwind.config.js";

describe("Tailwind semantic colors", () => {
  it("registers the shadcn color tokens used by dashboard components", () => {
    const colors = tailwindConfig.theme?.extend?.colors as Record<string, unknown>;

    for (const token of [
      "background",
      "foreground",
      "card",
      "popover",
      "primary",
      "secondary",
      "muted",
      "accent",
      "destructive",
      "input",
      "ring",
      "sidebar",
    ]) {
      expect(colors[token], `missing semantic color ${token}`).toBeTruthy();
    }
  });
});
