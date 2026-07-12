import * as React from "react";
import * as RechartsPrimitive from "recharts";
import type { TooltipValueType } from "recharts";

import { useI18n } from "@/hooks/useI18n";
import { dashboardLocale } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const THEMES = { light: "", dark: ".dark" } as const;

export type ChartConfig = Record<
  string,
  {
    label?: React.ReactNode;
  } & (
    | { color?: string; theme?: never }
    | { color?: never; theme: Record<keyof typeof THEMES, string> }
  )
>;

const ChartContext = React.createContext<ChartConfig>({});

function ChartContainer({
  id,
  className,
  children,
  config,
  ...props
}: React.ComponentProps<"div"> & {
  config: ChartConfig;
  children: React.ComponentProps<typeof RechartsPrimitive.ResponsiveContainer>["children"];
}) {
  const uniqueId = React.useId();
  const chartId = `chart-${id ?? uniqueId.replace(/:/g, "")}`;
  const colorConfig = Object.entries(config).filter(([, item]) => item.theme ?? item.color);

  return (
    <ChartContext.Provider value={config}>
      <div
        data-slot="chart"
        data-chart={chartId}
        className={cn(
          "flex min-h-0 min-w-0 justify-center text-xs [&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground [&_.recharts-cartesian-grid_line]:stroke-border [&_.recharts-layer]:outline-none [&_.recharts-surface]:outline-none",
          className,
        )}
        {...props}
      >
        {colorConfig.length > 0 ? (
          <style
            dangerouslySetInnerHTML={{
              __html: Object.entries(THEMES)
                .map(([theme, prefix]) => `${prefix} [data-chart=${chartId}] {\n${colorConfig
                  .map(([key, item]) => {
                    const color = item.theme?.[theme as keyof typeof item.theme] ?? item.color;
                    return color ? `  --color-${key}: ${color};` : null;
                  })
                  .filter(Boolean)
                  .join("\n")}\n}`)
                .join("\n"),
            }}
          />
        ) : null}
        <RechartsPrimitive.ResponsiveContainer initialDimension={{ width: 640, height: 240 }}>
          {children}
        </RechartsPrimitive.ResponsiveContainer>
      </div>
    </ChartContext.Provider>
  );
}

const ChartTooltip = RechartsPrimitive.Tooltip;

function ChartTooltipContent({
  active,
  payload,
  label,
  className,
  valueLabel,
  formatLabel,
}: React.ComponentProps<"div"> &
  RechartsPrimitive.DefaultTooltipContentProps<TooltipValueType, string> & {
    active?: boolean;
    valueLabel?: React.ReactNode;
    formatLabel?: (label: string) => React.ReactNode;
  }) {
  const { currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  if (!active || !payload?.length) return null;
  const value = payload[0]?.value;

  return (
    <div className={cn("grid min-w-32 gap-1 rounded-lg border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md", className)}>
      <span className="font-medium">{formatLabel ? formatLabel(String(label ?? "")) : label}</span>
      <span className="flex items-center justify-between gap-4 text-muted-foreground">
        {valueLabel}
        <strong className="font-mono text-foreground">{typeof value === "number" ? value.toLocaleString(locale) : String(value ?? "--")}</strong>
      </span>
    </div>
  );
}

export { ChartContainer, ChartTooltip, ChartTooltipContent };
