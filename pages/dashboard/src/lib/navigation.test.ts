import { describe, expect, it, vi } from "vitest";

import type { Translate } from "@/lib/i18n";

import {
  DASHBOARD_NAVIGATION,
  localizeDashboardNavigation,
} from "./navigation";

describe("dashboard navigation catalog", () => {
  it("keeps the dashboard groups and pages in their stable order", () => {
    expect(DASHBOARD_NAVIGATION.map((group) => group.id)).toEqual([
      "overview",
      "memory",
      "insights",
      "relationships",
      "system",
    ]);
    expect(
      DASHBOARD_NAVIGATION.flatMap((group) =>
        group.items.map((item) => item.id),
      ),
    ).toEqual([
      "preview",
      "graph",
      "memory",
      "timeline",
      "recall",
      "injection",
      "knowledge",
      "notes",
      "intelligence",
      "learning",
      "jargon",
      "profiles",
      "affection",
      "social",
      "system",
      "config",
      "gate",
    ]);
  });

  it("places Gate at the end of the System group", () => {
    const system = DASHBOARD_NAVIGATION.find((group) => group.id === "system");

    expect(system?.items.map((item) => item.id)).toEqual([
      "system",
      "config",
      "gate",
    ]);
  });

  it("places Injection Strategy after Recall and before Knowledge", () => {
    const memory = DASHBOARD_NAVIGATION.find((group) => group.id === "memory");

    expect(memory?.items.map((item) => item.id)).toEqual([
      "graph",
      "memory",
      "timeline",
      "recall",
      "injection",
      "knowledge",
      "notes",
    ]);
  });

  it("localizes labels without changing stable IDs or icon references", () => {
    const t = vi.fn<Translate>((key) => `translated:${key}`);

    const localized = localizeDashboardNavigation(t);

    expect(localized.map((group) => group.label)).toEqual([
      "translated:nav.groupOverview",
      "translated:nav.groupMemory",
      "translated:nav.groupInsights",
      "translated:nav.groupRelationships",
      "translated:nav.groupSystem",
    ]);
    expect(
      localized.flatMap((group) => group.items.map((item) => item.label)),
    ).toEqual(
      DASHBOARD_NAVIGATION.flatMap((group) =>
        group.items.map((item) => `translated:${item.labelKey}`),
      ),
    );

    localized.forEach((group, groupIndex) => {
      const catalogGroup = DASHBOARD_NAVIGATION[groupIndex];
      expect(group.id).toBe(catalogGroup.id);
      group.items.forEach((item, itemIndex) => {
        const catalogItem = catalogGroup.items[itemIndex];
        expect(item.id).toBe(catalogItem.id);
        expect(item.icon).toBe(catalogItem.icon);
      });
    });
  });
});
