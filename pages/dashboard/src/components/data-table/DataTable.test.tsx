import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Checkbox } from "@/components/ui/checkbox";
import { DataTablePagination } from "./DataTablePagination";
import { actionsColumn, selectionColumn } from "./data-table-columns";
import type { DataTableColumn, DataTableSort } from "./table-types";
import { DataTable } from "./DataTable";

vi.mock("@/hooks/useI18n", () => {
  const copy: Record<string, string> = {
    "pagination.label": "Pagination",
    "pagination.nextPage": "Next page",
    "pagination.pageOf": "Page {0} of {1}",
    "pagination.previousPage": "Previous page",
    "table.clearSort": "Clear sort",
    "table.columns": "Columns",
    "table.density": "Density",
    "table.densityComfortable": "Comfortable",
    "table.densityCompact": "Compact",
    "table.densityStandard": "Standard",
    "table.moveLeft": "Move left",
    "table.moveRight": "Move right",
    "table.pinLeft": "Pin left",
    "table.pinRight": "Pin right",
    "table.resetView": "Reset view",
    "table.resizeColumn": "Resize column",
    "table.sortAscending": "Sort ascending",
    "table.sortDescending": "Sort descending",
    "table.unpin": "Unpin",
    "table.viewOptions": "Table view",
  };
  return {
    useI18n: () => ({
      currentLang: () => "en",
      t: (key: string, ...args: string[]) => {
        let value = copy[key] ?? key;
        args.forEach((arg, index) => {
          value = value.replace(`{${index}}`, arg);
        });
        return value;
      },
    }),
  };
});

interface RowFixture {
  id: string;
  title: string;
  category?: string;
  updated?: string;
}

const columns: DataTableColumn<RowFixture>[] = [
  {
    id: "select",
    meta: { label: "Select", required: true, defaultPin: "left" },
    header: () => null,
    cell: ({ row }) => (
      <Checkbox
        aria-label={`Select ${row.original.title}`}
        checked={row.getIsSelected()}
        onCheckedChange={row.getToggleSelectedHandler()}
      />
    ),
  },
  {
    id: "title",
    accessorKey: "title",
    header: "Title",
    meta: { label: "Title", serverSortKey: "title", required: true },
  },
];

describe("DataTable", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => cleanup());

  it("keeps server order while exposing controlled sort, selection, activation, and density", () => {
    const onRowActivate = vi.fn();
    const onSelectionChange = vi.fn();
    const onSortChange = vi.fn();

    function ControlledTable() {
      const [sort, setSort] = useState<DataTableSort | null>(null);
      return (
        <DataTable
          tableId="test"
          data={[
            { id: "1", title: "Beta" },
            { id: "2", title: "Alpha" },
            { id: "3", title: "Gamma" },
          ]}
          columns={columns}
          getRowId={(row) => row.id}
          sort={sort}
          onSortChange={(next) => {
            onSortChange(next);
            setSort(next);
          }}
          selectedRowIds={new Set()}
          onSelectedRowIdsChange={onSelectionChange}
          currentRowId="2"
          onRowActivate={onRowActivate}
          loading={false}
          emptyLabel="No rows"
        />
      );
    }

    render(<ControlledTable />);

    fireEvent.click(screen.getByRole("button", { name: "Sort Title ascending" }));
    expect(onSortChange).toHaveBeenCalledWith({ id: "title", desc: false });
    fireEvent.click(screen.getByRole("button", { name: "Sort Title descending" }));
    expect(onSortChange).toHaveBeenLastCalledWith({ id: "title", desc: true });
    fireEvent.click(screen.getByRole("button", { name: "Clear Title sort" }));
    expect(onSortChange).toHaveBeenLastCalledWith(null);

    const rows = screen.getAllByRole("row");
    expect(rows[1].textContent).toContain("Beta");
    expect(rows[2].getAttribute("aria-current")).toBe("true");
    expect(
      screen.getByRole("separator", { name: "Resize column Title" }).getAttribute("tabindex"),
    ).toBe("0");

    fireEvent.click(screen.getByRole("checkbox", { name: "Select Beta" }));
    expect(onSelectionChange).toHaveBeenCalledWith(new Set(["1"]));
    expect(onRowActivate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Alpha"));
    expect(onRowActivate).toHaveBeenCalledWith(
      expect.objectContaining({ id: "2", title: "Alpha" }),
    );
    expect(screen.getByRole("table").getAttribute("data-density")).toBe("standard");
  });

  it("manages columns, density, row actions, and true pagination", () => {
    const onDelete = vi.fn();
    const onPageChange = vi.fn();
    const onRowActivate = vi.fn();

    const controlledColumns: DataTableColumn<RowFixture>[] = [
      selectionColumn({
        label: "Select all rows",
        rowLabel: (row) => `Select ${row.title}`,
      }),
      {
        id: "title",
        accessorKey: "title",
        header: "Title",
        meta: { label: "Title", serverSortKey: "title", required: true },
      },
      {
        id: "category",
        accessorKey: "category",
        header: "Category",
        meta: { label: "Category" },
      },
      {
        id: "updated",
        accessorKey: "updated",
        header: "Updated",
        meta: { label: "Updated" },
      },
      actionsColumn({
        label: "Actions",
        rowLabel: (row) => `Actions for ${row.title}`,
        actions: () => [
          {
            id: "delete",
            label: "Delete",
            destructive: true,
            onSelect: (row) => onDelete(row.id),
          },
        ],
      }),
    ];

    render(
      <DataTable
        tableId="controls"
        data={[
          { id: "1", title: "Beta", category: "One", updated: "Later" },
          { id: "2", title: "Alpha", category: "Two", updated: "Sooner" },
        ]}
        columns={controlledColumns}
        getRowId={(row) => row.id}
        sort={null}
        onSortChange={vi.fn()}
        selectedRowIds={new Set()}
        onSelectedRowIdsChange={vi.fn()}
        onRowActivate={onRowActivate}
        loading={false}
        emptyLabel="No rows"
        pagination={
          <DataTablePagination
            page={0}
            pageCount={2}
            total={4}
            onPageChange={onPageChange}
          />
        }
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Table view" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Unpin Select all rows" }),
    );
    expect(
      JSON.parse(localStorage.getItem("memora.table.controls.v1") ?? "null")
        .columnPinning.left,
    ).not.toContain("select");
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "Category" }));
    expect(screen.queryByRole("columnheader", { name: /Category/ })).toBeNull();
    expect(
      JSON.parse(localStorage.getItem("memora.table.controls.v1") ?? "null")
        .columnVisibility.category,
    ).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Move Updated left" }));
    const movedPreferences = JSON.parse(
      localStorage.getItem("memora.table.controls.v1") ?? "null",
    );
    expect(movedPreferences.columnOrder.indexOf("updated")).toBeLessThan(
      movedPreferences.columnOrder.indexOf("category"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Pin Updated right" }));
    expect(screen.getByRole("button", { name: "Unpin Updated" })).toBeTruthy();
    expect(
      JSON.parse(localStorage.getItem("memora.table.controls.v1") ?? "null")
        .columnPinning.right,
    ).toContain("updated");
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Compact" }));
    expect(screen.getByRole("table").getAttribute("data-density")).toBe("compact");
    expect(
      JSON.parse(localStorage.getItem("memora.table.controls.v1") ?? "null").density,
    ).toBe("compact");

    fireEvent.click(screen.getByRole("button", { name: "Actions for Alpha" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(onDelete).toHaveBeenCalledWith("2");
    expect(onRowActivate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });
});
