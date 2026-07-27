import { useState, useEffect, useRef, useCallback } from "react";
import { GitGraph, Search, Maximize2, Minimize2 } from "lucide-react";
import { Graph, type GraphOptions, type IPointerEvent } from "@antv/g6";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { useI18n } from "@/hooks/useI18n";
import type { Theme } from "@/hooks/useTheme";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/input";
import { PageContent, PageFrame, PageHeader, PageToolbar } from "@/components/layout/PageLayout";
import {
  GRAPH_NODE_COLORS,
  GraphNodeDetail,
  graphNodeTypeLabel,
} from "@/components/graph/GraphNodeDetail";
import {
  buildGraphRenderData,
  type GraphEdgePayload,
} from "@/components/graph/graphRenderData";
import { GraphTimeRangeFilter } from "@/components/graph/GraphTimeRangeFilter";
import { GraphStats } from "@/components/graph/GraphStats";
import type { GraphNode } from "@/types";
import { dashboardLocale, formatDashboardPercent } from "@/lib/i18n";

interface GraphPageProps {
  showToast: (msg: string, isError?: boolean) => void;
  theme: Theme;
}

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

/** 管理图谱工作区的数据加载、G6 生命周期和交互状态。 */
export function GraphPage({ showToast, theme }: GraphPageProps) {
  const { t, currentLang } = useI18n();
  const locale = dashboardLocale(currentLang());
  const [totalMemories, setTotal] = useState(0);
  const [nodeCount, setNodeCount] = useState(0);
  const [edgeCount, setEdgeCount] = useState(0);
  const [sessionCount, setSessionCount] = useState(0);
  const [query, setQuery] = useState("");
  const [memoryId, setMemoryId] = useState("");
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null);
  const [scale, setScale] = useState(1);
  const [timeRangeStart, setTimeRangeStart] = useState(0); // 距今小时数，0 表示现在。
  const [timeRangeEnd, setTimeRangeEnd] = useState(720); // 距今小时数上限。
  const [isFullscreen, setIsFullscreen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const fullscreenRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const mountedRef = useRef(false);
  const requestGenerationRef = useRef(0);
  const graphGenerationRef = useRef(0);
  const renderGenerationRef = useRef(0);
  const themeOperationGenerationRef = useRef(0);
  const timeRangeRef = useRef({ start: timeRangeStart, end: timeRangeEnd });
  const themeRef = useRef(theme);
  const appliedThemeRef = useRef(theme);
  const nodesRef = useRef<GraphNode[]>([]);
  const allEdgesRef = useRef<GraphEdgePayload[]>([]);
  const selectedNodeIdRef = useRef<string | null>(null);
  const [graphState, setGraphState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestGenerationRef.current += 1;
      graphGenerationRef.current += 1;
      renderGenerationRef.current += 1;
      themeOperationGenerationRef.current += 1;
    };
  }, []);

  /** 拉取概览统计并忽略已过期请求。 */
  const fetchOverview = useCallback(async () => {
    try {
      const data = unwrapApiData(await apiRequest("stats"));
      if (!mountedRef.current) return;
      setTotal(Number(data.total_memories ?? data.total_count ?? 0));
      setNodeCount(Number(data.graph_nodes ?? 0));
      setEdgeCount(Number(data.graph_edges ?? 0));
      const sessions = (data.sessions ?? {}) as Record<string, unknown>;
      setSessionCount(Object.keys(sessions).length);
    } catch (e) {
      if (mountedRef.current) showToast(String(e), true);
    }
  }, [showToast]);

  themeRef.current = theme;
  timeRangeRef.current = { start: timeRangeStart, end: timeRangeEnd };

  // 缓存最新已加载的图谱数据，供时间筛选重放。
  const lastDataRef = useRef<{ nodes: GraphNode[]; edges: GraphEdgePayload[] } | null>(null);
  // 记录已写入完整数据的图实例，防止新实例误把缓存视为已渲染数据。
  const dataRenderedGraphRef = useRef<Graph | null>(null);

  /** 清除画布与详情面板中的当前节点选择。 */
  const clearGraphSelection = useCallback((graph = graphRef.current) => {
    const selectedId = selectedNodeIdRef.current;
    if (graph && selectedId) {
      void graph.setElementState(selectedId, [], false).catch(() => {});
    }
    selectedNodeIdRef.current = null;
    setSelectedNode(null);
  }, []);

  // 创建 G6 实例（不渲染数据，等 updateGraphData 调用）
  /** 创建 G6 实例并绑定节点、画布与视口事件。 */
  const createGraph = useCallback((container: HTMLDivElement, initialTheme: Theme) => {
    const motionEnabled = graphMotionEnabled();
    const graph = new Graph({
      container,
      autoFit: "view",
      animation: motionEnabled,
      ...graphElementOptions(initialTheme, motionEnabled),
      layout: {
        type: "d3-force",
        preventOverlap: true,
        nodeStrength: -200,
        linkDistance: 120,
        alphaDecay: GRAPH_LAYOUT_ALPHA_DECAY,
        animation: motionEnabled,
      },
      behaviors: [
        "drag-canvas",
        "zoom-canvas",
        "drag-element",
        {
          type: "click-select",
          multiple: false,
          state: "selected",
          degree: 0,
          animation: motionEnabled,
          enable: (event: IPointerEvent) => (
            event.targetType === "node" || event.targetType === "canvas"
          ),
          onClick: (event: IPointerEvent) => {
            if (!mountedRef.current || graphRef.current !== graph) return;
            if (event.targetType === "canvas") {
              selectedNodeIdRef.current = null;
              setSelectedNode(null);
              return;
            }
            if (event.targetType !== "node") return;

            if (!("id" in event.target)) return;
            const id = String(event.target.id ?? "");
            if (!id) return;

            if (!graph.getElementState(id).includes("selected")) {
              if (selectedNodeIdRef.current === id) {
                selectedNodeIdRef.current = null;
                setSelectedNode(null);
              }
              return;
            }

            const node = nodesRef.current.find((item) => String(item.id) === id);
            if (node) {
              void Promise.resolve(graph.focusElement(id, {
                duration: motionEnabled ? 500 : 0,
              })).catch((error) => {
                if (mountedRef.current && graphRef.current === graph) {
                  showToast(String(error), true);
                }
              });
              selectedNodeIdRef.current = id;
              setSelectedNode(node);
            }
          },
        },
        { type: "hover-activate", degree: 1, direction: "both" },
      ],
      data: { nodes: [], edges: [] },
    });

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    graph.on("node:pointerover", (evt: any) => {
      const id = evt.target?.id as string | undefined;
      if (id) {
        const node = nodesRef.current.find((n) => String(n.id) === id);
        setHoveredNode(node ?? null);
      }
    });
    graph.on("node:pointerout", () => setHoveredNode(null));

    graph.on("viewport:change", () => setScale(graph.getZoom()));

    return graph;
  }, [showToast]);

  // 容器挂载时只创建一次图实例；主题变化由独立 effect 原位更新。
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const initialTheme = themeRef.current;
    const graph = createGraph(el, initialTheme);
    const graphGeneration = ++graphGenerationRef.current;
    graphRef.current = graph;
    appliedThemeRef.current = initialTheme;

    // 如果已有缓存数据，立即渲染
    const cached = lastDataRef.current;
    if (cached) {
      void updateGraphData(cached.nodes, cached.edges).catch((error) => {
        if (
          mountedRef.current
          && graphRef.current === graph
          && graphGenerationRef.current === graphGeneration
        ) {
          showToast(String(error), true);
          setGraphState("error");
        }
      });
    }

    return () => {
      graphGenerationRef.current += 1;
      renderGenerationRef.current += 1;
      themeOperationGenerationRef.current += 1;
      if (dataRenderedGraphRef.current === graph) dataRenderedGraphRef.current = null;
      graph.destroy();
      if (graphRef.current === graph) graphRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [createGraph, showToast]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || appliedThemeRef.current === theme) return;

    appliedThemeRef.current = theme;
    const graphGeneration = graphGenerationRef.current;
    const operationGeneration = ++themeOperationGenerationRef.current;
    // 仅允许当前图实例的最后一次主题操作提交状态。
    const isCurrentOperation = () => (
      mountedRef.current
      && graphRef.current === graph
      && graphGenerationRef.current === graphGeneration
      && themeOperationGenerationRef.current === operationGeneration
    );
    const motionEnabled = graphMotionEnabled();
    graph.setOptions(graphElementOptions(theme, motionEnabled));
    void (async () => {
      await graph.draw();
      if (!isCurrentOperation()) return;
        const selectedId = selectedNodeIdRef.current;
        if (selectedId) {
          await graph.setElementState(selectedId, ["selected"], false);
        }
    })().catch((error) => {
      if (!isCurrentOperation()) return;
        console.error("[GraphPage] G6 主题重绘失败:", error);
        showToast(String(error), true);
      });
  }, [showToast, theme]);

  /**
   * 根据当前时间范围同步图数据和选择状态到 G6。
   *
   * 同一份节点、边数据只调用 G6 的可见性更新，避免重新 setData、render 和全图布局。
   * @param nodes 最新图谱节点。
   * @param edges 最新图谱边。
   * @returns 本次操作是否仍是当前图实例的有效提交。
   */
  const updateGraphData = useCallback(async (nodes: GraphNode[], edges: GraphEdgePayload[]) => {
    const g = graphRef.current;
    if (!g || !mountedRef.current) return false;
    const graphGeneration = graphGenerationRef.current;
    const operationGeneration = ++renderGenerationRef.current;
    // 仅允许当前图实例的最后一次渲染提交状态。
    const isCurrentOperation = () => (
      mountedRef.current
      && graphRef.current === g
      && graphGenerationRef.current === graphGeneration
      && renderGenerationRef.current === operationGeneration
    );

    const dataUnchanged = dataRenderedGraphRef.current === g
      && lastDataRef.current?.nodes === nodes
      && lastDataRef.current.edges === edges;
    nodesRef.current = nodes;
    allEdgesRef.current = edges;
    lastDataRef.current = { nodes, edges };
    const renderData = buildGraphRenderData(
      nodes,
      edges,
      timeRangeRef.current,
      Date.now() / 1000,
      CAUSAL_EDGES,
    );
    for (const invalidEdge of renderData.invalidEdges) {
      console.warn(
        `[GraphPage] 过滤孤立边: ${invalidEdge.source} → ${invalidEdge.target}，节点列表中存在=${invalidEdge.sourceExists}/${invalidEdge.targetExists}`,
      );
    }

    const selectedId = selectedNodeIdRef.current;
    const selectionRemainsVisible = selectedId !== null
      && renderData.visibleNodeIds.has(selectedId);
    const refreshedSelectedNode = selectionRemainsVisible
      ? nodes.find((node) => String(node.id) === selectedId) ?? null
      : null;
    if (selectedId && !selectionRemainsVisible) {
      clearGraphSelection(g);
    }

    try {
      if (!isCurrentOperation()) return false;
      const supportsVisibilityUpdates = typeof g.setElementVisibility === "function";
      if (dataUnchanged && supportsVisibilityUpdates) {
        await g.setElementVisibility(renderData.visibility, false);
      } else {
        g.setData(supportsVisibilityUpdates ? renderData.data : renderData.visibleData);
        await g.render();
        if (!isCurrentOperation()) return false;
        dataRenderedGraphRef.current = g;
        if (supportsVisibilityUpdates && renderData.hasHiddenElements) {
          await g.setElementVisibility(renderData.visibility, false);
        }
      }
      if (!isCurrentOperation()) return false;
      if (selectedId && selectionRemainsVisible) {
        await g.setElementState(selectedId, ["selected"], false);
        if (!isCurrentOperation()) return false;
      }
      setSelectedNode(refreshedSelectedNode);
      return true;
    } catch (err) {
      if (!isCurrentOperation()) return false;
      console.error("[GraphPage] G6 render 失败:", err);
      throw err;
    }
  }, [clearGraphSelection]);

  useEffect(() => {
    const cached = lastDataRef.current;
    if (!cached || !graphRef.current) return;
    void updateGraphData(cached.nodes, cached.edges)
      .then((applied) => {
        if (applied && mountedRef.current) setGraphState("ready");
      })
      .catch((error) => {
        if (!mountedRef.current) return;
        showToast(String(error), true);
        setGraphState("error");
      });
  }, [showToast, timeRangeStart, timeRangeEnd, updateGraphData]);

  /** 请求图数据，并仅允许最后一次请求更新页面。 */
  const requestGraphData = useCallback(async (
    endpoint: string,
    options: { showLoading?: boolean; setErrorState?: boolean } = {},
  ) => {
    const requestGeneration = ++requestGenerationRef.current;
    // 判断请求是否仍拥有页面数据的提交权。
    const isCurrentRequest = () => (
      mountedRef.current && requestGenerationRef.current === requestGeneration
    );
    if (!isCurrentRequest()) return false;
    if (options.showLoading) setGraphState("loading");

    try {
      const data = unwrapApiData(await apiRequest(endpoint));
      if (!isCurrentRequest()) return false;
      const applied = await updateGraphData(
        (data.nodes ?? []) as GraphNode[],
        (data.edges ?? []) as GraphEdgePayload[],
      );
      if (!applied || !isCurrentRequest()) return false;
      if (options.showLoading) setGraphState("ready");
      return true;
    } catch (error) {
      if (!isCurrentRequest()) return false;
      showToast(String(error), true);
      if (options.setErrorState) setGraphState("error");
      return false;
    }
  }, [showToast, updateGraphData]);

  /** 按当前查询与可选记忆 ID 检索图谱。 */
  const searchGraph = useCallback(async () => {
    clearGraphSelection();
    const params = new URLSearchParams();
    if (query) params.set("query", query);
    if (memoryId) params.set("memory_id", memoryId);
    if (!query && !memoryId) params.set("canvas", "1");
    await requestGraphData(`graph/search?${params.toString()}`);
  }, [clearGraphSelection, query, memoryId, requestGraphData]);

  // 挂载时读取统计数据。
  useEffect(() => { fetchOverview(); }, [fetchOverview]);

  // 画布容器挂载后加载首份图数据。
  useEffect(() => {
    if (!containerRef.current) return;
    void requestGraphData("graph/search?canvas=1", {
      showLoading: true,
      setErrorState: true,
    });
  }, [requestGraphData]);

  /** 在宿主支持时切换图谱画布全屏状态。 */
  const toggleFullscreen = useCallback(() => {
    if (!fullscreenRef.current) return;
    if (!document.fullscreenElement) {
      fullscreenRef.current.requestFullscreen().catch(() => {});
      setIsFullscreen(true);
    } else {
      document.exitFullscreen().catch(() => {});
      setIsFullscreen(false);
    }
  }, []);

  useEffect(() => {
    // 保持组件状态与浏览器全屏事件一致。
    const handler = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", handler);
    return () => document.removeEventListener("fullscreenchange", handler);
  }, []);

  return (
    <PageFrame variant="workspace">
      <PageHeader title={t("nav.graph")} icon={<GitGraph size={18} />} />
      <PageContent
        width="full"
        data-workspace-grid="stable"
        className="grid grid-rows-[auto_minmax(320px,1fr)_auto_auto_auto] overflow-hidden p-0 sm:p-0 lg:p-0"
      >

      <GraphStats
        totalMemories={totalMemories}
        nodeCount={nodeCount}
        edgeCount={edgeCount}
        sessionCount={sessionCount}
        t={t}
      />

      {/* G6 画布。 */}
      <div data-slot="graph-canvas" ref={fullscreenRef} className={`relative min-h-[320px] bg-muted/30 ${isFullscreen ? "fixed inset-0 z-50" : ""}`}>
        <div ref={containerRef} className="h-full w-full" />

        {graphState === "loading" && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-muted/80">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              {t("table.loading")}
            </div>
          </div>
        )}
        {graphState === "error" && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-muted/80">
            <div className="text-center">
              <p className="text-sm text-muted-foreground">{t("error.graphSearch")}</p>
              <Button variant="link" size="xs" onClick={() => {
                void requestGraphData("graph/search?canvas=1", {
                  showLoading: true,
                  setErrorState: true,
                });
              }}
                className="mt-2">{t("common.retry")}</Button>
            </div>
          </div>
        )}

        {hoveredNode && (
          <div className="pointer-events-none absolute left-3 top-3 z-10 rounded-lg border bg-popover px-3 py-1.5 text-xs text-popover-foreground shadow-md">
            {hoveredNode.label || hoveredNode.id}
          </div>
        )}

        <div className="absolute bottom-3 right-3 flex items-center gap-2 z-10">
          <Button
            variant="outline"
            size="icon-sm"
            onClick={toggleFullscreen}
            className="bg-background/80"
            aria-label={t(isFullscreen ? "graph.exitFullscreen" : "graph.fullscreen")}
            title={t(isFullscreen ? "graph.exitFullscreen" : "graph.fullscreen")}
          >
            {isFullscreen ? <Minimize2 /> : <Maximize2 />}
          </Button>
          <span className="rounded-md bg-background/80 px-2 py-0.5 text-2xs text-muted-foreground">
            {formatDashboardPercent(scale, locale, { maximumFractionDigits: 0 })}
          </span>
        </div>
      </div>

      {/* 图谱搜索栏。 */}
      <PageToolbar className="flex-nowrap overflow-x-auto border-b-0 border-t bg-background">
        <Input
          placeholder={t("graph.queryPh")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="flex-1 max-w-md"
        />
        <Input
          placeholder={t("graph.memoryIdPh")}
          value={memoryId}
          onChange={(e) => setMemoryId(e.target.value)}
          className="w-32"
        />
        <Button size="sm" onClick={searchGraph}>
          <Search size={14} /> {t("graph.searchBtn")}
        </Button>
        <Button variant="secondary" size="sm" onClick={fetchOverview}>
          <Maximize2 size={14} /> {t("graph.overviewBtn")}
        </Button>
      </PageToolbar>

      {/* 节点与边类型图例。 */}
      <div className="flex flex-nowrap items-center gap-x-4 overflow-x-auto whitespace-nowrap border-t px-6 py-2 text-muted-foreground">
        <span className="mr-1 text-2xs">{t("graph.legendNodes")}</span>
        {Object.entries(GRAPH_NODE_COLORS).map(([type, color]) => (
          <div key={type} className="flex items-center gap-1.5 text-2xs">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
            {t(graphNodeTypeLabel(type))}
          </div>
        ))}
        <span className="mx-2 h-3 w-px bg-border" />
        <span className="mr-1 text-2xs">{t("graph.temporalEdges")}</span>
        {Object.entries(EDGE_STYLES).filter(([type]) => TEMPORAL_EDGES.has(type)).map(([type, style]) => (
          <div key={type} className="flex items-center gap-1.5 text-2xs">
            <svg width="14" height="8" className="shrink-0">
              <line x1="0" y1="4" x2="14" y2="4" stroke={style.color} strokeWidth={1} strokeDasharray="4,3" />
            </svg>
            {t(style.label)}
          </div>
        ))}
        <span className="ml-1 mr-1 text-2xs">{t("graph.causalEdges")}</span>
        {Object.entries(EDGE_STYLES).filter(([type]) => CAUSAL_EDGES.has(type)).map(([type, style]) => (
          <div key={type} className="flex items-center gap-1.5 text-2xs">
            <svg width="14" height="8" className="shrink-0">
              <line x1="0" y1="4" x2="14" y2="4" stroke={style.color} strokeWidth={2} />
            </svg>
            {t(style.label)}
          </div>
        ))}
      </div>

      <GraphTimeRangeFilter
        start={timeRangeStart}
        end={timeRangeEnd}
        t={t}
        onStartChange={setTimeRangeStart}
        onEndChange={setTimeRangeEnd}
        onReset={() => {
          setTimeRangeStart(0);
          setTimeRangeEnd(720);
        }}
      />

      {selectedNode && (
        <GraphNodeDetail
          node={selectedNode}
          locale={locale}
          t={t}
          onClose={() => clearGraphSelection()}
        />
      )}
      </PageContent>
    </PageFrame>
  );
}
