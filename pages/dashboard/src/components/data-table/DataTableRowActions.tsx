import { MoreHorizontal } from "lucide-react";

import { buttonVariants } from "@/components/ui/Button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export interface DataTableAction<TData> {
  id: string;
  label: string;
  destructive?: boolean;
  disabled?: boolean;
  onSelect(row: TData): void;
}

interface DataTableRowActionsProps<TData> {
  row: TData;
  rowLabel: string;
  actions: DataTableAction<TData>[];
}

export function DataTableRowActions<TData>({
  row,
  rowLabel,
  actions,
}: DataTableRowActionsProps<TData>) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={rowLabel}
        title={rowLabel}
        className={buttonVariants({ variant: "ghost", size: "icon-sm" })}
        onClick={(event) => event.stopPropagation()}
      >
        <MoreHorizontal aria-hidden="true" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-40">
        <DropdownMenuGroup>
          {actions.map((action) => (
            <DropdownMenuItem
              key={action.id}
              disabled={action.disabled}
              variant={action.destructive ? "destructive" : "default"}
              onClick={(event) => {
                event.stopPropagation();
                action.onSelect(row);
              }}
            >
              {action.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
