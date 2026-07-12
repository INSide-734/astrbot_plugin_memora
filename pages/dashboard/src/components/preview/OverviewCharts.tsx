import { Area, AreaChart, Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";

export interface DailyMemoryCount {
  date: string;
  count: number;
}

export interface NamedCount {
  name: string;
  count: number;
}

const trendConfig = {
  count: { label: "Memories", color: "var(--primary)" },
} satisfies ChartConfig;

const importanceConfig = {
  count: { label: "Memories", color: "var(--primary)" },
} satisfies ChartConfig;

function shortDate(value: string) {
  const parts = value.split("-");
  return parts.length === 3 ? `${parts[1]}/${parts[2]}` : value;
}

export function GrowthTrendChart({ data, ariaLabel, valueLabel }: { data: DailyMemoryCount[]; ariaLabel: string; valueLabel: string }) {
  const tickInterval = data.length <= 7 ? 0 : data.length <= 30 ? 4 : 13;
  return (
    <div role="img" aria-label={ariaLabel} className="h-64 min-w-0">
      <ChartContainer config={trendConfig} className="h-full w-full">
        <AreaChart data={data} margin={{ top: 12, right: 8, left: 0, bottom: 0 }} accessibilityLayer>
          <CartesianGrid vertical={false} strokeDasharray="3 3" />
          <XAxis dataKey="date" tickFormatter={shortDate} interval={tickInterval} tickLine={false} axisLine={false} minTickGap={18} />
          <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={38} />
          <ChartTooltip content={<ChartTooltipContent valueLabel={valueLabel} />} />
          <Area
            dataKey="count"
            type="monotone"
            fill="var(--color-count)"
            fillOpacity={0.14}
            stroke="var(--color-count)"
            strokeWidth={2}
            isAnimationActive={false}
          />
        </AreaChart>
      </ChartContainer>
    </div>
  );
}

export function StatusComposition({
  items,
  ariaLabel,
}: {
  items: Array<NamedCount & { colorClass: string }>;
  ariaLabel: string;
}) {
  const total = items.reduce((sum, item) => sum + item.count, 0);
  return (
    <div role="img" aria-label={ariaLabel} className="space-y-3">
      <div className="flex h-3 overflow-hidden rounded-full bg-muted" aria-hidden="true">
        {items.map((item) => (
          <span key={item.name} className={item.colorClass} style={{ width: `${total > 0 ? (item.count / total) * 100 : 0}%` }} />
        ))}
      </div>
      <div className="grid grid-cols-3 gap-2">
        {items.map((item) => (
          <div key={item.name} className="min-w-0">
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className={`size-2 shrink-0 rounded-sm ${item.colorClass}`} aria-hidden="true" />
              <span className="truncate">{item.name}</span>
            </div>
            <div className="mt-0.5 text-sm font-semibold tabular-nums text-foreground">
              {item.count.toLocaleString()} <span className="text-xs font-normal text-muted-foreground">{total > 0 ? `${Math.round((item.count / total) * 100)}%` : "0%"}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function RankedBars({ items, ariaLabel }: { items: NamedCount[]; ariaLabel: string }) {
  const maximum = Math.max(1, ...items.map((item) => item.count));
  return (
    <div role="img" aria-label={ariaLabel} className="space-y-2.5">
      {items.map((item) => (
        <div key={item.name} className="grid grid-cols-[minmax(4.5rem,7rem)_1fr_2.5rem] items-center gap-2 text-xs">
          <span className="truncate text-muted-foreground" title={item.name}>{item.name}</span>
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-primary" style={{ width: `${(item.count / maximum) * 100}%` }} />
          </div>
          <span className="text-right font-medium tabular-nums text-foreground">{item.count}</span>
        </div>
      ))}
    </div>
  );
}

export function ImportanceDistribution({ items, ariaLabel }: { items: NamedCount[]; ariaLabel: string }) {
  return (
    <div role="img" aria-label={ariaLabel} className="h-32 min-w-0">
      <ChartContainer config={importanceConfig} className="h-full w-full">
        <BarChart data={items} margin={{ top: 8, right: 2, left: 0, bottom: 0 }} accessibilityLayer>
          <CartesianGrid vertical={false} strokeDasharray="3 3" />
          <XAxis dataKey="name" tickLine={false} axisLine={false} interval={1} fontSize={10} />
          <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={30} />
          <ChartTooltip content={<ChartTooltipContent valueLabel="Memories" />} />
          <Bar dataKey="count" fill="var(--color-count)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ChartContainer>
    </div>
  );
}
