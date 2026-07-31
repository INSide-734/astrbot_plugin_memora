"""面向管理面板画布的轻量全量图快照。"""

from __future__ import annotations

from typing import Any

import aiosqlite

from .base import BaseStore
from .graph_subgraph import GraphSubgraphMixin


class GraphCanvasMixin(BaseStore):
    """提供不携带条目正文与记忆摘要的全量图画布读取能力。"""

    async def get_canvas_snapshot(
        self,
        *,
        session_id: str | None = None,
        persona_id: str | None = None,
        oldest_timestamp: float | None = None,
        newest_timestamp: float | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """返回指定作用域与时间范围内可绘制节点和边的轻量快照。

        Args:
            session_id: 可选会话作用域。
            persona_id: 可选人格作用域。
            oldest_timestamp: 允许显示的最旧 Unix 秒；缺失时不限制。
            newest_timestamp: 允许显示的最新 Unix 秒；缺失时不限制。

        Returns:
            不携带条目正文与记忆摘要的节点、边快照。
        """
        params = {"session_id": session_id, "persona_id": persona_id}

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            node_cursor = await db.execute(
                """
                SELECT gn.id, gn.node_key, gn.node_type, gn.node_value,
                       gn.canonical_value,
                       COUNT(DISTINCT ge.id) AS entry_count,
                       COUNT(DISTINCT ge.source_memory_id) AS memory_count
                FROM graph_entries ge
                JOIN graph_entry_nodes gen ON gen.entry_id = ge.id
                JOIN graph_nodes gn ON gn.id = gen.node_id
                WHERE (:session_id IS NULL OR ge.session_id = :session_id)
                  AND (:persona_id IS NULL OR ge.persona_id = :persona_id)
                GROUP BY gn.id
                """,
                params,
            )
            node_rows = list(await node_cursor.fetchall())
            if not node_rows:
                return {"nodes": [], "edges": []}

            edge_cursor = await db.execute(
                """
                WITH scoped_entries AS (
                    SELECT ge.id, ge.source_memory_id, ge.edge_id, ge.metadata
                    FROM graph_entries ge
                    WHERE (:session_id IS NULL OR ge.session_id = :session_id)
                      AND (:persona_id IS NULL OR ge.persona_id = :persona_id)
                ),
                scoped_nodes AS (
                    SELECT DISTINCT gen.node_id
                    FROM graph_entry_nodes gen
                    JOIN scoped_entries se ON se.id = gen.entry_id
                ),
                edge_entry_ids AS (
                    SELECT edge_id, MAX(id) AS entry_id
                    FROM scoped_entries
                    WHERE edge_id IS NOT NULL
                    GROUP BY edge_id
                ),
                edge_entry_times AS (
                    SELECT se.edge_id, se.metadata
                    FROM scoped_entries se
                    JOIN edge_entry_ids ids ON ids.entry_id = se.id
                )
                SELECT edge.id, edge.source_node_id, edge.target_node_id,
                       edge.relation_type, edge.weight, edge.metadata,
                       edge.created_at, entry_time.metadata AS entry_metadata
                FROM graph_edges edge
                JOIN scoped_nodes source_node ON source_node.node_id = edge.source_node_id
                JOIN scoped_nodes target_node ON target_node.node_id = edge.target_node_id
                LEFT JOIN edge_entry_times entry_time ON entry_time.edge_id = edge.id
                WHERE EXISTS (
                    SELECT 1
                    FROM scoped_entries se
                    WHERE se.source_memory_id = edge.source_memory_id
                )
                ORDER BY edge.id DESC
                """,
                params,
            )
            edge_rows = list(await edge_cursor.fetchall())

        nodes = self._build_canvas_nodes(node_rows)
        edges = self._build_canvas_edges(
            edge_rows,
            nodes,
            oldest_timestamp=oldest_timestamp,
            newest_timestamp=newest_timestamp,
        )
        if oldest_timestamp is not None or newest_timestamp is not None:
            visible_node_ids = {
                int(node_id)
                for edge in edges
                for node_id in (edge["source"], edge["target"])
            }
            nodes = {
                node_id: node
                for node_id, node in nodes.items()
                if node_id in visible_node_ids
            }
        return {
            "nodes": sorted(
                nodes.values(),
                key=lambda item: (
                    -float(item["weight"]),
                    -int(item["entry_count"]),
                    -int(item["degree"]),
                    str(item["label"]),
                ),
            ),
            "edges": edges,
        }

    @staticmethod
    def _build_canvas_nodes(
        node_rows: list[aiosqlite.Row],
    ) -> dict[int, dict[str, Any]]:
        """从聚合查询结果构建节点，并预留边度数与权重字段。"""
        return {
            int(row["id"]): {
                "id": int(row["id"]),
                "key": row["node_key"],
                "type": row["node_type"],
                "label": row["node_value"],
                "canonical_value": row["canonical_value"],
                "entry_count": int(row["entry_count"]),
                "memory_count": int(row["memory_count"]),
                "degree": 0,
                "weight": 0.0,
            }
            for row in node_rows
        }

    def _build_canvas_edges(
        self,
        edge_rows: list[aiosqlite.Row],
        nodes: dict[int, dict[str, Any]],
        *,
        oldest_timestamp: float | None = None,
        newest_timestamp: float | None = None,
    ) -> list[dict[str, Any]]:
        """构建范围内画布边，并同步回填节点度数与展示权重。"""
        edges: list[dict[str, Any]] = []
        for row in edge_rows:
            source = int(row["source_node_id"])
            target = int(row["target_node_id"])
            relation_type = row["relation_type"]
            edge_metadata = self._from_json(row["metadata"])
            entry_metadata = self._from_json(row["entry_metadata"])
            entry_time = GraphSubgraphMixin._metadata_timestamp(entry_metadata)
            timestamp = GraphSubgraphMixin._edge_timestamp(
                self,
                relation_type=relation_type,
                metadata=edge_metadata,
                created_at=row["created_at"],
                entry_time=entry_time,
            )
            if timestamp is not None:
                if oldest_timestamp is not None and timestamp < oldest_timestamp:
                    continue
                if newest_timestamp is not None and timestamp > newest_timestamp:
                    continue
            nodes[source]["degree"] += 1
            nodes[target]["degree"] += 1
            edges.append(
                {
                    "id": int(row["id"]),
                    "source": source,
                    "target": target,
                    "type": relation_type,
                    "weight": float(row["weight"]),
                    "timestamp": timestamp,
                }
            )

        for node in nodes.values():
            node["weight"] = round(
                node["entry_count"]
                + node["memory_count"] * 0.75
                + node["degree"] * 0.35,
                4,
            )
        return edges


__all__ = ["GraphCanvasMixin"]
