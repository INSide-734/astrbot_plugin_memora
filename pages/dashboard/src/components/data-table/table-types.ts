import type { ColumnDef, RowData } from "@tanstack/react-table";

export type DataTableDensity = "compact" | "standard" | "comfortable";
export type DataTablePin = "left" | "right" | false;

export interface DataTableSort {
  id: string;
  desc: boolean;
}

export interface DataTableColumnDescriptor {
  id: string;
  required?: boolean;
  defaultPin?: DataTablePin;
}

export interface DataTableColumnMeta {
  label: string;
  serverSortKey?: string;
  required?: boolean;
  defaultPin?: DataTablePin;
  cellClassName?: string;
}

export type DataTableColumn<TData extends RowData, TValue = unknown> = ColumnDef<
  TData,
  TValue
> & {
  meta: DataTableColumnMeta;
};

declare module "@tanstack/react-table" {
  interface ColumnMeta<TData extends RowData, TValue> extends DataTableColumnMeta {}
}
