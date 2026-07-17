import {
  flexRender,
  functionalUpdate,
  getCoreRowModel,
  useReactTable,
  type Column,
  type ColumnSizingState,
  type RowData,
  type RowSelectionState,
  type SortingState,
  type Updater,
} from "@tanstack/react-table";
import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from "react";

import { Skeleton } from "@/components/ui/Skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { selectionStateVariants } from "@/components/ui/selection-state";
import { cn } from "@/lib/utils";
import {
  loadTablePreferences,
  sanitizeTablePreferences,
  saveTablePreferences,
  type TablePreferences,
} from "./table-preferences";
import type { DataTableColumn, DataTableSort } from "./table-types";

export interface DataTableProps<TData extends RowData> {
  tableId: string;
  data: TData[];
  columns: DataTableColumn<TData>[];
  getRowId(row: TData): string;
  sort: DataTableSort | null;
  onSortChange(next: DataTableSort | null): void;
  selectedRowIds?: Set<string>;
  onSelectedRowIdsChange?(next: Set<string>): void;
  currentRowId?: string | null;
  onRowActivate?(row: TData): void;
  loading: boolean;
  emptyLabel: string;
  toolbar?: ReactNode;
  pagination?: ReactNode;
}

function getPinningStyles<TData extends RowData, TValue>(
  column: Column<TData, TValue>,
): CSSProperties {
  const pinned = column.getIsPinned();

  return {
    left: pinned === "left" ? `${column.getStart("left")}px` : undefined,
    right: pinned === "right" ? `${column.getAfter("right")}px` : undefined,
    position: pinned ? "sticky" : "relative",
    width: column.getSize(),
    zIndex: pinned ? 1 : 0,
  };
}

function isInteractiveTarget(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    Boolean(
      target.closest(
        "a,button,input,select,textarea,[role='button'],[role='checkbox'],[data-row-interactive]",
      ),
    )
  );
}

function nextSortAction<TData extends RowData, TValue>(
  column: Column<TData, TValue>,
): "ascending" | "descending" | "clear" {
  const sorted = column.getIsSorted();
  if (sorted === "asc") return "descending";
  if (sorted === "desc") return "clear";
  return "ascending";
}

export function DataTable<TData extends RowData>({
  tableId,
  data,
  columns,
  getRowId,
  sort,
  onSortChange,
  selectedRowIds,
  onSelectedRowIdsChange,
  currentRowId,
  onRowActivate,
  loading,
  emptyLabel,
  toolbar,
  pagination,
}: DataTableProps<TData>) {
  const descriptors = useMemo(
    () =>
      columns.map((column) => ({
        id: String(column.id),
        required: column.meta.required,
        defaultPin: column.meta.defaultPin,
      })),
    [columns],
  );
  const [preferences, setPreferences] = useState(() =>
    loadTablePreferences(tableId, descriptors),
  );
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>({});

  const updatePreferences = useCallback(
    (updater: Updater<TablePreferences>) => {
      setPreferences((current) => {
        const next = sanitizeTablePreferences(
          functionalUpdate(updater, current),
          descriptors,
        );
        saveTablePreferences(tableId, next, descriptors);
        return next;
      });
    },
    [descriptors, tableId],
  );

  const sorting = useMemo<SortingState>(
    () => (sort ? [{ id: sort.id, desc: sort.desc }] : []),
    [sort],
  );
  const rowSelection = useMemo<RowSelectionState>(
    () =>
      Object.fromEntries(
        Array.from(selectedRowIds ?? []).map((id) => [id, true]),
      ),
    [selectedRowIds],
  );

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId,
    manualSorting: true,
    enableMultiSort: false,
    enableSortingRemoval: true,
    state: {
      sorting,
      rowSelection,
      columnVisibility: preferences.columnVisibility,
      columnOrder: preferences.columnOrder,
      columnPinning: preferences.columnPinning,
      columnSizing,
    },
    onSortingChange: (updater) => {
      const next = functionalUpdate(updater, sorting)[0];
      onSortChange(next ? { id: next.id, desc: next.desc } : null);
    },
    onRowSelectionChange: (updater) => {
      const next = functionalUpdate(updater, rowSelection);
      onSelectedRowIdsChange?.(
        new Set(Object.keys(next).filter((id) => next[id])),
      );
    },
    onColumnVisibilityChange: (updater) => {
      updatePreferences((current) => ({
        ...current,
        columnVisibility: functionalUpdate(updater, current.columnVisibility),
      }));
    },
    onColumnOrderChange: (updater) => {
      updatePreferences((current) => ({
        ...current,
        columnOrder: functionalUpdate(updater, current.columnOrder),
      }));
    },
    onColumnPinningChange: (updater) => {
      updatePreferences((current) => ({
        ...current,
        columnPinning: functionalUpdate(updater, current.columnPinning),
      }));
    },
    onColumnSizingChange: setColumnSizing,
    columnResizeMode: "onChange",
  });

  const visibleColumnCount = Math.max(table.getVisibleLeafColumns().length, 1);

  const activateRow = (
    event: MouseEvent<HTMLTableRowElement> | KeyboardEvent<HTMLTableRowElement>,
    row: TData,
  ) => {
    if (!onRowActivate || isInteractiveTarget(event.target)) return;
    if ("key" in event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
    }
    onRowActivate(row);
  };

  return (
    <div className="flex min-w-0 flex-col gap-3" data-table-id={tableId}>
      {toolbar}
      <Table
        aria-busy={loading}
        data-density={preferences.density}
        containerClassName="min-w-0"
      >
        <TableHeader className="sticky top-0 z-20 bg-background">
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id}>
              {headerGroup.headers.map((header) => {
                const pinned = header.column.getIsPinned();
                const content = header.isPlaceholder
                  ? null
                  : flexRender(
                      header.column.columnDef.header,
                      header.getContext(),
                    );

                return (
                  <TableHead
                    key={header.id}
                    colSpan={header.colSpan}
                    className={cn(pinned && "bg-background")}
                    style={getPinningStyles(header.column)}
                  >
                    {header.column.getCanSort() ? (
                      <button
                        type="button"
                        className="inline-flex min-h-8 items-center rounded-sm text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        aria-label={`Sort ${header.column.columnDef.meta?.label ?? header.id} ${nextSortAction(header.column)}`}
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        {content}
                      </button>
                    ) : (
                      content
                    )}
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {loading ? (
            <TableRow>
              <TableCell colSpan={visibleColumnCount} className="h-24">
                <div role="status" className="flex flex-col gap-2" aria-busy="true">
                  <Skeleton className="h-4 w-full" />
                  <Skeleton className="h-4 w-2/3" />
                </div>
              </TableCell>
            </TableRow>
          ) : table.getRowModel().rows.length ? (
            table.getRowModel().rows.map((row) => {
              const current = row.id === currentRowId;
              return (
                <TableRow
                  key={row.id}
                  aria-current={current ? "true" : undefined}
                  data-state={row.getIsSelected() ? "selected" : undefined}
                  tabIndex={onRowActivate ? 0 : undefined}
                  className={cn(
                    onRowActivate &&
                      "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                    selectionStateVariants({
                      kind: "current-item",
                      selected: current,
                    }),
                  )}
                  onClick={(event) => activateRow(event, row.original)}
                  onKeyDown={(event) => activateRow(event, row.original)}
                >
                  {row.getVisibleCells().map((cell) => {
                    const pinned = cell.column.getIsPinned();
                    return (
                      <TableCell
                        key={cell.id}
                        className={cn(
                          pinned && "bg-background",
                          cell.column.columnDef.meta?.cellClassName,
                        )}
                        style={getPinningStyles(cell.column)}
                      >
                        {flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext(),
                        )}
                      </TableCell>
                    );
                  })}
                </TableRow>
              );
            })
          ) : (
            <TableRow>
              <TableCell
                colSpan={visibleColumnCount}
                className="h-24 text-center text-muted-foreground"
              >
                {emptyLabel}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      {pagination}
    </div>
  );
}
