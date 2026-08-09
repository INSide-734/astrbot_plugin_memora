"""图查询操作 — 搜索、遍历与子图检索。"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from ...infrastructure.base import BaseStore


class GraphQueryMixin(BaseStore):
    """GraphStore 的查询与搜索方法。"""

    async def search_entries_by_bm25(
        self,
        fts_query: str,
        limit: int,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """通过 FTS 表搜索图条目。"""
        params = {
            "fts_query": fts_query,
            "session_id": session_id,
            "persona_id": persona_id,
            "limit": limit,
        }

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT ge.id, ge.source_memory_id, ge.content, ge.metadata,
                       ge.entry_type, ge.relation_type, ge.session_id, ge.persona_id,
                       bm25(memora_graph_entries_fts) AS score
                FROM memora_graph_entries_fts
                JOIN graph_entries ge ON ge.id = memora_graph_entries_fts.entry_id
                WHERE memora_graph_entries_fts MATCH :fts_query
                  AND (:session_id IS NULL OR ge.session_id = :session_id)
                  AND (:persona_id IS NULL OR ge.persona_id = :persona_id)
                ORDER BY score ASC
                LIMIT :limit
                """,
                params,
            )
            rows = await cursor.fetchall()

        if not rows:
            return []

        scores = [float(row["score"]) for row in rows]
        max_score = max(scores)
        min_score = min(scores)
        score_range = max_score - min_score
        hits: list[dict[str, Any]] = []
        for row in rows:
            normalized = (
                1.0
                if score_range == 0
                else (max_score - float(row["score"])) / score_range
            )
            metadata = self._from_json(row["metadata"])
            hits.append(
                {
                    "entry_id": int(row["id"]),
                    "source_memory_id": int(row["source_memory_id"]),
                    "content": row["content"],
                    "metadata": metadata,
                    "entry_type": row["entry_type"],
                    "relation_type": row["relation_type"],
                    "score": normalized,
                }
            )
        return hits

    async def search_nodes_by_tokens(
        self, tokens: list[str], limit: int = 20
    ) -> list[dict[str, Any]]:
        """查找规范值中包含查询 token 的图节点。"""
        if not tokens:
            return []
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT id, node_key, node_type, node_value, canonical_value, metadata
                FROM graph_nodes
                WHERE EXISTS (
                    SELECT 1 FROM json_each(:patterns_json) AS pattern
                    WHERE canonical_value LIKE pattern.value
                )
                ORDER BY LENGTH(canonical_value) ASC
                LIMIT :limit
                """,
                {
                    "patterns_json": json.dumps([f"%{token}%" for token in tokens]),
                    "limit": limit,
                },
            )
            rows = await cursor.fetchall()

        return [
            {
                "id": int(row["id"]),
                "node_key": row["node_key"],
                "node_type": row["node_type"],
                "node_value": row["node_value"],
                "canonical_value": row["canonical_value"],
                "metadata": self._from_json(row["metadata"]),
            }
            for row in rows
        ]

    async def get_entries_for_node_ids(
        self,
        node_ids: list[int],
        limit: int,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """从匹配节点展开一跳到其关联的条目。"""
        if not node_ids:
            return []

        params = {
            "node_ids_json": json.dumps(node_ids),
            "session_id": session_id,
            "persona_id": persona_id,
            "limit": limit,
        }

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT ge.id, ge.source_memory_id, ge.content, ge.metadata,
                       ge.entry_type, ge.relation_type, COUNT(DISTINCT gen.node_id) AS hit_count
                FROM graph_entry_nodes gen
                JOIN graph_entries ge ON ge.id = gen.entry_id
                WHERE gen.node_id IN (
                    SELECT value FROM json_each(:node_ids_json)
                )
                  AND (:session_id IS NULL OR ge.session_id = :session_id)
                  AND (:persona_id IS NULL OR ge.persona_id = :persona_id)
                GROUP BY ge.id
                ORDER BY hit_count DESC, ge.id DESC
                LIMIT :limit
                """,
                params,
            )
            rows = await cursor.fetchall()

        hits: list[dict[str, Any]] = []
        for row in rows:
            metadata = self._from_json(row["metadata"])
            hits.append(
                {
                    "entry_id": int(row["id"]),
                    "source_memory_id": int(row["source_memory_id"]),
                    "content": row["content"],
                    "metadata": metadata,
                    "entry_type": row["entry_type"],
                    "relation_type": row["relation_type"],
                    "score": min(1.0, 0.35 + 0.15 * int(row["hit_count"])),
                    "hit_count": int(row["hit_count"]),
                }
            )
        return hits

    async def get_neighbor_node_ids(
        self,
        node_ids: list[int],
        limit: int,
    ) -> list[int]:
        """返回通过活跃边与给定节点相邻的图节点。"""
        if not node_ids:
            return []

        normalized_ids = sorted({int(item) for item in node_ids})
        limit = max(1, min(limit, 500))

        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT neighbor_id, SUM(edge_weight) AS total_weight
                FROM (
                    SELECT target_node_id AS neighbor_id, weight AS edge_weight
                    FROM graph_edges
                    WHERE source_node_id IN (
                        SELECT value FROM json_each(:node_ids_json)
                    )
                      AND status = 'active'
                    UNION ALL
                    SELECT source_node_id AS neighbor_id, weight AS edge_weight
                    FROM graph_edges
                    WHERE target_node_id IN (
                        SELECT value FROM json_each(:node_ids_json)
                    )
                      AND status = 'active'
                )
                WHERE neighbor_id NOT IN (
                    SELECT value FROM json_each(:node_ids_json)
                )
                GROUP BY neighbor_id
                ORDER BY total_weight DESC, neighbor_id ASC
                LIMIT :limit
                """,
                {"node_ids_json": json.dumps(normalized_ids), "limit": limit},
            )
            rows = await cursor.fetchall()

        return [int(row[0]) for row in rows]

    async def get_recent_memory_ids(
        self,
        limit: int = 12,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[int]:
        """返回图中最近更新的记忆标识符。"""
        limit = max(1, min(limit, 200))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT source_memory_id, MAX(id) AS latest_entry_id
                FROM graph_entries
                WHERE (:session_id IS NULL OR session_id = :session_id)
                  AND (:persona_id IS NULL OR persona_id = :persona_id)
                GROUP BY source_memory_id
                ORDER BY latest_entry_id DESC
                LIMIT :limit
                """,
                {
                    "session_id": session_id,
                    "persona_id": persona_id,
                    "limit": limit,
                },
            )
            rows = await cursor.fetchall()

        return [int(row["source_memory_id"]) for row in rows]
