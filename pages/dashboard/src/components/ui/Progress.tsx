import * as React from "react";

import { cn } from "@/lib/utils";

interface ProgressProps extends Omit<React.ComponentProps<"div">, "value"> {
  value: number;
  min?: number;
  max?: number;
  indicatorClassName?: string;
}

function Progress({
  value,
  min = 0,
  max = 1,
  className,
  indicatorClassName,
  ...props
}: ProgressProps) {
  const lower = Math.min(min, max);
  const upper = Math.max(min, max);
  const safeValue = Number.isFinite(value) ? value : lower;
  const clampedValue = Math.min(upper, Math.max(lower, safeValue));
  const range = upper - lower;
  const percentage = range > 0 ? ((clampedValue - lower) / range) * 100 : 0;

  return (
    <div
      data-slot="progress"
      role="progressbar"
      aria-valuemin={lower}
      aria-valuemax={upper}
      aria-valuenow={clampedValue}
      className={cn(
        "h-2.5 w-full overflow-hidden rounded-full border border-border bg-muted",
        className,
      )}
      {...props}
    >
      <div
        data-slot="progress-indicator"
        className={cn("h-full rounded-full bg-primary transition-[width] duration-300", indicatorClassName)}
        style={{ width: `${percentage}%` }}
      />
    </div>
  );
}

export { Progress };
export type { ProgressProps };
