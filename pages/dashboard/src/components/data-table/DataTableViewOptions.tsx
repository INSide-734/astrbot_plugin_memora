import type { RowData, Table } from "@tanstack/react-table";
import {
  ArrowLeft,
  ArrowRight,
  Pin,
  PinOff,
  RotateCcw,
  SlidersHorizontal,
} from "lucide-react";
import { useState, type DragEvent, type MouseEvent } from "react";

import { Button, buttonVariants } from "@/components/ui/Button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useI18n } from "@/hooks/useI18n";
import type { DataTableDensity } from "./table-types";

interface DataTableViewOptionsProps<TData extends RowData> {
  table: Table<TData>;
  density: DataTableDensity;
  onDensityChange(density: DataTableDensity): void;
  onReset(): void;
}

export function moveColumn(
  order: readonly string[],
  draggedId: string,
  targetId: string,
): string[] {
  const fromIndex = order.indexOf(draggedId);
  const targetIndex = order.indexOf(targetId);
  if (fromIndex < 0 || targetIndex < 0 || fromIndex === targetIndex) {
    return [...order];
  }

  const next = [...order];
  const [dragged] = next.splice(fromIndex, 1);
  next.splice(targetIndex, 0, dragged);
  return next;
}

function columnActionLabel(action: string, label: string, language: string): string {
  if (language.startsWith("en")) {
    const match = action.match(/^(Move|Pin) (left|right)$/);
    if (match) return `${match[1]} ${label} ${match[2]}`;
  }
  return `${action} ${label}`;
}

export function DataTableViewOptions<TData extends RowData>({
  table,
  density,
  onDensityChange,
  onReset,
}: DataTableViewOptionsProps<TData>) {
  const { currentLang, t } = useI18n();
  const [draggedColumnId, setDraggedColumnId] = useState<string | null>(null);
  const columns = table.getAllLeafColumns();
  const order = table.getState().columnOrder;
  const pinning = table.getState().columnPinning;
  const leftOrder = pinning.left ?? [];
  const rightOrder = pinning.right ?? [];
  const pinnedIds = new Set([...leftOrder, ...rightOrder]);
  const centerOrder = order.filter((id) => !pinnedIds.has(id));
  const columnsById = new Map(columns.map((column) => [column.id, column]));
  const orderedColumns = [...leftOrder, ...centerOrder, ...rightOrder]
    .map((id) => columnsById.get(id))
    .filter((column): column is (typeof columns)[number] => Boolean(column));

  const preventMenuSelection = (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
  };

  const moveTo = (id: string, targetId: string) => {
    const pin = table.getColumn(id)?.getIsPinned();
    const targetPin = table.getColumn(targetId)?.getIsPinned();
    if (pin !== targetPin) return;
    if (pin) {
      table.setColumnPinning((current) => ({
        ...current,
        [pin]: moveColumn(current[pin] ?? [], id, targetId),
      }));
      return;
    }
    table.setColumnOrder(moveColumn(order, id, targetId));
  };

  const moveBy = (
    event: MouseEvent<HTMLButtonElement>,
    id: string,
    direction: -1 | 1,
  ) => {
    preventMenuSelection(event);
    const pin = table.getColumn(id)?.getIsPinned();
    const activeOrder = pin ? pinning[pin] ?? [] : centerOrder;
    const index = activeOrder.indexOf(id);
    const target = activeOrder[index + direction];
    if (target) moveTo(id, target);
  };

  const dropColumn = (event: DragEvent<HTMLDivElement>, targetId: string) => {
    event.preventDefault();
    const draggedId = draggedColumnId ?? event.dataTransfer.getData("text/plain");
    if (draggedId) moveTo(draggedId, targetId);
    setDraggedColumnId(null);
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={t("table.viewOptions")}
        className={buttonVariants({ variant: "outline", size: "sm" })}
      >
        <SlidersHorizontal data-icon="inline-start" aria-hidden="true" />
        {t("table.viewOptions")}
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-80 max-w-[calc(100vw-2rem)]"
      >
        <DropdownMenuGroup>
          <DropdownMenuLabel>{t("table.columns")}</DropdownMenuLabel>
          {orderedColumns.map((column) => {
            const label = column.columnDef.meta?.label ?? column.id;
            const required = Boolean(column.columnDef.meta?.required);
            const pin = column.getIsPinned();
            const activeOrder = pin ? pinning[pin] ?? [] : centerOrder;
            const index = activeOrder.indexOf(column.id);
            return (
              <div
                key={column.id}
                draggable
                className="flex items-center gap-1 rounded-md"
                onDragStart={(event) => {
                  setDraggedColumnId(column.id);
                  event.dataTransfer.setData("text/plain", column.id);
                }}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => dropColumn(event, column.id)}
              >
                <DropdownMenuCheckboxItem
                  checked={column.getIsVisible()}
                  closeOnClick={false}
                  disabled={required || !column.getCanHide()}
                  className="min-w-0 flex-1"
                  onCheckedChange={(checked) =>
                    column.toggleVisibility(Boolean(checked))
                  }
                >
                  <span className="truncate">{label}</span>
                </DropdownMenuCheckboxItem>
                <div className="flex shrink-0 items-center gap-0.5" role="group">
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label={columnActionLabel(
                      t("table.moveLeft"),
                      label,
                      currentLang(),
                    )}
                    disabled={index <= 0}
                    onClick={(event) => moveBy(event, column.id, -1)}
                  >
                    <ArrowLeft aria-hidden="true" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label={columnActionLabel(
                      t("table.moveRight"),
                      label,
                      currentLang(),
                    )}
                    disabled={index < 0 || index >= activeOrder.length - 1}
                    onClick={(event) => moveBy(event, column.id, 1)}
                  >
                    <ArrowRight aria-hidden="true" />
                  </Button>
                  {pin ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      aria-label={`${t("table.unpin")} ${label}`}
                      onClick={(event) => {
                        preventMenuSelection(event);
                        column.pin(false);
                      }}
                    >
                      <PinOff aria-hidden="true" />
                    </Button>
                  ) : null}
                  {pin !== "right" ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      aria-label={columnActionLabel(
                        t("table.pinRight"),
                        label,
                        currentLang(),
                      )}
                      onClick={(event) => {
                        preventMenuSelection(event);
                        column.pin("right");
                      }}
                    >
                      <Pin aria-hidden="true" />
                    </Button>
                  ) : null}
                  {pin !== "left" ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      aria-label={columnActionLabel(
                        t("table.pinLeft"),
                        label,
                        currentLang(),
                      )}
                      onClick={(event) => {
                        preventMenuSelection(event);
                        column.pin("left");
                      }}
                    >
                      <Pin aria-hidden="true" />
                    </Button>
                  ) : null}
                </div>
              </div>
            );
          })}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup
          value={density}
          onValueChange={(value) => onDensityChange(value as DataTableDensity)}
        >
          <DropdownMenuLabel>{t("table.density")}</DropdownMenuLabel>
          <DropdownMenuRadioItem value="compact" closeOnClick={false}>
            {t("table.densityCompact")}
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="standard" closeOnClick={false}>
            {t("table.densityStandard")}
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="comfortable" closeOnClick={false}>
            {t("table.densityComfortable")}
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem onClick={onReset}>
            <RotateCcw aria-hidden="true" />
            {t("table.resetView")}
          </DropdownMenuItem>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
