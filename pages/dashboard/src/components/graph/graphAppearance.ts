import type { GraphOptions } from "@antv/g6";
import type { Theme } from "@/hooks/useTheme";
import { GRAPH_NODE_COLORS } from "./GraphNodeDetail";
// 边类型对应颜色、虚线样式和翻译键。
const EDGE_STYLES: Record<string, { color: string; dash: boolean; label: string }> = {
  before:      { color: "#748ffc", dash: true,  label: "graph.edgeBefore" },
  after:       { color: "#4dabf7", dash: true,  label: "graph.edgeAfter" },
  during:      { color: "#a5d8ff", dash: true,  label: "graph.edgeDuring" },
  results_in:  { color: "#51cf66", dash: false, label: "graph.edgeResultsIn" },
  caused_by:   { color: "#ff6b6b", dash: false, label: "graph.edgeCausedBy" },
  prevents:    { color: "#adb5bd", dash: true,  label: "graph.edgePrevents" },
  is_a:        { color: "#cc5de8", dash: true,  label: "graph.edgeIsA" },
  describes:       { color: "#bea4d8", dash: false, label: "graph.edgeDescribes" },
  mentioned_in:    { color: "#94a3b8", dash: false, label: "graph.edgeMentionedIn" },
  co_occurs_with:  { color: "#a0a4b0", dash: false, label: "graph.edgeCoOccurs" },
};

// 时序边使用虚线，因果边使用带标签的实线。
const TEMPORAL_EDGES = new Set(["before", "after", "during"]);
const CAUSAL_EDGES = new Set(["results_in", "caused_by"]);
// 加快力模型降温，在完整保留节点、边和碰撞检测的前提下缩短首次布局时间。
const GRAPH_LAYOUT_ALPHA_DECAY = 0.03;

const EDGE_DEFAULT = { color: "rgba(148,163,184,0.5)", dash: false, label: "graph.edgeOther" };
/** 返回边类型对应的画布样式。 */
function edgeStyle(type: string | undefined) { return EDGE_STYLES[type ?? ""] ?? EDGE_DEFAULT; }

/** 把主题选择态 token 解析为 G6 可直接消费的颜色。 */
function resolveSelectionColor(
  token: "--selection-indicator" | "--selection-border",
  fallback: string,
): string {
  if (typeof document === "undefined" || !document.body) return fallback;

  const probe = document.createElement("span");
  probe.hidden = true;
  probe.style.color = `var(${token}, ${fallback})`;
  document.body.appendChild(probe);

  try {
    const value = getComputedStyle(probe).color.trim();
    return value && !value.includes("var(") && !value.includes("color-mix(")
      ? value
      : fallback;
  } finally {
    probe.remove();
  }
}

/** 构建随主题和动效偏好变化的 G6 节点与边配置。 */
function graphElementOptions(
  theme: Theme,
  animateLabels: boolean,
): Pick<GraphOptions, "node" | "edge"> {
  const selectedStroke = resolveSelectionColor(
    "--selection-indicator",
    theme === "dark" ? "#f1f3f5" : "#343a40",
  );
  const hoverStroke = resolveSelectionColor(
    "--selection-border",
    theme === "dark" ? "rgba(241,243,245,0.35)" : "rgba(52,58,64,0.35)",
  );
  const labelAnimation = animateLabels
    ? {
        update: [{
          fields: ["fill"],
          shape: "label",
          duration: 200,
          easing: "ease-out",
        }],
      }
    : false;

  return {
    node: {
      type: "circle",
      style: {
        size: 24,
        fill: (datum: Record<string, unknown>) => (
          GRAPH_NODE_COLORS[String((datum as any).data?.type ?? "other")]
            ?? GRAPH_NODE_COLORS.other
        ),
        fillOpacity: 0.85,
        stroke: "transparent",
        labelText: (datum: Record<string, unknown>) => (
          String((datum as any).data?.label ?? datum.id ?? "")
        ),
        labelFontSize: 10,
        labelFill: theme === "dark" ? "#e8eaed" : "#1e1e1e",
        labelOffsetY: 12,
        labelPlacement: "bottom",
      },
      state: {
        hover: { stroke: hoverStroke, lineWidth: 2 },
        selected: { stroke: selectedStroke, lineWidth: 3 },
      },
      animation: labelAnimation,
    },
    edge: {
      type: "line",
      style: {
        stroke: (datum: Record<string, unknown>) => {
          const type = String((datum as any)?.data?.type ?? "");
          return edgeStyle(type).color;
        },
        lineWidth: (datum: Record<string, unknown>) => {
          const type = String((datum as any)?.data?.type ?? "");
          return CAUSAL_EDGES.has(type) ? 2 : 0.8;
        },
        lineDash: (datum: Record<string, unknown>) => {
          const type = String((datum as any)?.data?.type ?? "");
          return edgeStyle(type).dash ? [6, 3] : undefined;
        },
        labelText: (datum: Record<string, unknown>) => {
          const data = (datum as any)?.data;
          return data?.label ?? undefined;
        },
        labelFontSize: 9,
        labelFill: theme === "dark" ? "#94a3b8" : "#64748b",
        labelOffsetY: -6,
      },
      animation: labelAnimation,
    },
  };
}

/** 判断当前浏览器是否允许图谱动效。 */
function graphMotionEnabled(): boolean {
  try {
    return !(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false);
  } catch {
    return true;
  }
}

export {
  EDGE_STYLES,
  TEMPORAL_EDGES,
  CAUSAL_EDGES,
  GRAPH_LAYOUT_ALPHA_DECAY,
  graphElementOptions,
  graphMotionEnabled,
};
