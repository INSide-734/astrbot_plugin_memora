import { beforeEach, describe, expect, it } from "vitest";

import {
  loadTablePreferences,
  resetTablePreferences,
  saveTablePreferences,
  sanitizeTablePreferences,
} from "./table-preferences";

const columns = [
  { id: "select", required: true, defaultPin: "left" as const },
  { id: "title", required: true, defaultPin: "left" as const },
  { id: "category" },
  { id: "updated_at" },
  { id: "actions", required: true, defaultPin: "right" as const },
];

beforeEach(() => localStorage.clear());

describe("table preferences", () => {
  it("drops unknown and duplicate columns and restores required columns", () => {
    expect(
      sanitizeTablePreferences(
        {
          schemaVersion: 1,
          density: "compact",
          columnVisibility: { title: false, ghost: false },
          columnOrder: ["category", "category", "ghost"],
          columnPinning: { left: ["ghost"], right: ["actions", "actions"] },
        },
        columns,
      ),
    ).toEqual({
      schemaVersion: 1,
      density: "compact",
      columnVisibility: {
        select: true,
        title: true,
        category: true,
        updated_at: true,
        actions: true,
      },
      columnOrder: ["select", "title", "category", "updated_at", "actions"],
      columnPinning: { left: ["select", "title"], right: ["actions"] },
    });
  });

  it("persists only display preferences and rejects an old schema", () => {
    saveTablePreferences(
      "knowledge",
      {
        schemaVersion: 1,
        density: "comfortable",
        columnVisibility: { category: false },
        columnOrder: ["select", "title", "updated_at", "category", "actions"],
        columnPinning: { left: ["select", "title"], right: ["actions"] },
      },
      columns,
    );

    expect(loadTablePreferences("knowledge", columns).density).toBe("comfortable");

    localStorage.setItem(
      "memora.table.knowledge.v1",
      JSON.stringify({ schemaVersion: 0 }),
    );
    expect(loadTablePreferences("knowledge", columns).density).toBe("standard");

    resetTablePreferences("knowledge");
    expect(localStorage.getItem("memora.table.knowledge.v1")).toBeNull();
  });
});
