import { useState, useEffect, useRef, useCallback } from "react";
import { GitGraph, Search, Maximize2, Minimize2, X } from "lucide-react";
import { Graph, type GraphOptions, type IPointerEvent } from "@antv/g6";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { useI18n } from "@/hooks/useI18n";
import type { Theme } from "@/hooks/useTheme";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { MetricGrid, PageContent, PageFrame, PageHeader, PageToolbar } from "@/components/layout/PageLayout";
import type { GraphNode } from "@/types";
import { dashboardLocale, formatDashboardNumber, formatDashboardPercent, type Translate } from "@/lib/i18n";

interface GraphPageProps {
  showToast: (msg: string, isError?: boolean) => void;
  theme: Theme;
}

const NODE_COLORS: Record<string, string> = {
  topic: "#7950f2", person: "#20c997", fact: "#fcc419", summary: "#f06595", other: "#909296",
};

// Edge type → color + dash pattern + i18n label
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

// Temporal edges (dashed) vs causal edges (solid + labeled)
const TEMPORAL_EDGES = new Set(["before", "after", "during"]);
const CAUSAL_EDGES = new Set(["results_in", "caused_by"]);

const EDGE_DEFAULT = { color: "rgba(148,163,184,0.5)", dash: false, label: "graph.edgeOther" };
function edgeStyle(type: string | undefined) { return EDGE_STYLES[type ?? ""] ?? EDGE_DEFAULT; }

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
          NODE_COLORS[String((datum as any).data?.type ?? "other")] ?? NODE_COLORS.other
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

function graphMotionEnabled(): boolean {
  try {
    return !(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false);
  } catch {
    return true;
  }
}

interface GraphEdgePayload {
  source: string;
  target: string;
  type?: string;
  relation_type?: string;
  weight?: number;
  timestamp?: number | string;
  event_time?: number | string;
  created_at?: string;
}

function graphEdgeType(edge: GraphEdgePayload): string {
  return edge.type ?? edge.relation_type ?? "related";
}

function normalizeUnixSeconds(value: number): number {
  let timestamp = value;
  while (timestamp > 100_000_000_000) {
    timestamp /= 1000;
  }
  return timestamp;
}

function graphEdgeTimestamp(edge: GraphEdgePayload): number {
  // 优先使用后端 _edge_timestamp 计算好的 Unix 秒时间戳
  const ts = edge.timestamp;
  if (ts != null && typeof ts === "number" && Number.isFinite(ts) && ts > 0) {
    return normalizeUnixSeconds(ts);
  }
  // event_time 字段作为回退
  const et = edge.event_time;
  if (et != null) {
    if (typeof et === "number" && Number.isFinite(et) && et > 0) {
      return normalizeUnixSeconds(et);
    }
    if (typeof et === "string" && et.trim()) {
      const n = Number(et);
      if (Number.isFinite(n) && n > 0) return normalizeUnixSeconds(n);
      const p = Date.parse(et);
      if (Number.isFinite(p)) return p / 1000;
    }
  }
  // 最后回退到 created_at ISO 字符串
  const ca = edge.created_at;
  if (ca != null && typeof ca === "string" && ca.trim()) {
    // 将 SQLite 空格分隔格式规范化为 Date.parse 兼容的格式
    const normalized = ca.trim().replace(" ", "T");
    const parsed = Date.parse(normalized);
    if (Number.isFinite(parsed)) return parsed / 1000;
  }
  return 0;
}

function nodeTypeLabel(type: string): string {
  const t = String(type || "other").toLowerCase();
  const labels: Record<string, string> = {
    topic: "graph.nodeTopic", person: "graph.nodePerson",
    fact: "graph.nodeFact", summary: "graph.nodeSummary",
  };
  return labels[t] || "graph.nodeUnknown";
}

/** Format hours to human-readable label */
function formatHours(h: number, t: Translate): string {
  if (h === 0) return t("graph.all");
  if (h < 24) return t("graph.hoursShort", String(h));
  return t("graph.daysShort", String(Math.round(h / 24)));
}

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
  const [timeRangeStart, setTimeRangeStart] = useState(0); // hours ago (0=now)
  const [timeRangeEnd, setTimeRangeEnd] = useState(720);     // hours ago (max)
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

  const clearGraphSelection = useCallback((graph = graphRef.current) => {
    const selectedId = selectedNodeIdRef.current;
    if (graph && selectedId) {
      void graph.setElementState(selectedId, [], false).catch(() => {});
    }
    selectedNodeIdRef.current = null;
    setSelectedNode(null);
  }, []);

  // 创建 G6 实例（不渲染数据，等 updateGraphData 调用）
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

  // 同步图谱数据到 G6 实例
  const updateGraphData = useCallback(async (nodes: GraphNode[], edges: GraphEdgePayload[]) => {
    const g = graphRef.current;
    if (!g || !mountedRef.current) return false;
    const graphGeneration = graphGenerationRef.current;
    const operationGeneration = ++renderGenerationRef.current;
    const isCurrentOperation = () => (
      mountedRef.current
      && graphRef.current === g
      && graphGenerationRef.current === graphGeneration
      && renderGenerationRef.current === operationGeneration
    );

    nodesRef.current = nodes;
    allEdgesRef.current = edges;
    // 缓存最新数据，供时间筛选重放。
    lastDataRef.current = { nodes, edges };

    const now = Date.now() / 1000;
    const nodeIdSet = new Set(nodes.map((n) => String(n.id)));
    const { start: currentTimeRangeStart, end: currentTimeRangeEnd } = timeRangeRef.current;
    const isTimeFilterActive = currentTimeRangeStart > 0 || currentTimeRangeEnd < 720;

    // 第一步：过滤孤立边（source/target 不在节点列表中）
    const connectedEdges = edges.filter((e) => {
      const src = String(e.source);
      const tgt = String(e.target);
      if (!nodeIdSet.has(src) || !nodeIdSet.has(tgt)) {
        console.warn(
          `[GraphPage] 过滤孤立边: ${src} → ${tgt}，节点列表中存在=${nodeIdSet.has(src)}/${nodeIdSet.has(tgt)}`
        );
        return false;
      }
      return true;
    });

    // 第二步：按时间范围过滤边
    // - 无时间戳的边 (ts <= 0)：始终显示，但不参与时间筛选
    // - 有时间戳的边 (ts > 0)：按 cutoffStart/cutoffEnd 筛选
    const cutoffStart = currentTimeRangeStart > 0
      ? now - currentTimeRangeStart * 3600
      : Infinity;
    const cutoffEnd = currentTimeRangeEnd > 0
      ? now - currentTimeRangeEnd * 3600
      : 0;

    const timeFilteredEdges = isTimeFilterActive
      ? connectedEdges.filter((e) => {
          const ts = graphEdgeTimestamp(e);
          if (ts <= 0) return false; // 无时间戳 → 由 timelessEdges 处理
          if (cutoffStart < Infinity && ts > cutoffStart) return false; // 太新
          if (cutoffEnd > 0 && ts < cutoffEnd) return false;            // 太旧
          return true;
        })
      : connectedEdges.filter((e) => graphEdgeTimestamp(e) > 0);

    // 无时间戳的边 — 始终显示
    const timelessEdges = connectedEdges.filter((e) => graphEdgeTimestamp(e) <= 0);

    // 显示的边 = 时间范围内 + 无时间戳
    const visibleEdges = isTimeFilterActive
      ? [...timeFilteredEdges, ...timelessEdges]
      : connectedEdges;

    // 节点可见性：基于所有已连接的边（不只是时间筛选后的），
    // 避免"所有节点消失"的回归问题
    const visibleNodeIdSet = isTimeFilterActive
      ? new Set([
          ...timeFilteredEdges.flatMap((e) => [String(e.source), String(e.target)]),
          ...timelessEdges.flatMap((e) => [String(e.source), String(e.target)]),
        ])
      : nodeIdSet;

    // 统一转为字符串：后端返回的 ID 是 SQLite INTEGER，G6 v5 内部使用字符串 ID 查找
    const g6Nodes = nodes.filter((n) => visibleNodeIdSet.has(String(n.id))).map((n) => ({
      id: String(n.id),
      data: {
        label: n.label ?? String(n.id),
        type: String(n.type ?? "other"),
        weight: n.weight ?? 1,
        memory_count: n.memory_count ?? 0,
        degree: n.degree ?? 0,
        entry_count: n.entry_count ?? 0,
      },
    }));

    const g6Edges = visibleEdges.map((e, i) => {
      const type = graphEdgeType(e);
      const style = edgeStyle(type);
      const isCausal = CAUSAL_EDGES.has(type);
      const sourceStr = String(e.source);
      const targetStr = String(e.target);
      return {
        id: `e-${sourceStr}-${targetStr}-${i}`,
        source: sourceStr,
        target: targetStr,
        data: {
          type,
          weight: e.weight ?? 1,
          label: isCausal ? type : undefined,
        },
      };
    });

    const selectedId = selectedNodeIdRef.current;
    const selectionRemainsVisible = selectedId !== null
      && g6Nodes.some((node) => node.id === selectedId);
    if (selectedId && !selectionRemainsVisible) {
      clearGraphSelection(g);
    }

    try {
      if (!isCurrentOperation()) return false;
      g.setData({ nodes: g6Nodes, edges: g6Edges });
      await g.render();
      if (!isCurrentOperation()) return false;
      if (selectedId && selectionRemainsVisible) {
        await g.setElementState(selectedId, ["selected"], false);
        if (!isCurrentOperation()) return false;
      }
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

  const requestGraphData = useCallback(async (
    endpoint: string,
    options: { showLoading?: boolean; setErrorState?: boolean } = {},
  ) => {
    const requestGeneration = ++requestGenerationRef.current;
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

  const searchGraph = useCallback(async () => {
    clearGraphSelection();
    const params = new URLSearchParams();
    if (query) params.set("query", query);
    if (memoryId) params.set("memory_id", memoryId);
    await requestGraphData(`graph/search?${params.toString()}`);
  }, [clearGraphSelection, query, memoryId, requestGraphData]);

  // Stats on mount
  useEffect(() => { fetchOverview(); }, [fetchOverview]);

  // Initial graph data load — triggered after container mounts
  useEffect(() => {
    if (!containerRef.current) return;
    void requestGraphData("graph/search", {
      showLoading: true,
      setErrorState: true,
    });
  }, [requestGraphData]);

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

      {/* Stats */}
      <div data-slot="graph-stats-scroll" className="w-full overflow-x-auto border-b bg-muted/20">
        <MetricGrid
          minItemWidth="8rem"
          className="min-w-[32rem] gap-0 px-4 sm:px-5 lg:px-6"
          style={{ gridTemplateColumns: "repeat(4, minmax(8rem, 1fr))" }}
        >
          {[
            { label: t("stats.total"), value: totalMemories },
            { label: t("graph.nodes"), value: nodeCount },
            { label: t("graph.edges"), value: edgeCount },
            { label: t("stats.sessions"), value: sessionCount },
          ].map((s) => (
            <div key={s.label} className="border-r px-4 py-2 text-center last:border-r-0">
              <div className="text-lg font-bold tabular-nums text-foreground">{s.value}</div>
              <div className="text-2xs text-muted-foreground">{s.label}</div>
            </div>
          ))}
        </MetricGrid>
      </div>

      {/* G6 canvas */}
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
                void requestGraphData("graph/search", {
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

      {/* Search bar */}
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

      {/* Legend: nodes + edge types */}
      <div className="flex flex-nowrap items-center gap-x-4 overflow-x-auto whitespace-nowrap border-t px-6 py-2 text-muted-foreground">
        <span className="mr-1 text-2xs">{t("graph.legendNodes")}</span>
        {Object.entries(NODE_COLORS).map(([type, color]) => (
          <div key={type} className="flex items-center gap-1.5 text-2xs">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
            {t(nodeTypeLabel(type))}
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

      {/* Dual range time filter */}
      <div className="flex flex-nowrap items-center gap-3 overflow-x-auto whitespace-nowrap border-t px-6 py-2">
        <span className="shrink-0 text-2xs text-muted-foreground">{t("graph.timeRange")}</span>
        <div className="relative flex-1 max-w-[240px] h-6 flex items-center">
          <input
            type="range"
            min="0" max="720" step="1"
            value={timeRangeStart}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (v <= timeRangeEnd) setTimeRangeStart(v);
            }}
            className="absolute inset-x-0 h-1 appearance-none bg-transparent pointer-events-none [&::-webkit-slider-thumb]:pointer-events-auto [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-[var(--color-accent)] [&::-webkit-slider-thumb]:cursor-pointer [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-white z-10"
            style={{ accentColor: "var(--color-accent)" }}
          />
          <input
            type="range"
            min="0" max="720" step="1"
            value={timeRangeEnd}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (v >= timeRangeStart) setTimeRangeEnd(v);
            }}
            className="absolute inset-x-0 h-1 appearance-none bg-transparent pointer-events-none [&::-webkit-slider-thumb]:pointer-events-auto [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-[var(--color-accent-secondary)] [&::-webkit-slider-thumb]:cursor-pointer [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-white"
            style={{ accentColor: "var(--color-accent-secondary)" }}
          />
          <div className="pointer-events-none absolute inset-x-0 h-1 rounded bg-border" />
        </div>
        <Button
          variant="link"
          size="xs"
          onClick={() => { setTimeRangeStart(0); setTimeRangeEnd(720); }}
          className="shrink-0"
        >
          {t("common.reset")}
        </Button>
        <span className="w-28 shrink-0 text-right text-2xs tabular-nums text-muted-foreground">
          {timeRangeStart === 0 && timeRangeEnd >= 720
            ? t("graph.all")
            : `${formatHours(timeRangeStart, t)} – ${formatHours(timeRangeEnd, t)}`}
        </span>
      </div>

      {/* Detail pane */}
      {selectedNode && (
        <div className="fixed inset-y-0 right-0 z-40 w-[380px] overflow-y-auto border-l bg-popover text-popover-foreground shadow-lg animate-slide-in-right">
          <div className="flex items-center justify-between border-b px-5 py-3">
            <div className="flex items-center gap-2">
              <span
                className="h-3 w-3 rounded-full"
                style={{ background: NODE_COLORS[selectedNode.type ?? "other"] }}
              />
              <h3 className="text-sm font-semibold">
                {selectedNode.label || t("graph.unnamedNode")}
              </h3>
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => clearGraphSelection()}
              aria-label={t("common.close")}
              title={t("common.close")}
            >
              <X />
            </Button>
          </div>
          <div className="p-5 space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground">
                  {t("detail.nodeMemories")}
                </label>
                <p className="text-sm font-semibold">{selectedNode.memory_count ?? 0}</p>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">
                  {t("detail.nodeDegree")}
                </label>
                <p className="text-sm font-semibold">{selectedNode.degree ?? 0}</p>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">
                  {t("detail.nodeEntries")}
                </label>
                <p className="text-sm font-semibold">{selectedNode.entry_count ?? 0}</p>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">
                  {t("detail.nodeWeight")}
                </label>
                <p className="text-sm font-semibold">
                  {formatDashboardNumber(selectedNode.weight ?? 0, locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </p>
              </div>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">
                {t("table.type")}
              </label>
              <p className="text-sm">{t(nodeTypeLabel(selectedNode.type ?? "other"))}</p>
            </div>
          </div>
        </div>
      )}
      </PageContent>
    </PageFrame>
  );
}
