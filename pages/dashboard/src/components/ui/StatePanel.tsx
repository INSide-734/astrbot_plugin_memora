import { AlertTriangle, Inbox, LoaderCircle } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils";

type PanelState = "loading" | "empty" | "error";

interface StatePanelProps {
  state: PanelState;
  title: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
  className?: string;
}

export function StatePanel({ actionLabel, className, description, onAction, state, title }: StatePanelProps) {
  if (state === "loading") {
    return (
      <div
        role="status"
        aria-busy="true"
        data-state="loading"
        className={cn("flex min-h-48 flex-col justify-center gap-3 p-6", className)}
      >
        <span className="sr-only">{title}</span>
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-4 w-full max-w-xl" />
        <Skeleton className="h-4 w-2/3 max-w-md" />
      </div>
    );
  }

  const isError = state === "error";
  const Icon = isError ? AlertTriangle : Inbox;

  return (
    <div
      role={isError ? "alert" : "status"}
      data-state={state}
      className={cn("flex min-h-48 flex-col items-center justify-center gap-3 px-6 py-10 text-center", className)}
    >
      <div className={cn("flex size-10 items-center justify-center rounded-lg border bg-muted", isError && "border-destructive/30 bg-destructive/10 text-destructive")}>
        <Icon className="size-5" aria-hidden="true" />
      </div>
      <div className="space-y-1">
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        {description ? <p className="max-w-md text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {actionLabel && onAction ? (
        <Button variant="outline" onClick={onAction}>
          {isError ? <LoaderCircle aria-hidden="true" /> : null}
          {actionLabel}
        </Button>
      ) : null}
    </div>
  );
}
