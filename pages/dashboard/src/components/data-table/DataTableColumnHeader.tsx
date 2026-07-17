import type { Column, Header, RowData } from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { useI18n } from "@/hooks/useI18n";

interface DataTableColumnHeaderProps<TData extends RowData, TValue> {
  header: Header<TData, TValue>;
  title: ReactNode;
}

function columnActionLabel(action: string, label: string, language: string): string {
  if (language.startsWith("en")) {
    const match = action.match(/^(.*) (ascending|descending)$/);
    if (match) return `${match[1]} ${label} ${match[2]}`;
    if (action === "Clear sort") return `Clear ${label} sort`;
  }
  return `${action} ${label}`;
}

export function pinnedCellStyle<TData extends RowData, TValue>(
  column: Column<TData, TValue>,
): CSSProperties {
  const pin = column.getIsPinned();
  if (!pin) return {};

  return {
    position: "sticky",
    left: pin === "left" ? `${column.getStart("left")}px` : undefined,
    right: pin === "right" ? `${column.getAfter("right")}px` : undefined,
    width: column.getSize(),
    zIndex: 2,
  };
}

export function DataTableColumnHeader<TData extends RowData, TValue>({
  header,
  title,
}: DataTableColumnHeaderProps<TData, TValue>) {
  const { column } = header;
  const { currentLang, t } = useI18n();
  const label = column.columnDef.meta?.label ?? column.id;
  const sorted = column.getIsSorted();
  const nextAction = sorted === "asc"
    ? t("table.sortDescending")
    : sorted === "desc"
      ? t("table.clearSort")
      : t("table.sortAscending");
  const SortIcon = sorted === "asc" ? ArrowUp : sorted === "desc" ? ArrowDown : ArrowUpDown;

  const cycleSort = () => {
    if (sorted === "asc") {
      column.toggleSorting(true);
    } else if (sorted === "desc") {
      column.clearSorting();
    } else {
      column.toggleSorting(false);
    }
  };

  return (
    <div className="relative flex min-w-0 items-center gap-1">
      {column.getCanSort() ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="min-w-0 justify-start"
          aria-label={columnActionLabel(nextAction, label, currentLang())}
          onClick={cycleSort}
        >
          <span className="truncate">{title}</span>
          <SortIcon data-icon="inline-end" aria-hidden="true" />
        </Button>
      ) : (
        <span className="truncate">{title}</span>
      )}
      {column.getCanResize() ? (
        <div
          role="separator"
          tabIndex={0}
          aria-label={`${t("table.resizeColumn")} ${label}`}
          aria-orientation="vertical"
          className="absolute -right-1 top-0 h-full w-2 cursor-col-resize touch-none rounded-sm outline-none after:absolute after:inset-y-1 after:left-1/2 after:w-px after:bg-border hover:after:bg-foreground focus-visible:ring-2 focus-visible:ring-ring"
          onDoubleClick={() => column.resetSize()}
          onMouseDown={header.getResizeHandler()}
          onTouchStart={header.getResizeHandler()}
        />
      ) : null}
    </div>
  );
}
