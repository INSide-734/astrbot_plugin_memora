import type { CSSProperties, HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

type PageVariant = "standard" | "dense" | "workspace";

interface PageFrameProps extends HTMLAttributes<HTMLDivElement> {
  variant?: PageVariant;
}

export function PageFrame({ className, variant = "standard", ...props }: PageFrameProps) {
  return (
    <section
      role="region"
      data-slot="page-frame"
      data-layout={variant}
      className={cn(
        "flex h-full min-h-0 w-full flex-col bg-background text-foreground",
        variant === "dense" && "overflow-hidden",
        variant === "workspace" && "overflow-hidden",
        className,
      )}
      {...props}
    />
  );
}

interface PageHeaderProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  title: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  status?: ReactNode;
}

export function PageHeader({
  actions,
  className,
  description,
  icon,
  status,
  title,
  ...props
}: PageHeaderProps) {
  return (
    <header
      data-slot="page-header"
      className={cn(
        "flex min-h-16 shrink-0 flex-wrap items-center justify-between gap-3 border-b bg-background px-4 py-3 sm:px-5 lg:px-6",
        className,
      )}
      {...props}
    >
      <div className="flex min-w-0 items-center gap-3">
        {icon ? <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border bg-muted text-foreground">{icon}</div> : null}
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold leading-7 text-foreground">{title}</h1>
            {status}
          </div>
          {description ? <p className="mt-0.5 max-w-3xl text-sm text-muted-foreground">{description}</p> : null}
        </div>
      </div>
      {actions ? (
        <div
          data-slot="page-header-actions"
          className="flex w-full min-w-0 flex-wrap items-center gap-2 [&>*]:min-w-0 [&>*]:max-w-full sm:w-auto sm:shrink-0"
        >
          {actions}
        </div>
      ) : null}
    </header>
  );
}

export function PageToolbar({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      role="toolbar"
      data-slot="page-toolbar"
      className={cn(
        "flex min-h-12 shrink-0 flex-wrap items-center gap-2 border-b bg-muted/30 px-4 py-2 sm:px-5 lg:px-6",
        className,
      )}
      {...props}
    />
  );
}

interface PageContentProps extends HTMLAttributes<HTMLDivElement> {
  width?: "constrained" | "full";
}

export function PageContent({ className, width = "constrained", ...props }: PageContentProps) {
  return (
    <div
      data-slot="page-content"
      className={cn(
        "min-h-0 flex-1 overflow-auto px-4 py-4 sm:px-5 sm:py-5 lg:px-6 lg:py-6",
        width === "constrained" && "mx-auto w-full max-w-[1440px]",
        className,
      )}
      {...props}
    />
  );
}

interface MetricGridProps extends HTMLAttributes<HTMLDivElement> {
  minItemWidth?: string;
}

export function MetricGrid({ className, minItemWidth = "14rem", style, ...props }: MetricGridProps) {
  const gridStyle = {
    gridTemplateColumns: `repeat(auto-fit, minmax(min(100%, ${minItemWidth}), 1fr))`,
    ...style,
  } as CSSProperties;

  return (
    <div
      data-slot="metric-grid"
      className={cn("grid gap-4", className)}
      style={gridStyle}
      {...props}
    />
  );
}
