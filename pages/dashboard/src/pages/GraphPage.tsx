import { useState, useEffect, useRef, useCallback } from "react";
import { GitGraph, Search, Maximize2, Minimize2, X } from "lucide-react";
import { Graph } from "@antv/g6";
import { apiRequest, unwrapApiData } from "@/lib/bridge";
import { useI18n } from "@/hooks/useI18n";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import type { GraphNode } from "@/types";

interface GraphPageProps {
  showToast: (msg: string, isError?: boolean) => void;
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
function formatHours(h: number): string {
  if (h === 0) return "全部";
  if (h < 24) return `${h}h`;
  return `${Math.round(h / 24)}d`;
}

export function GraphPage({ showToast }: GraphPageProps) {
  const { t } = useI18n();
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
  const nodesRef = useRef<GraphNode[]>([]);
  const allEdgesRef = useRef<GraphEdgePayload[]>([]);
  const [graphState, setGraphState] = useState<"loading" | "ready" | "error">("loading");

  const fetchOverview = useCallback(async () => {
    try {
      const data = unwrapApiData(await apiRequest("stats"));
      setTotal(Number(data.total_memories ?? data.total_count ?? 0));
      setNodeCount(Number(data.graph_nodes ?? 0));
      setEdgeCount(Number(data.graph_edges ?? 0));
      const sessions = (data.sessions ?? {}) as Record<string, unknown>;
      setSessionCount(Object.keys(sessions).length);
    } catch (e) { showToast(String(e), true); }
  }, [showToast]);

  // 追踪 DOM 上的实际主题（只读，不写入）
  const [domTheme, setDomTheme] = useState<"light" | "dark">(() =>
    document.documentElement.dataset.theme === "dark" ? "dark" : "light"
  );
  // 缓存最新已加载的图谱数据，主题切换时重放
  const lastDataRef = useRef<{ nodes: GraphNode[]; edges: GraphEdgePayload[] } | null>(null);

  // 跟随 App.tsx 中的 useTheme 所管理的 data-theme 属性变化
  useEffect(() => {
    const observer = new MutationObserver(() => {
      const next = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
      setDomTheme((prev) => (prev !== next ? next : prev));
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  // 创建 G6 实例（不渲染数据，等 updateGraphData 调用）
  const createGraph = useCallback((container: HTMLDivElement, isDark: boolean) => {
    const nodeLabelFill = isDark ? "#e8eaed" : "#1e1e1e";
    const edgeLabelFill = isDark ? "#94a3b8" : "#64748b";

    const graph = new Graph({
      container,
      autoFit: "view",
      animation: true,
      node: {
        type: "circle",
        style: {
          size: 24,
          fill: (d: Record<string, unknown>) => NODE_COLORS[String((d as any).data?.type ?? "other")] ?? NODE_COLORS.other,
          fillOpacity: 0.85,
          stroke: "transparent",
          labelText: (d: Record<string, unknown>) => String((d as any).data?.label ?? d.id ?? ""),
          labelFontSize: 10,
          labelFill: nodeLabelFill,
          labelOffsetY: 12,
          labelPlacement: "bottom",
        },
        state: {
          hover: { stroke: "#ffffff88", lineWidth: 3 },
          selected: { stroke: "#4dabf7", lineWidth: 3 },
        },
      },
      edge: {
        type: "line",
        style: {
          stroke: (d: Record<string, unknown>) => {
            const t = String((d as any)?.data?.type ?? "");
            return edgeStyle(t).color;
          },
          lineWidth: (d: Record<string, unknown>) => {
            const t = String((d as any)?.data?.type ?? "");
            return CAUSAL_EDGES.has(t) ? 2 : 0.8;
          },
          lineDash: (d: Record<string, unknown>) => {
            const t = String((d as any)?.data?.type ?? "");
            return edgeStyle(t).dash ? [6, 3] : undefined;
          },
          labelText: (d: Record<string, unknown>) => {
            const data = (d as any)?.data;
            return data?.label ?? undefined;
          },
          labelFontSize: 9,
          labelFill: edgeLabelFill,
          labelOffsetY: -6,
        },
      },
      layout: {
        type: "d3-force",
        preventOverlap: true,
        nodeStrength: -200,
        linkDistance: 120,
        animation: true,
      },
      behaviors: [
        "drag-canvas",
        "zoom-canvas",
        "drag-element",
        { type: "hover-activate", degree: 1, direction: "both" },
      ],
      data: { nodes: [], edges: [] },
    });

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    graph.on("node:click", (evt: any) => {
      const id = evt.target?.id as string | undefined;
      if (id) {
        graph.focusElement(id, { duration: 500 });
        const node = nodesRef.current.find((n) => String(n.id) === id);
        if (node) setSelectedNode(node);
      }
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

    graph.on("canvas:click", () => setSelectedNode(null));
    graph.on("viewport:change", () => setScale(graph.getZoom()));

    return graph;
  }, []);

  // 当容器就绪或主题变化时，销毁旧图并重建
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    // 清空旧容器（G6 在 destroy 后可能残留 canvas）
    el.innerHTML = "";

    const isDark = domTheme === "dark";
    const graph = createGraph(el, isDark);
    graphRef.current = graph;

    // 如果已有缓存数据，立即渲染
    const cached = lastDataRef.current;
    if (cached) {
      updateGraphData(cached.nodes, cached.edges);
    }

    return () => {
      graph.destroy();
      graphRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domTheme]);

  // 同步图谱数据到 G6 实例
  const updateGraphData = useCallback(async (nodes: GraphNode[], edges: GraphEdgePayload[]) => {
    const g = graphRef.current;
    if (!g) return;

    nodesRef.current = nodes;
    allEdgesRef.current = edges;
    // 缓存最新数据，供主题切换重建时重放
    lastDataRef.current = { nodes, edges };

    const now = Date.now() / 1000;
    const nodeIdSet = new Set(nodes.map((n) => String(n.id)));
    const isTimeFilterActive = timeRangeStart > 0 || timeRangeEnd < 720;

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
    const cutoffStart = timeRangeStart > 0 ? now - timeRangeStart * 3600 : Infinity;
    const cutoffEnd = timeRangeEnd > 0 ? now - timeRangeEnd * 3600 : 0;

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

    try {
      g.setData({ nodes: g6Nodes, edges: g6Edges });
      await g.render();
    } catch (err) {
      console.error("[GraphPage] G6 render 失败:", err);
      // 不抛出，让错误状态 UI 接管
    }
  }, [timeRangeStart, timeRangeEnd]);

  useEffect(() => {
    const cached = lastDataRef.current;
    if (!cached || !graphRef.current) return;
    void updateGraphData(cached.nodes, cached.edges);
  }, [timeRangeStart, timeRangeEnd, updateGraphData]);

  const searchGraph = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (query) params.set("query", query);
      if (memoryId) params.set("memory_id", memoryId);
      const data = unwrapApiData(await apiRequest(`graph/search?${params.toString()}`));
      const newNodes = (data.nodes ?? []) as GraphNode[];
      const newEdges = (data.edges ?? []) as GraphEdgePayload[];
      setSelectedNode(null);
      await updateGraphData(newNodes, newEdges);
    } catch (e) { showToast(String(e), true); }
  }, [query, memoryId, showToast, updateGraphData]);

  // Stats on mount
  useEffect(() => { fetchOverview(); }, [fetchOverview]);

  // Initial graph data load — triggered after container mounts
  useEffect(() => {
    if (!containerRef.current) return;
    const load = async () => {
      try {
        setGraphState("loading");
        const data = unwrapApiData(await apiRequest("graph/search"));
        await updateGraphData(
          (data.nodes ?? []) as GraphNode[],
          (data.edges ?? []) as GraphEdgePayload[]
        );
        setGraphState("ready");
      } catch (e) {
        showToast(String(e), true);
        setGraphState("error");
      }
    };
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-3">
        <h1 className="flex items-center gap-2 text-sm font-semibold text-[var(--text-primary)]">
          <GitGraph size={18} /> {t("nav.graph")}
        </h1>
      </header>

      {/* Stats */}
      <div className="flex gap-4 border-b border-[var(--color-border-light)] px-6 py-3">
        {[
          { label: t("stats.total"), value: totalMemories },
          { label: t("graph.nodes"), value: nodeCount },
          { label: t("graph.edges"), value: edgeCount },
          { label: t("stats.sessions"), value: sessionCount },
        ].map((s) => (
          <div key={s.label} className="text-center">
            <div className="text-lg font-bold tabular-nums text-[var(--text-primary)]">{s.value}</div>
            <div className="text-2xs text-[var(--text-tertiary)]">{s.label}</div>
          </div>
        ))}
      </div>

      {/* G6 canvas */}
      <div ref={fullscreenRef} className={`relative flex-1 bg-[var(--color-surface-secondary)] ${isFullscreen ? "fixed inset-0 z-50" : ""}`}>
        <div ref={containerRef} className="h-full w-full" />

        {graphState === "loading" && (
          <div className="absolute inset-0 flex items-center justify-center bg-[var(--color-surface-secondary)]/80 z-10">
            <div className="flex items-center gap-2 text-sm text-[var(--text-tertiary)]">
              <div className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-accent)] border-t-transparent" />
              {t("table.loading")}
            </div>
          </div>
        )}
        {graphState === "error" && (
          <div className="absolute inset-0 flex items-center justify-center bg-[var(--color-surface-secondary)]/80 z-10">
            <div className="text-center">
              <p className="text-sm text-[var(--text-tertiary)]">{t("error.graphSearch")}</p>
              <button onClick={() => {
                setGraphState("loading");
                // 重试：重新加载图谱数据
                const retry = async () => {
                  try {
                    const data = unwrapApiData(await apiRequest("graph/search"));
                    await updateGraphData(
                      (data.nodes ?? []) as GraphNode[],
                      (data.edges ?? []) as GraphEdgePayload[]
                    );
                    setGraphState("ready");
                  } catch (e2) {
                    showToast(String(e2), true);
                    setGraphState("error");
                  }
                };
                retry();
              }}
                className="mt-2 text-xs text-[var(--color-accent)] hover:underline">{t("common.retry")}</button>
            </div>
          </div>
        )}

        {hoveredNode && (
          <div className="pointer-events-none absolute left-3 top-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)] px-3 py-1.5 text-xs shadow-elevated z-10">
            {hoveredNode.label || hoveredNode.id}
          </div>
        )}

        <div className="absolute bottom-3 right-3 flex items-center gap-2 z-10">
          <button
            onClick={toggleFullscreen}
            className="rounded-md bg-[var(--color-surface-elevated)]/80 p-1.5 text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--color-surface-elevated)]"
            title={isFullscreen ? "退出全屏" : "全屏"}
          >
            {isFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
          <span className="rounded-md bg-[var(--color-surface-elevated)]/80 px-2 py-0.5 text-2xs text-[var(--text-tertiary)]">
            {Math.round(scale * 100)}%
          </span>
        </div>
      </div>

      {/* Search bar */}
      <div className="flex items-center gap-3 border-t border-[var(--color-border)] px-6 py-2.5">
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
      </div>

      {/* Legend: nodes + edge types */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-[var(--color-border-light)] px-6 py-2">
        <span className="text-2xs text-[var(--text-tertiary)] mr-1">{t("graph.legendNodes")}</span>
        {Object.entries(NODE_COLORS).map(([type, color]) => (
          <div key={type} className="flex items-center gap-1.5 text-2xs text-[var(--text-tertiary)]">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
            {t(nodeTypeLabel(type))}
          </div>
        ))}
        <span className="mx-2 h-3 w-px bg-[var(--color-border)]" />
        <span className="text-2xs text-[var(--text-tertiary)] mr-1">时序边:</span>
        {Object.entries(EDGE_STYLES).filter(([type]) => TEMPORAL_EDGES.has(type)).map(([type, style]) => (
          <div key={type} className="flex items-center gap-1.5 text-2xs text-[var(--text-tertiary)]">
            <svg width="14" height="8" className="shrink-0">
              <line x1="0" y1="4" x2="14" y2="4" stroke={style.color} strokeWidth={1} strokeDasharray="4,3" />
            </svg>
            {t(style.label)}
          </div>
        ))}
        <span className="text-2xs text-[var(--text-tertiary)] mr-1 ml-1">因果边:</span>
        {Object.entries(EDGE_STYLES).filter(([type]) => CAUSAL_EDGES.has(type)).map(([type, style]) => (
          <div key={type} className="flex items-center gap-1.5 text-2xs text-[var(--text-tertiary)]">
            <svg width="14" height="8" className="shrink-0">
              <line x1="0" y1="4" x2="14" y2="4" stroke={style.color} strokeWidth={2} />
            </svg>
            {t(style.label)}
          </div>
        ))}
      </div>

      {/* Dual range time filter */}
      <div className="flex items-center gap-3 border-t border-[var(--color-border-light)] px-6 py-2">
        <span className="text-2xs text-[var(--text-tertiary)] shrink-0">时间范围:</span>
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
          <div className="absolute inset-x-0 h-1 rounded bg-[var(--color-border)] pointer-events-none" />
        </div>
        <button
          onClick={() => { setTimeRangeStart(0); setTimeRangeEnd(720); }}
          className="text-2xs text-[var(--color-accent)] hover:underline shrink-0"
        >
          重置
        </button>
        <span className="text-2xs text-[var(--text-tertiary)] shrink-0 w-28 text-right tabular-nums">
          {timeRangeStart === 0 && timeRangeEnd >= 720 ? "全部" : `${formatHours(timeRangeStart)} – ${formatHours(timeRangeEnd)}`}
        </span>
      </div>

      {/* Detail pane */}
      {selectedNode && (
        <div className="fixed inset-y-0 right-0 z-40 w-[380px] overflow-y-auto border-l border-[var(--color-border)] bg-[var(--color-surface-elevated)] shadow-modal animate-slide-in-right">
          <div className="flex items-center justify-between border-b border-[var(--color-border)] px-5 py-3">
            <div className="flex items-center gap-2">
              <span
                className="h-3 w-3 rounded-full"
                style={{ background: NODE_COLORS[selectedNode.type ?? "other"] }}
              />
              <h3 className="text-sm font-semibold">
                {selectedNode.label || t("graph.unnamedNode")}
              </h3>
            </div>
            <button
              onClick={() => setSelectedNode(null)}
              className="rounded-lg p-1 text-[var(--text-tertiary)] hover:bg-[var(--color-surface-secondary)]"
            >
              <X size={16} />
            </button>
          </div>
          <div className="p-5 space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-[var(--text-tertiary)]">
                  {t("detail.nodeMemories")}
                </label>
                <p className="text-sm font-semibold">{selectedNode.memory_count ?? 0}</p>
              </div>
              <div>
                <label className="text-xs font-medium text-[var(--text-tertiary)]">
                  {t("detail.nodeDegree")}
                </label>
                <p className="text-sm font-semibold">{selectedNode.degree ?? 0}</p>
              </div>
              <div>
                <label className="text-xs font-medium text-[var(--text-tertiary)]">
                  {t("detail.nodeEntries")}
                </label>
                <p className="text-sm font-semibold">{selectedNode.entry_count ?? 0}</p>
              </div>
              <div>
                <label className="text-xs font-medium text-[var(--text-tertiary)]">
                  {t("detail.nodeWeight")}
                </label>
                <p className="text-sm font-semibold">
                  {Number(selectedNode.weight ?? 0).toFixed(2)}
                </p>
              </div>
            </div>
            <div>
              <label className="text-xs font-medium text-[var(--text-tertiary)]">
                {t("table.type")}
              </label>
              <p className="text-sm">{t(nodeTypeLabel(selectedNode.type ?? "other"))}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
