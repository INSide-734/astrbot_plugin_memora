import type { ColumnPinningState, VisibilityState } from "@tanstack/react-table";

import type { DataTableColumnDescriptor, DataTableDensity } from "./table-types";

export const TABLE_PREFERENCE_SCHEMA = 1;

export interface TablePreferences {
  schemaVersion: 1;
  density: DataTableDensity;
  columnVisibility: VisibilityState;
  columnOrder: string[];
  columnPinning: ColumnPinningState;
}

const keyFor = (tableId: string) => `memora.table.${tableId}.v1`;

const validDensity = (value: unknown): value is DataTableDensity =>
  value === "compact" || value === "standard" || value === "comfortable";

export function defaultTablePreferences(
  columns: readonly DataTableColumnDescriptor[],
): TablePreferences {
  return {
    schemaVersion: TABLE_PREFERENCE_SCHEMA,
    density: "standard",
    columnVisibility: Object.fromEntries(columns.map(({ id }) => [id, true])),
    columnOrder: columns.map(({ id }) => id),
    columnPinning: {
      left: columns.filter((column) => column.defaultPin === "left").map(({ id }) => id),
      right: columns.filter((column) => column.defaultPin === "right").map(({ id }) => id),
    },
  };
}

export function sanitizeTablePreferences(
  raw: unknown,
  columns: readonly DataTableColumnDescriptor[],
): TablePreferences {
  const defaults = defaultTablePreferences(columns);
  if (
    !raw ||
    typeof raw !== "object" ||
    (raw as { schemaVersion?: unknown }).schemaVersion !== TABLE_PREFERENCE_SCHEMA
  ) {
    return defaults;
  }

  const value = raw as Partial<TablePreferences>;
  const ids = new Set(columns.map(({ id }) => id));
  const required = new Set(columns.filter((column) => column.required).map(({ id }) => id));
  const uniqueKnownIds = (items: unknown): string[] =>
    Array.from(
      new Set(
        Array.isArray(items)
          ? items.filter(
              (id): id is string => typeof id === "string" && ids.has(id),
            )
          : [],
      ),
    );

  const columnOrder = uniqueKnownIds(value.columnOrder);
  defaults.columnOrder.forEach((id, index) => {
    if (!columnOrder.includes(id)) {
      columnOrder.splice(Math.min(index, columnOrder.length), 0, id);
    }
  });

  const rawVisibility =
    value.columnVisibility && typeof value.columnVisibility === "object"
      ? (value.columnVisibility as Record<string, unknown>)
      : {};
  const columnVisibility = Object.fromEntries(
    defaults.columnOrder.map((id) => [
      id,
      required.has(id) ? true : rawVisibility[id] !== false,
    ]),
  );

  const defaultLeft = defaults.columnPinning.left ?? [];
  const defaultRight = defaults.columnPinning.right ?? [];
  const left = uniqueKnownIds([
    ...defaultLeft,
    ...uniqueKnownIds(value.columnPinning?.left),
  ]).filter((id) => !defaultRight.includes(id));
  const right = uniqueKnownIds([
    ...uniqueKnownIds(value.columnPinning?.right),
    ...defaultRight,
  ]).filter((id) => !left.includes(id));

  return {
    schemaVersion: TABLE_PREFERENCE_SCHEMA,
    density: validDensity(value.density) ? value.density : "standard",
    columnVisibility,
    columnOrder,
    columnPinning: { left, right },
  };
}

export function loadTablePreferences(
  tableId: string,
  columns: readonly DataTableColumnDescriptor[],
): TablePreferences {
  try {
    return sanitizeTablePreferences(
      JSON.parse(localStorage.getItem(keyFor(tableId)) ?? "null"),
      columns,
    );
  } catch {
    return defaultTablePreferences(columns);
  }
}

export function saveTablePreferences(
  tableId: string,
  value: TablePreferences,
  columns: readonly DataTableColumnDescriptor[],
): void {
  try {
    localStorage.setItem(
      keyFor(tableId),
      JSON.stringify(sanitizeTablePreferences(value, columns)),
    );
  } catch {
    // Current-session table state remains usable without persistence.
  }
}

export function resetTablePreferences(tableId: string): void {
  try {
    localStorage.removeItem(keyFor(tableId));
  } catch {
    // Storage can be unavailable without affecting the current table state.
  }
}
