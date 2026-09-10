import { MetricGrid } from "@/components/layout/PageLayout";
import type { Translate } from "@/lib/i18n";

interface GraphStatsProps {
  totalMemories: number;
  nodeCount: number;
  edgeCount: number;
  sessionCount: number;
  t: Translate;
}

/** 展示图谱工作区的四项概览统计。 */
export function GraphStats({
  totalMemories,
  nodeCount,
  edgeCount,
  sessionCount,
  t,
}: GraphStatsProps) {
  const metrics = [
    { label: t("stats.total"), value: totalMemories },
    { label: t("graph.nodes"), value: nodeCount },
    { label: t("graph.edges"), value: edgeCount },
    { label: t("stats.sessions"), value: sessionCount },
  ];

  return (
    <div
      data-slot="graph-stats-scroll"
      className="w-full overflow-x-auto border-b bg-muted/20"
    >
      <MetricGrid
        minItemWidth="8rem"
        className="min-w-[32rem] gap-0 px-4 sm:px-5 lg:px-6"
        style={{ gridTemplateColumns: "repeat(4, minmax(8rem, 1fr))" }}
      >
        {metrics.map((metric) => (
          <div
            key={metric.label}
            className="border-r px-4 py-2 text-center last:border-r-0"
          >
            <div className="text-lg font-bold tabular-nums text-foreground">
              {metric.value}
            </div>
            <div className="text-2xs text-muted-foreground">{metric.label}</div>
          </div>
        ))}
      </MetricGrid>
    </div>
  );
}
