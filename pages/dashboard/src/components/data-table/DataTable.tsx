import {
  flexRender,
  functionalUpdate,
  getCoreRowModel,
  useReactTable,
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
  defaultTablePreferences,
  loadTablePreferences,
  resetTablePreferences,
  sanitizeTablePreferences,
  saveTablePreferences,
  type TablePreferences,
} from "./table-preferences";
import type { DataTableColumn, DataTableSort } from "./table-types";
import {
  DataTableColumnHeader,
  pinnedCellStyle,
} from "./DataTableColumnHeader";
import { DataTableViewOptions } from "./DataTableViewOptions";

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

function isInteractiveTarget(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    Boolean(
      target.closest(
        "a,button,input,select,textarea,[role='button'],[role='checkbox'],[role='menuitem'],[role='menuitemcheckbox'],[role='menuitemradio'],[data-row-interactive]",
      ),
    )
  );
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

  const resetView = () => {
    setPreferences(defaultTablePreferences(descriptors));
    setColumnSizing({});
    resetTablePreferences(tableId);
  };

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

  const viewOptions = (compact = false) => (
    <DataTableViewOptions
      table={table}
      density={preferences.density}
      onDensityChange={(density) =>
        updatePreferences((current) => ({ ...current, density }))
      }
      onReset={resetView}
      compact={compact}
    />
  );
  const headerGroups = table.getHeaderGroups();

  return (
    <div className="flex min-w-0 flex-col gap-3" data-table-id={tableId}>
      {toolbar ? (
        <div className="flex min-w-0 items-center gap-2">
          <div className="min-w-0 flex-1">{toolbar}</div>
          <div className="ml-auto shrink-0">{viewOptions()}</div>
        </div>
      ) : null}
      <div className="relative min-w-0">
        {!toolbar ? (
          <div data-slot="data-table-view-overlay" className="absolute right-2 top-1.5 z-30">
            {viewOptions(true)}
          </div>
        ) : null}
        <Table
          aria-busy={loading}
          data-density={preferences.density}
          className="[&[data-density=compact]_th]:h-8 [&[data-density=compact]_td]:py-1 [&[data-density=comfortable]_th]:h-12 [&[data-density=comfortable]_td]:py-3"
          containerClassName="min-w-0"
          style={{ minWidth: table.getTotalSize() }}
        >
          <TableHeader className="sticky top-0 z-20 bg-background">
            {headerGroups.map((headerGroup, groupIndex) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header, headerIndex) => {
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
                      className={cn(
                        pinned && "bg-background",
                        !toolbar &&
                          groupIndex === headerGroups.length - 1 &&
                          headerIndex === headerGroup.headers.length - 1 &&
                          "pr-12",
                      )}
                      style={{
                        width: header.column.getSize(),
                        ...pinnedCellStyle(header.column),
                      }}
                    >
                      <DataTableColumnHeader
                        header={header}
                        title={content}
                      />
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
                          style={{
                            width: cell.column.getSize(),
                            ...pinnedCellStyle(cell.column),
                          }}
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
      </div>
      {pagination}
    </div>
  );
}
