import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Checkbox } from "@/components/ui/checkbox";
import type { DataTableColumn } from "./table-types";
import { DataTable } from "./DataTable";

interface RowFixture {
  id: string;
  title: string;
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
  it("keeps server order while exposing controlled sort, selection, activation, and density", () => {
    const onRowActivate = vi.fn();
    const onSelectionChange = vi.fn();
    const onSortChange = vi.fn();

    render(
      <DataTable
        tableId="test"
        data={[
          { id: "1", title: "Beta" },
          { id: "2", title: "Alpha" },
          { id: "3", title: "Gamma" },
        ]}
        columns={columns}
        getRowId={(row) => row.id}
        sort={null}
        onSortChange={onSortChange}
        selectedRowIds={new Set()}
        onSelectedRowIdsChange={onSelectionChange}
        currentRowId="2"
        onRowActivate={onRowActivate}
        loading={false}
        emptyLabel="No rows"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Sort Title ascending" }));
    expect(onSortChange).toHaveBeenCalledWith({ id: "title", desc: false });

    const rows = screen.getAllByRole("row");
    expect(rows[1].textContent).toContain("Beta");
    expect(rows[2].getAttribute("aria-current")).toBe("true");

    fireEvent.click(screen.getByRole("checkbox", { name: "Select Beta" }));
    expect(onSelectionChange).toHaveBeenCalledWith(new Set(["1"]));
    expect(onRowActivate).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Alpha"));
    expect(onRowActivate).toHaveBeenCalledWith(
      expect.objectContaining({ id: "2", title: "Alpha" }),
    );
    expect(screen.getByRole("table").getAttribute("data-density")).toBe("standard");
  });
});
