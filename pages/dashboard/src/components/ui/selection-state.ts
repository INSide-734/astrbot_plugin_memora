import { cva } from "class-variance-authority"

import { cn } from "@/lib/utils"

export type SelectionKind =
  | "navigation"
  | "control"
  | "row"
  | "surface"
  | "current-item"

const selectionStateRecipe = cva(
  "transition-[color,background-color,border-color,box-shadow] duration-150 motion-reduce:transition-none",
  {
    variants: {
      kind: {
        navigation:
          "aria-[current=page]:border-primary aria-[current=page]:bg-primary aria-[current=page]:text-primary-foreground data-checked:border-primary data-checked:bg-primary data-checked:text-primary-foreground",
        control:
          "data-active:bg-[var(--selection-surface)] data-active:text-[var(--selection-foreground)] data-active:shadow-[inset_0_-2px_0_var(--selection-indicator)] aria-[pressed=true]:bg-[var(--selection-surface)] aria-[pressed=true]:text-[var(--selection-foreground)] aria-[pressed=true]:shadow-[inset_0_-2px_0_var(--selection-indicator)] aria-[selected=true]:bg-[var(--selection-surface)] aria-[selected=true]:text-[var(--selection-foreground)] aria-[selected=true]:shadow-[inset_0_-2px_0_var(--selection-indicator)]",
        row:
          "data-[state=selected]:bg-[var(--selection-surface)] data-[state=selected]:text-[var(--selection-foreground)] data-[state=selected]:shadow-[inset_2px_0_0_var(--selection-indicator)] data-[state=selected]:hover:bg-[var(--selection-surface)] data-[state=selected]:has-aria-expanded:bg-[var(--selection-surface)]",
        surface:
          "data-[selected]:bg-[var(--selection-surface)] data-[selected]:text-[var(--selection-foreground)] data-[selected]:shadow-[inset_0_0_0_1px_var(--selection-border)] data-[selected=true]:bg-[var(--selection-surface)] data-[selected=true]:text-[var(--selection-foreground)] data-[selected=true]:shadow-[inset_0_0_0_1px_var(--selection-border)] aria-[checked=true]:bg-[var(--selection-surface)] aria-[checked=true]:text-[var(--selection-foreground)] aria-[checked=true]:shadow-[inset_0_0_0_1px_var(--selection-border)]",
        "current-item":
          "aria-[current=true]:bg-[var(--selection-surface)] aria-[current=true]:text-[var(--selection-foreground)] aria-[current=true]:shadow-[inset_2px_0_0_var(--selection-indicator)] aria-expanded:bg-[var(--selection-surface)] aria-expanded:text-[var(--selection-foreground)] aria-expanded:shadow-[inset_2px_0_0_var(--selection-indicator)] data-[selected=true]:bg-[var(--selection-surface)] data-[selected=true]:text-[var(--selection-foreground)] data-[selected=true]:shadow-[inset_2px_0_0_var(--selection-indicator)]",
      },
      selected: {
        false: "",
        true: "",
      },
    },
    compoundVariants: [
      {
        kind: "navigation",
        selected: true,
        className: "border-primary bg-primary text-primary-foreground",
      },
      {
        kind: "control",
        selected: true,
        className:
          "bg-[var(--selection-surface)] text-[var(--selection-foreground)] shadow-[inset_0_-2px_0_var(--selection-indicator)]",
      },
      {
        kind: "row",
        selected: true,
        className:
          "bg-[var(--selection-surface)] text-[var(--selection-foreground)] shadow-[inset_2px_0_0_var(--selection-indicator)]",
      },
      {
        kind: "surface",
        selected: true,
        className:
          "bg-[var(--selection-surface)] text-[var(--selection-foreground)] shadow-[inset_0_0_0_1px_var(--selection-border)]",
      },
      {
        kind: "current-item",
        selected: true,
        className:
          "bg-[var(--selection-surface)] text-[var(--selection-foreground)] shadow-[inset_2px_0_0_var(--selection-indicator)]",
      },
    ],
    defaultVariants: {
      selected: false,
    },
  },
)

export function selectionStateVariants({
  kind,
  selected = false,
}: {
  kind: SelectionKind
  selected?: boolean
}): string {
  return cn(selectionStateRecipe({ kind, selected }))
}
