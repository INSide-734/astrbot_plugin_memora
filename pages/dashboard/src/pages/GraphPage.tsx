import { useState, useEffect, useRef, useCallback } from "react";
import { GitGraph, Search, Maximize2, Minimize2 } from "lucide-react";
import { Graph, type IPointerEvent } from "@antv/g6";
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
import {
  EDGE_STYLES,
  TEMPORAL_EDGES,
  CAUSAL_EDGES,
  GRAPH_LAYOUT_ALPHA_DECAY,
  graphElementOptions,
  graphMotionEnabled,
} from "@/components/graph/graphAppearance";

interface GraphPageProps {
  showToast: (msg: string, isError?: boolean) => void;
  theme: Theme;
}

interface GraphTimeRange {
  start: number;
  end: number;
  isAll: boolean;
}

const DEFAULT_GRAPH_TIME_RANGE: GraphTimeRange = { start: 0, end: 168, isAll: false };
const ALL_GRAPH_TIME_RANGE: GraphTimeRange = { start: 0, end: 720, isAll: true };

/** 判断草稿时间范围是否与当前画布范围一致。 */
function graphTimeRangesEqual(left: GraphTimeRange, right: GraphTimeRange): boolean {
  return left.start === right.start && left.end === right.end && left.isAll === right.isAll;
}

/** 构建携带当前已应用时间范围的图谱搜索端点。 */
function graphSearchEndpoint(
  options: { query?: string; memoryId?: string; canvas?: boolean },
  timeRange: GraphTimeRange,
): string {
  const params = new URLSearchParams();
  if (options.query) params.set("query", options.query);
  if (options.memoryId) params.set("memory_id", options.memoryId);
  if (options.canvas) params.set("canvas", "1");
  if (!timeRange.isAll) {
    if (timeRange.start > 0) params.set("time_start_hours", String(timeRange.start));
    params.set("time_end_hours", String(timeRange.end));
  }
  return `graph/search?${params.toString()}`;
}

/** 根据当前搜索输入决定请求画布概览或聚焦子图。 */
function currentGraphSearchEndpoint(
  query: string,
  memoryId: string,
  timeRange: GraphTimeRange,
): string {
  const trimmedQuery = query.trim();
  const trimmedMemoryId = memoryId.trim();
  return graphSearchEndpoint(
    {
      query: trimmedQuery || undefined,
      memoryId: trimmedMemoryId || undefined,
      canvas: !trimmedQuery && !trimmedMemoryId,
    },
    timeRange,
  );
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
  const [draftTimeRange, setDraftTimeRange] = useState<GraphTimeRange>(
    DEFAULT_GRAPH_TIME_RANGE,
  );
  const [appliedTimeRange, setAppliedTimeRange] = useState<GraphTimeRange>(
    DEFAULT_GRAPH_TIME_RANGE,
  );
  const [isFullscreen, setIsFullscreen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const fullscreenRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const mountedRef = useRef(false);
  const requestGenerationRef = useRef(0);
  const statsGenerationRef = useRef(0);
  const graphGenerationRef = useRef(0);
  const renderGenerationRef = useRef(0);
  const renderQueueRef = useRef<Promise<boolean> | null>(null);
  const themeOperationGenerationRef = useRef(0);
  const themeRef = useRef(theme);
  const appliedThemeRef = useRef(theme);
  const nodesRef = useRef<GraphNode[]>([]);
  const selectedNodeIdRef = useRef<string | null>(null);
  const [graphState, setGraphState] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestGenerationRef.current += 1;
      statsGenerationRef.current += 1;
      graphGenerationRef.current += 1;
      renderGenerationRef.current += 1;
      themeOperationGenerationRef.current += 1;
    };
  }, []);

  /** 拉取概览统计并忽略已过期请求。 */
  const fetchOverview = useCallback(async () => {
    const generation = ++statsGenerationRef.current;
    try {
      const data = unwrapApiData(await apiRequest("stats"));
      if (!mountedRef.current || statsGenerationRef.current !== generation) return;
      setTotal(Number(data.total_memories ?? data.total_count ?? 0));
      setNodeCount(Number(data.graph_nodes ?? 0));
      setEdgeCount(Number(data.graph_edges ?? 0));
      const sessions = (data.sessions ?? {}) as Record<string, unknown>;
      setSessionCount(Object.keys(sessions).length);
    } catch (e) {
      if (mountedRef.current && statsGenerationRef.current === generation) showToast(String(e), true);
    }
  }, [showToast]);

  themeRef.current = theme;

  // 缓存最新已加载的图谱数据，供画布实例重建。
  const lastDataRef = useRef<{ nodes: GraphNode[]; edges: GraphEdgePayload[] } | null>(null);

  /** 清除画布与详情面板中的当前节点选择。 */
  const clearGraphSelection = useCallback((graph = graphRef.current) => {
    if (graph) {
      for (const node of graph.getElementDataByState("node", "selected")) {
        void graph.setElementState(String(node.id), [], false).catch(() => {});
      }
    }
    selectedNodeIdRef.current = null;
    setSelectedNode(null);
  }, []);

  // 创建 G6 实例（不渲染数据，等 updateGraphData 调用）
  /** 创建 G6 实例并绑定节点、画布与视口事件。 */
  const createGraph = useCallback((container: HTMLDivElement, initialTheme: Theme) => {
    const motionEnabled = graphMotionEnabled();
    // G6 在选择动效结束后才回调，事件需保留开始时的请求代次。
    const clickRequests = new WeakMap<IPointerEvent, number>();
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
          enable: (event: IPointerEvent) => {
            if (event.targetType !== "node" && event.targetType !== "canvas") return false;
            clickRequests.set(event, requestGenerationRef.current);
            return true;
          },
          onClick: (event: IPointerEvent) => {
            if (!mountedRef.current || graphRef.current !== graph) return;
            if (clickRequests.get(event) !== requestGenerationRef.current) return;
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
    renderQueueRef.current = null;
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
   * 把后端已筛选的图数据和选择状态同步到 G6。
   *
   * @param nodes 最新图谱节点。
   * @param edges 最新图谱边。
   * @returns 本次操作是否仍是当前图实例的有效提交。
   */
  const updateGraphData = useCallback((nodes: GraphNode[], edges: GraphEdgePayload[]): Promise<boolean> => {
    const g = graphRef.current;
    if (!g || !mountedRef.current) return Promise.resolve(false);
    const graphGeneration = graphGenerationRef.current;
    const requestGeneration = requestGenerationRef.current;
    const operationGeneration = ++renderGenerationRef.current;
    // 仅允许当前请求与图实例的最后一次渲染提交状态。
    const isCurrentOperation = () => (
      mountedRef.current
      && graphRef.current === g
      && graphGenerationRef.current === graphGeneration
      && requestGenerationRef.current === requestGeneration
      && renderGenerationRef.current === operationGeneration
    );
    const previousRender = renderQueueRef.current;
    const operation = (async () => {
      // 旧布局完成前不能替换同一 G6 model；失败不能阻塞后续重试。
      await previousRender?.catch(() => {});
      if (!isCurrentOperation()) return false;
      nodesRef.current = nodes;
      lastDataRef.current = { nodes, edges };
      const renderData = buildGraphRenderData(nodes, edges, CAUSAL_EDGES);
      for (const invalidEdge of renderData.invalidEdges) {
        console.warn(
          `[GraphPage] 过滤孤立边: ${invalidEdge.source} → ${invalidEdge.target}，节点列表中存在=${invalidEdge.sourceExists}/${invalidEdge.targetExists}`,
        );
      }

      const selectedId = selectedNodeIdRef.current;
      const refreshedSelectedNode = selectedId === null
        ? null : nodes.find((node) => String(node.id) === selectedId) ?? null;
      if (selectedId && !refreshedSelectedNode) clearGraphSelection(g);
      try {
        g.setData(renderData.data);
        await g.render();
        if (!isCurrentOperation()) return false;
        if (selectedId && refreshedSelectedNode) {
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
    })();
    renderQueueRef.current = operation;
    return operation;
  }, [clearGraphSelection]);

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
    clearGraphSelection();
    setHoveredNode(null);
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
  }, [clearGraphSelection, showToast, updateGraphData]);

  /** 按当前查询与可选记忆 ID 检索图谱。 */
  const searchGraph = useCallback(async () => {
    await requestGraphData(
      currentGraphSearchEndpoint(query, memoryId, appliedTimeRange),
      { showLoading: true, setErrorState: true },
    );
  }, [appliedTimeRange, query, memoryId, requestGraphData]);

  /** 恢复最近七天的管理员总览，同时刷新统计。 */
  const loadRecentOverview = useCallback(async () => {
    setQuery("");
    setMemoryId("");
    setDraftTimeRange(DEFAULT_GRAPH_TIME_RANGE);
    setAppliedTimeRange(DEFAULT_GRAPH_TIME_RANGE);
    void fetchOverview();
    await requestGraphData(
      graphSearchEndpoint({ canvas: true }, DEFAULT_GRAPH_TIME_RANGE),
      { showLoading: true, setErrorState: true },
    );
  }, [fetchOverview, requestGraphData]);

  /** 应用时间范围草稿，并按当前搜索输入重新请求图谱。 */
  const applyTimeRange = useCallback(async () => {
    const nextRange = { ...draftTimeRange, isAll: false };
    setAppliedTimeRange(nextRange);
    await requestGraphData(
      currentGraphSearchEndpoint(query, memoryId, nextRange),
      { showLoading: true, setErrorState: true },
    );
  }, [draftTimeRange, memoryId, query, requestGraphData]);

  /** 切换到全部时间并立即按当前搜索输入重新请求图谱。 */
  const resetTimeRange = useCallback(async () => {
    const nextRange = { ...ALL_GRAPH_TIME_RANGE };
    setDraftTimeRange(nextRange);
    setAppliedTimeRange(nextRange);
    await requestGraphData(
      currentGraphSearchEndpoint(query, memoryId, nextRange),
      { showLoading: true, setErrorState: true },
    );
  }, [memoryId, query, requestGraphData]);

  useEffect(() => { void loadRecentOverview(); }, [loadRecentOverview]);



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

  const timeRangeDirty = !graphTimeRangesEqual(draftTimeRange, appliedTimeRange);

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

        {graphState === "ready" && !lastDataRef.current?.nodes.length && (
          <div role="status" className="pointer-events-none absolute inset-0 flex items-center justify-center px-4 text-center text-sm text-muted-foreground">
            {t("graph.canvasDefault")}
          </div>
        )}

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
              <Button
                variant="link"
                size="xs"
                onClick={() => { void searchGraph(); }}
                className="mt-2"
              >
                {t("common.retry")}
              </Button>
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
          aria-label={t("graph.queryPh")}
          placeholder={t("graph.queryPh")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) void searchGraph();
          }}
          className="min-w-48 flex-1 max-w-md"
        />
        <Input
          aria-label={t("graph.memoryIdPh")}
          placeholder={t("graph.memoryIdPh")}
          value={memoryId}
          onChange={(e) => setMemoryId(e.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) void searchGraph();
          }}
          className="w-40 shrink-0"
        />
        <Button size="sm" onClick={searchGraph}>
          <Search size={14} /> {t("graph.searchBtn")}
        </Button>
        <Button variant="secondary" size="sm" onClick={loadRecentOverview}>
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
        start={draftTimeRange.start}
        end={draftTimeRange.end}
        isAll={draftTimeRange.isAll}
        isDirty={timeRangeDirty}
        disabled={graphState === "loading"}
        t={t}
        onStartChange={(start) => setDraftTimeRange((current) => ({
          ...current,
          start,
          isAll: false,
        }))}
        onEndChange={(end) => setDraftTimeRange((current) => ({
          ...current,
          end,
          isAll: false,
        }))}
        onApply={() => { void applyTimeRange(); }}
        onReset={() => { void resetTimeRange(); }}
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
