import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "index.css"), "utf8");
const browserSmoke = readFileSync(
  resolve(__dirname, "../scripts/browser_smoke.mjs"),
  "utf8",
);

describe("theme motion CSS contract", () => {
  it("interpolates registered theme tokens once on the root element", () => {
    const registeredProperties = Array.from(
      css.matchAll(/@property\s+(--[\w-]+)/g),
      (match) => match[1],
    );
    const rule = css.match(
      /html\.theme-transitioning\s*\{(?<body>[^}]*)\}/,
    )?.groups?.body ?? "";

    expect(registeredProperties).toEqual(expect.arrayContaining([
      "--background",
      "--foreground",
      "--card",
      "--border",
      "--primary",
      "--selection-surface",
      "--sidebar",
      "--sidebar-primary",
      "--shadow-surface-color",
    ]));
    expect(rule).toContain("--background");
    expect(rule).toContain("--sidebar");
    expect(rule).toContain("--foreground");
    expect(rule).toContain("transition-duration: 200ms");
    expect(rule).toContain("transition-timing-function: ease-out");

    const propertyList = rule.match(/transition-property:\s*([^;]+)/)?.[1] ?? "";
    expect(propertyList).not.toMatch(
      /(^|,|\s)(color|background-color|border-color|outline-color|box-shadow|fill|stroke|opacity|transform|width|height|top|left)(,|\s|$)/,
    );

    const descendants = css.match(
      /html\.theme-transitioning body,\s*html\.theme-transitioning body \*:not\(canvas\)\s*\{(?<body>[^}]*)\}/,
    )?.groups?.body ?? "";
    expect(descendants).toContain("transition-property: none !important");
  });

  it("provides a reduced-motion fallback for any active transition", () => {
    const reducedMotion = css.match(
      /@media \(prefers-reduced-motion: reduce\)\s*\{(?<body>[\s\S]*?)\n\}/,
    )?.groups?.body ?? "";

    expect(reducedMotion).toContain("html.theme-transitioning");
    expect(reducedMotion).toContain("transition-duration: 0.01ms !important");
  });

  it("waits for a stable final theme before taking dark-mode screenshots", () => {
    const transitionWatch = browserSmoke.indexOf(
      "__memoraThemeTransitionSeen",
    );
    const click = browserSmoke.indexOf(
      'getByRole("button", { name: "切换主题" }).click()',
    );
    const learningRoute = browserSmoke.lastIndexOf('"#/learning"', click);
    const transitionStarted = browserSmoke.indexOf(
      "window.__memoraThemeTransitionSeen === true",
      click,
    );
    const transitionSettled = browserSmoke.indexOf(
      '!document.documentElement.classList.contains("theme-transitioning")',
      transitionStarted,
    );
    const surfacesSettled = browserSmoke.indexOf(
      "themeSurfaces",
      transitionSettled,
    );
    const selectedNavigation = browserSmoke.indexOf(
      '[aria-current="page"]',
      surfacesSettled,
    );
    const learningCard = browserSmoke.indexOf(
      '[data-slot="card"]',
      surfacesSettled,
    );
    const darkLearningScreenshot = browserSmoke.indexOf(
      '"dark-learning.png"',
      surfacesSettled,
    );
    const darkScreenshot = browserSmoke.indexOf('"dark-system.png"', click);

    expect(learningRoute).toBeGreaterThanOrEqual(0);
    expect(transitionWatch).toBeGreaterThanOrEqual(0);
    expect(transitionWatch).toBeGreaterThan(learningRoute);
    expect(click).toBeGreaterThan(transitionWatch);
    expect(click).toBeGreaterThan(learningRoute);
    expect(transitionStarted).toBeGreaterThan(click);
    expect(transitionSettled).toBeGreaterThan(transitionStarted);
    expect(surfacesSettled).toBeGreaterThan(transitionSettled);
    expect(selectedNavigation).toBeGreaterThan(surfacesSettled);
    expect(learningCard).toBeGreaterThan(surfacesSettled);
    expect(darkLearningScreenshot).toBeGreaterThan(learningCard);
    expect(darkScreenshot).toBeGreaterThan(surfacesSettled);
    expect(darkScreenshot).toBeGreaterThan(darkLearningScreenshot);
  });
});
