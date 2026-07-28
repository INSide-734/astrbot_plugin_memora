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

/** 后端快照转换后的 G6 渲染结果。 */
export interface GraphRenderData {
  data: GraphElementData;
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
 * 把后端已筛选的节点和边转换为 G6 数据。
 *
 * 时间范围由后端查询保证；此处只移除缺失端点的孤立边并构造渲染字段。
 *
 * @param nodes 图谱节点。
 * @param edges 图谱边。
 * @param causalEdgeTypes 需要显示关系标签的因果边类型。
 * @returns G6 数据和无效边列表。
 */
export function buildGraphRenderData(
  nodes: GraphNode[],
  edges: GraphEdgePayload[],
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
  const graphEdges = edges.flatMap<GraphElementEdge>((edge, index) => {
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
      id: `e-${source}-${target}-${edgeId}`,
      source,
      target,
      data: {
        type,
        weight: edge.weight ?? 1,
        label: causalEdgeTypes.has(type) ? type : undefined,
      },
    }];
  });
  return {
    data: { nodes: graphNodes, edges: graphEdges },
    invalidEdges,
  };
}
