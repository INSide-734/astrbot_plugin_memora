import { Button } from "@/components/ui/Button";
import type { Translate } from "@/lib/i18n";

interface GraphTimeRangeFilterProps {
  start: number;
  end: number;
  t: Translate;
  onStartChange: (value: number) => void;
  onEndChange: (value: number) => void;
  onReset: () => void;
}

/** 把距今小时数格式化为图谱时间范围文案。 */
function formatHours(hours: number, t: Translate): string {
  if (hours === 0) return t("graph.all");
  if (hours < 24) return t("graph.hoursShort", String(hours));
  return t("graph.daysShort", String(Math.round(hours / 24)));
}

/** 展示并约束图谱的双端时间范围筛选器。 */
export function GraphTimeRangeFilter({
  start,
  end,
  t,
  onStartChange,
  onEndChange,
  onReset,
}: GraphTimeRangeFilterProps) {
  return (
    <div className="flex flex-nowrap items-center gap-3 overflow-x-auto whitespace-nowrap border-t px-6 py-2">
      <span className="shrink-0 text-2xs text-muted-foreground">
        {t("graph.timeRange")}
      </span>
      <div className="relative flex h-6 max-w-[240px] flex-1 items-center">
        <input
          type="range"
          min="0"
          max="720"
          step="1"
          value={start}
          aria-label={`${t("graph.timeRange")} ${formatHours(start, t)}`}
          onChange={(event) => {
            const value = Number(event.target.value);
            if (value <= end) onStartChange(value);
          }}
          className="pointer-events-none absolute inset-x-0 z-10 h-1 appearance-none bg-transparent [&::-webkit-slider-thumb]:pointer-events-auto [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-white [&::-webkit-slider-thumb]:bg-[var(--color-accent)]"
          style={{ accentColor: "var(--color-accent)" }}
        />
        <input
          type="range"
          min="0"
          max="720"
          step="1"
          value={end}
          aria-label={`${t("graph.timeRange")} ${formatHours(end, t)}`}
          onChange={(event) => {
            const value = Number(event.target.value);
            if (value >= start) onEndChange(value);
          }}
          className="pointer-events-none absolute inset-x-0 h-1 appearance-none bg-transparent [&::-webkit-slider-thumb]:pointer-events-auto [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-white [&::-webkit-slider-thumb]:bg-[var(--color-accent-secondary)]"
          style={{ accentColor: "var(--color-accent-secondary)" }}
        />
        <div className="pointer-events-none absolute inset-x-0 h-1 rounded bg-border" />
      </div>
      <Button variant="link" size="xs" onClick={onReset} className="shrink-0">
        {t("common.reset")}
      </Button>
      <span className="w-28 shrink-0 text-right text-2xs tabular-nums text-muted-foreground">
        {start === 0 && end >= 720
          ? t("graph.all")
          : `${formatHours(start, t)} – ${formatHours(end, t)}`}
      </span>
    </div>
  );
}
