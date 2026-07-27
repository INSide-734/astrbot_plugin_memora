import type { EdgeData, GraphData, NodeData } from "@antv/g6";
import type { GraphNode } from "@/types";

/** 图谱接口兼容的边载荷。 */
export interface GraphEdgePayload {
  id?: string | number;
  source: string;
  target: string;
  type?: string;
  relation_type?: string;
  weight?: number;
  timestamp?: number | string;
  event_time?: number | string;
  created_at?: string;
}

interface GraphElementNode extends NodeData {
  id: string;
  data: {
    label: string;
    type: string;
    weight: number;
    memory_count: number;
    degree: number;
    entry_count: number;
  };
}

interface GraphElementEdge extends EdgeData {
  id: string;
  source: string;
  target: string;
  data: {
    type: string;
    weight: number;
    label: string | undefined;
  };
}

interface GraphElementData extends GraphData {
  nodes: GraphElementNode[];
  edges: GraphElementEdge[];
}

interface InvalidGraphEdge {
  source: string;
  target: string;
  sourceExists: boolean;
  targetExists: boolean;
}

/** 供 G6 全量数据和时间筛选增量可见性更新使用的结果。 */
export interface GraphRenderData {
  data: GraphElementData;
  visibleData: GraphElementData;
  visibility: Record<string, "visible" | "hidden">;
  visibleNodeIds: Set<string>;
  hasHiddenElements: boolean;
  invalidEdges: InvalidGraphEdge[];
}

/**
 * 读取兼容图边载荷中的关系类型。
 *
 * @param edge 图谱接口返回的边载荷。
 * @returns 边的关系类型；缺失时返回默认关联类型。
 */
function graphEdgeType(edge: GraphEdgePayload): string {
  return edge.type ?? edge.relation_type ?? "related";
}

/**
 * 把毫秒级时间戳逐级规范化为 Unix 秒。
 *
 * @param value 原始 Unix 时间戳，可能是秒、毫秒或更高精度。
 * @returns 以秒为单位的 Unix 时间戳。
 */
function normalizeUnixSeconds(value: number): number {
  let timestamp = value;
  while (timestamp > 100_000_000_000) {
    timestamp /= 1000;
  }
  return timestamp;
}

/**
 * 按后端时间戳、事件时间和创建时间顺序读取图边时间。
 *
 * @param edge 图谱接口返回的边载荷。
 * @returns 可用于时间筛选的 Unix 秒；缺失或无效时返回 0。
 */
function graphEdgeTimestamp(edge: GraphEdgePayload): number {
  const timestamp = edge.timestamp;
  if (timestamp != null && typeof timestamp === "number" && Number.isFinite(timestamp) && timestamp > 0) {
    return normalizeUnixSeconds(timestamp);
  }
  const eventTime = edge.event_time;
  if (eventTime != null) {
    if (typeof eventTime === "number" && Number.isFinite(eventTime) && eventTime > 0) {
      return normalizeUnixSeconds(eventTime);
    }
    if (typeof eventTime === "string" && eventTime.trim()) {
      const numeric = Number(eventTime);
      if (Number.isFinite(numeric) && numeric > 0) return normalizeUnixSeconds(numeric);
      const parsed = Date.parse(eventTime);
      if (Number.isFinite(parsed)) return parsed / 1000;
    }
  }
  if (typeof edge.created_at === "string" && edge.created_at.trim()) {
    const parsed = Date.parse(edge.created_at.trim().replace(" ", "T"));
    if (Number.isFinite(parsed)) return parsed / 1000;
  }
  return 0;
}

/**
 * 构建完整图数据与当前时间范围对应的可见性映射。
 *
 * 完整数据始终保留所有有效元素，时间范围仅改变 visibility，使 G6 可以避免
 * 在同一数据源重放时重新布局。缺失端点的边被单独返回，交由页面保持既有告警。
 *
 * @param nodes 图谱节点。
 * @param edges 图谱边。
 * @param timeRange 以小时表示的相对时间范围。
 * @param now 当前 Unix 秒。
 * @param causalEdgeTypes 需要显示关系标签的因果边类型。
 * @returns G6 全量数据、可见性映射及兼容回退数据。
 */
export function buildGraphRenderData(
  nodes: GraphNode[],
  edges: GraphEdgePayload[],
  timeRange: { start: number; end: number },
  now: number,
  causalEdgeTypes: ReadonlySet<string>,
): GraphRenderData {
  const nodeIds = new Set(nodes.map((node) => String(node.id)));
  const invalidEdges: InvalidGraphEdge[] = [];
  const graphNodes = nodes.map<GraphElementNode>((node) => ({
    id: String(node.id),
    data: {
      label: node.label ?? String(node.id),
      type: String(node.type ?? "other"),
      weight: node.weight ?? 1,
      memory_count: node.memory_count ?? 0,
      degree: node.degree ?? 0,
      entry_count: node.entry_count ?? 0,
    },
  }));
  const graphEdgesWithTimestamp = edges.flatMap<{
    element: GraphElementEdge;
    timestamp: number;
  }>((edge, index) => {
    const source = String(edge.source);
    const target = String(edge.target);
    const sourceExists = nodeIds.has(source);
    const targetExists = nodeIds.has(target);
    if (!sourceExists || !targetExists) {
      invalidEdges.push({ source, target, sourceExists, targetExists });
      return [];
    }
    const type = graphEdgeType(edge);
    const edgeId = edge.id == null ? String(index) : String(edge.id);
    return [{
      element: {
        id: `e-${source}-${target}-${edgeId}`,
        source,
        target,
        data: {
          type,
          weight: edge.weight ?? 1,
          label: causalEdgeTypes.has(type) ? type : undefined,
        },
      },
      timestamp: graphEdgeTimestamp(edge),
    }];
  });
  const graphEdges = graphEdgesWithTimestamp.map((item) => item.element);
  const timeFilterActive = timeRange.start > 0 || timeRange.end < 720;
  const newestAllowed = timeRange.start > 0
    ? now - timeRange.start * 3600
    : Infinity;
  const oldestAllowed = timeRange.end > 0
    ? now - timeRange.end * 3600
    : 0;
  const visibleEdgeIds = new Set(
    graphEdgesWithTimestamp
      .filter(({ timestamp }) => {
        if (!timeFilterActive) return true;
        if (timestamp <= 0) return true;
        return timestamp <= newestAllowed && timestamp >= oldestAllowed;
      })
      .map(({ element }) => element.id),
  );
  const visibleNodeIds = timeFilterActive
    ? new Set(
        graphEdges
          .filter((edge) => visibleEdgeIds.has(edge.id))
          .flatMap((edge) => [edge.source, edge.target]),
      )
    : nodeIds;
  const visibility = Object.fromEntries([
    ...graphNodes.map((node) => [
      node.id,
      visibleNodeIds.has(node.id) ? "visible" : "hidden",
    ]),
    ...graphEdges.map((edge) => [
      edge.id,
      visibleEdgeIds.has(edge.id) ? "visible" : "hidden",
    ]),
  ]) as Record<string, "visible" | "hidden">;
  const data = { nodes: graphNodes, edges: graphEdges };
  const visibleData = {
    nodes: graphNodes.filter((node) => visibleNodeIds.has(node.id)),
    edges: graphEdges.filter((edge) => visibleEdgeIds.has(edge.id)),
  };
  return {
    data,
    visibleData,
    visibility,
    visibleNodeIds,
    hasHiddenElements: timeFilterActive && (
      visibleNodeIds.size !== graphNodes.length || visibleEdgeIds.size !== graphEdges.length
    ),
    invalidEdges,
  };
}
