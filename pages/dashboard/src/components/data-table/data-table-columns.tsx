import type { RowData } from "@tanstack/react-table";

import { Checkbox } from "@/components/ui/checkbox";
import {
  DataTableRowActions,
  type DataTableAction,
} from "./DataTableRowActions";
import type { DataTableColumn } from "./table-types";

/** 创建固定在表格左侧的行选择列。 */
export function selectionColumn<TData extends RowData>({
  label,
  rowLabel,
}: {
  label: string;
  rowLabel(row: TData): string;
}): DataTableColumn<TData> {
  return {
    id: "select",
    meta: { label, required: true, defaultPin: "left" },
    enableSorting: false,
    enableHiding: false,
    header: ({ table }) => (
      <Checkbox
        aria-label={label}
        checked={table.getIsAllPageRowsSelected()}
        onCheckedChange={(checked) =>
          table.toggleAllPageRowsSelected(Boolean(checked))
        }
      />
    ),
    cell: ({ row }) => (
      <Checkbox
        aria-label={rowLabel(row.original)}
        checked={row.getIsSelected()}
        onCheckedChange={(checked) => row.toggleSelected(Boolean(checked))}
        onClick={(event) => event.stopPropagation()}
      />
    ),
  };
}

/** 创建固定在表格右侧且不会遮挡相邻内容的行操作列。 */
export function actionsColumn<TData extends RowData>({
  label,
  rowLabel,
  actions,
}: {
  label: string;
  rowLabel(row: TData): string;
  actions(row: TData): DataTableAction<TData>[];
}): DataTableColumn<TData> {
  return {
    id: "actions",
    size: 96,
    minSize: 96,
    maxSize: 96,
    meta: { label, required: true, defaultPin: "right" },
    enableSorting: false,
    enableHiding: false,
    enableResizing: false,
    header: () => <span className="sr-only">{label}</span>,
    cell: ({ row }) => (
      <DataTableRowActions
        row={row.original}
        rowLabel={rowLabel(row.original)}
        actions={actions(row.original)}
      />
    ),
  };
}
