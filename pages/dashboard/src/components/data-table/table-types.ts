import {
  columnOrderingFeature,
  columnPinningFeature,
  columnResizingFeature,
  columnSizingFeature,
  columnVisibilityFeature,
  rowSelectionFeature,
  rowSortingFeature,
  tableFeatures,
  type ColumnDef,
  type RowData,
} from "@tanstack/react-table";

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

export const dataTableFeatures = tableFeatures({
  columnOrderingFeature,
  columnPinningFeature,
  columnResizingFeature,
  columnSizingFeature,
  columnVisibilityFeature,
  rowSelectionFeature,
  rowSortingFeature,
  columnMeta: {} as DataTableColumnMeta,
});

export type DataTableFeatures = typeof dataTableFeatures;

export type DataTableColumn<TData extends RowData, TValue = unknown> = ColumnDef<
  DataTableFeatures,
  TData,
  TValue
> & {
  meta: DataTableColumnMeta;
};
