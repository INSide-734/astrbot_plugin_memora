"""图查询操作 — 搜索、遍历与子图检索。"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from ...infrastructure.base import BaseStore
from ..domain.models import (
    GraphBoundary,
    GraphQueryScope,
    resolve_graph_query_scope,
)


class GraphQueryMixin(BaseStore):
    """GraphStore 的查询与搜索方法。"""

    @staticmethod
    def _query_params(
        *,
        boundary: GraphBoundary | None,
        query_scope: GraphQueryScope | None,
        **extra: Any,
    ) -> dict[str, Any]:
        """构造请求 scope 参数，并把 source revision 保留为可选精确过滤。"""
        scope, revision_token = resolve_graph_query_scope(
            boundary=boundary,
            query_scope=query_scope,
        )
        return {
            **scope.as_params(),
            "revision_token": revision_token,
            **extra,
        }

    @staticmethod
    def _row_boundary(row: aiosqlite.Row) -> GraphBoundary | None:
        """从派生行读取 source boundary；缺字段的 legacy 行不可作为证据。"""
        try:
            return GraphBoundary(
                row["scope_key"],
                row["privacy_level"],
                row["revision_token"],
            )
        except (IndexError, KeyError, TypeError, ValueError):
            return None

    async def search_entries_by_bm25(
        self,
        fts_query: str,
        limit: int,
        session_id: str | None = None,
        persona_id: str | None = None,
        *,
        boundary: GraphBoundary | None = None,
        query_scope: GraphQueryScope | None = None,
    ) -> list[dict[str, Any]]:
        """通过 FTS 表搜索指定请求 scope 内的图条目。"""
        params = self._query_params(
            boundary=boundary,
            query_scope=query_scope,
            fts_query=fts_query,
            session_id=session_id,
            persona_id=persona_id,
            limit=limit,
        )

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT ge.id, ge.source_memory_id, ge.content, ge.metadata,
                       ge.entry_type, ge.relation_type, ge.session_id, ge.persona_id,
                       ge.scope_key, ge.privacy_level, ge.revision_token,
                       bm25(memora_graph_entries_fts) AS score
                FROM memora_graph_entries_fts
                JOIN graph_entries ge ON ge.id = memora_graph_entries_fts.entry_id
                WHERE memora_graph_entries_fts MATCH :fts_query
                  AND (:session_id IS NULL OR ge.session_id = :session_id)
                  AND (:persona_id IS NULL OR ge.persona_id = :persona_id)
                  AND ge.scope_key = :scope_key AND ge.privacy_level = :privacy_level
                  AND ge.scope_key IS NOT NULL AND ge.privacy_level IS NOT NULL
                  AND ge.revision_token IS NOT NULL
                  AND (
                    (ge.edge_id IS NULL AND ge.entry_type <> 'edge')
                    OR EXISTS (
                        SELECT 1
                        FROM graph_edges evidence
                        JOIN graph_semantic_edges semantic
                          ON semantic.id = evidence.semantic_edge_id
                         AND semantic.scope_key = evidence.scope_key
                         AND semantic.privacy_level = evidence.privacy_level
                         AND semantic.status = 'active'
                        WHERE evidence.id = ge.edge_id
                          AND evidence.status = 'active'
                          AND evidence.semantic_edge_id IS NOT NULL
                          AND evidence.source_memory_id = ge.source_memory_id
                          AND evidence.scope_key = ge.scope_key
                          AND evidence.privacy_level = ge.privacy_level
                          AND evidence.revision_token = ge.revision_token
                    )
                  )
                  AND (:revision_token IS NULL OR ge.revision_token = :revision_token)
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
                    "source_boundary": self._row_boundary(row),
                }
            )
        return hits

    async def search_nodes_by_tokens(
        self,
        tokens: list[str],
        *,
        boundary: GraphBoundary | None = None,
        query_scope: GraphQueryScope | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """查找请求 scope 内规范值中包含查询 token 的图节点。"""
        params = self._query_params(
            boundary=boundary,
            query_scope=query_scope,
            patterns_json=json.dumps([f"%{token}%" for token in tokens]),
            limit=limit,
        )
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
                  AND scope_key = :scope_key AND privacy_level = :privacy_level
                  AND scope_key IS NOT NULL AND privacy_level IS NOT NULL
                  AND revision_token IS NOT NULL
                  AND (:revision_token IS NULL OR revision_token = :revision_token)
                ORDER BY LENGTH(canonical_value) ASC
                LIMIT :limit
                """,
                params,
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
        *,
        boundary: GraphBoundary | None = None,
        query_scope: GraphQueryScope | None = None,
    ) -> list[dict[str, Any]]:
        """从请求 scope 内匹配节点展开一跳到其关联条目。"""
        if not node_ids:
            return []
        params = self._query_params(
            boundary=boundary,
            query_scope=query_scope,
            node_ids_json=json.dumps(node_ids),
            session_id=session_id,
            persona_id=persona_id,
            limit=limit,
        )

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT ge.id, ge.source_memory_id, ge.content, ge.metadata,
                       ge.entry_type, ge.relation_type,
                       ge.scope_key, ge.privacy_level, ge.revision_token,
                       COUNT(DISTINCT gen.node_id) AS hit_count
                FROM graph_entry_nodes gen
                JOIN graph_entries ge ON ge.id = gen.entry_id
                JOIN graph_nodes gn ON gn.id = gen.node_id
                WHERE (
                    (:revision_token IS NOT NULL AND gen.node_id IN (
                        SELECT value FROM json_each(:node_ids_json)
                    ))
                    OR (:revision_token IS NULL AND gn.node_key IN (
                        SELECT seed.node_key
                        FROM graph_nodes seed
                        WHERE seed.id IN (
                            SELECT value FROM json_each(:node_ids_json)
                        )
                          AND seed.scope_key = :scope_key
                          AND seed.privacy_level = :privacy_level
                    ))
                )
                  AND (:session_id IS NULL OR ge.session_id = :session_id)
                  AND (:persona_id IS NULL OR ge.persona_id = :persona_id)
                  AND ge.scope_key = :scope_key AND ge.privacy_level = :privacy_level
                  AND ge.scope_key IS NOT NULL AND ge.privacy_level IS NOT NULL
                  AND ge.revision_token IS NOT NULL
                  AND (
                    (ge.edge_id IS NULL AND ge.entry_type <> 'edge')
                    OR EXISTS (
                        SELECT 1
                        FROM graph_edges evidence
                        JOIN graph_semantic_edges semantic
                          ON semantic.id = evidence.semantic_edge_id
                         AND semantic.scope_key = evidence.scope_key
                         AND semantic.privacy_level = evidence.privacy_level
                         AND semantic.status = 'active'
                        WHERE evidence.id = ge.edge_id
                          AND evidence.status = 'active'
                          AND evidence.semantic_edge_id IS NOT NULL
                          AND evidence.source_memory_id = ge.source_memory_id
                          AND evidence.scope_key = ge.scope_key
                          AND evidence.privacy_level = ge.privacy_level
                          AND evidence.revision_token = ge.revision_token
                    )
                  )
                  AND (:revision_token IS NULL OR ge.revision_token = :revision_token)
                  AND gn.scope_key = :scope_key AND gn.privacy_level = :privacy_level
                  AND gn.scope_key IS NOT NULL AND gn.privacy_level IS NOT NULL
                  AND gn.revision_token IS NOT NULL
                  AND (:revision_token IS NULL OR gn.revision_token = :revision_token)
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
                    "source_boundary": self._row_boundary(row),
                }
            )
        return hits

    async def get_neighbor_node_ids(
        self,
        node_ids: list[int],
        limit: int,
        *,
        boundary: GraphBoundary | None = None,
        query_scope: GraphQueryScope | None = None,
        exclude_node_ids: list[int] | None = None,
    ) -> list[int]:
        """仅沿请求作用域内、仍由有效 evidence 支持的语义边遍历节点。

        每条 evidence 行必须挂在 active 语义边上；legacy 边行（无语义边）与
        失去来源 evidence 的关系不参与扩展。同一语义边被多个来源支持时，
        按语义边去重后计权，不因来源数量被重复放大。种子与 ``exclude_node_ids``
        在 revision 无关的请求 scope 下按 canonical ``node_key`` 排除，避免多跳
        扩展经其他 revision 的物理节点绕回已命中节点。
        """
        if not node_ids:
            return []
        params = self._query_params(
            boundary=boundary,
            query_scope=query_scope,
            node_ids_json=json.dumps(sorted({int(item) for item in node_ids})),
            excluded_ids_json=json.dumps(
                sorted({int(item) for item in (*node_ids, *(exclude_node_ids or ()))})
            ),
            limit=max(1, min(limit, 500)),
        )
        async with self._connect() as db:
            cursor = await db.execute(
                """WITH scoped_edges AS (
                    SELECT DISTINCT edge.semantic_edge_id,
                           edge.source_node_id, edge.target_node_id,
                           source.node_key AS source_node_key,
                           target.node_key AS target_node_key,
                           semantic.weight
                    FROM graph_edges edge
                    JOIN graph_semantic_edges semantic
                      ON semantic.id = edge.semantic_edge_id
                     AND semantic.scope_key = edge.scope_key
                     AND semantic.privacy_level = edge.privacy_level
                     AND semantic.status = 'active'
                    JOIN graph_nodes source ON source.id = edge.source_node_id
                    JOIN graph_nodes target ON target.id = edge.target_node_id
                    WHERE edge.status = 'active'
                      AND edge.semantic_edge_id IS NOT NULL
                      AND edge.scope_key = :scope_key
                      AND edge.privacy_level = :privacy_level
                      AND edge.scope_key IS NOT NULL
                      AND edge.privacy_level IS NOT NULL
                      AND edge.revision_token IS NOT NULL
                      AND (:revision_token IS NULL OR edge.revision_token = :revision_token)
                      AND source.scope_key = :scope_key
                      AND source.privacy_level = :privacy_level
                      AND source.revision_token IS NOT NULL
                      AND (:revision_token IS NULL OR source.revision_token = :revision_token)
                      AND target.scope_key = :scope_key
                      AND target.privacy_level = :privacy_level
                      AND target.revision_token IS NOT NULL
                      AND (:revision_token IS NULL OR target.revision_token = :revision_token)
                ), seed_nodes AS (
                    SELECT DISTINCT node.id, node.node_key
                    FROM graph_nodes node
                    WHERE node.id IN (
                        SELECT value FROM json_each(:node_ids_json)
                    )
                      AND node.scope_key = :scope_key
                      AND node.privacy_level = :privacy_level
                      AND node.revision_token IS NOT NULL
                      AND (:revision_token IS NULL OR node.revision_token = :revision_token)
                ), excluded_nodes AS (
                    SELECT DISTINCT node.id, node.node_key
                    FROM graph_nodes node
                    WHERE node.id IN (
                        SELECT value FROM json_each(:excluded_ids_json)
                    )
                      AND node.scope_key = :scope_key
                      AND node.privacy_level = :privacy_level
                ), directed_edges AS (
                    SELECT target_node_key AS neighbor_key,
                           MIN(target_node_id) AS neighbor_id,
                           semantic_edge_id, weight
                    FROM scoped_edges
                    WHERE (
                        (:revision_token IS NULL AND source_node_key IN (
                            SELECT node_key FROM seed_nodes
                        ))
                        OR (:revision_token IS NOT NULL AND source_node_id IN (
                            SELECT id FROM seed_nodes
                        ))
                    )
                    GROUP BY target_node_key, semantic_edge_id, weight
                    UNION ALL
                    SELECT source_node_key AS neighbor_key,
                           MIN(source_node_id) AS neighbor_id,
                           semantic_edge_id, weight
                    FROM scoped_edges
                    WHERE (
                        (:revision_token IS NULL AND target_node_key IN (
                            SELECT node_key FROM seed_nodes
                        ))
                        OR (:revision_token IS NOT NULL AND target_node_id IN (
                            SELECT id FROM seed_nodes
                        ))
                    )
                    GROUP BY source_node_key, semantic_edge_id, weight
                ), aggregated_neighbors AS (
                    SELECT neighbor_key, MIN(neighbor_id) AS neighbor_id,
                           SUM(weight) AS total_weight
                    FROM directed_edges
                    WHERE (
                        (:revision_token IS NULL AND neighbor_key NOT IN (
                            SELECT node_key FROM excluded_nodes
                        ))
                        OR (:revision_token IS NOT NULL AND neighbor_id NOT IN (
                            SELECT id FROM excluded_nodes
                        ))
                    )
                    GROUP BY neighbor_key
                )
                SELECT neighbor_id, total_weight
                FROM aggregated_neighbors
                ORDER BY total_weight DESC, neighbor_id ASC
                LIMIT :limit""",
                params,
            )
            rows = await cursor.fetchall()
        return [int(row[0]) for row in rows]

    async def get_recent_memory_ids(
        self,
        limit: int = 12,
        session_id: str | None = None,
        persona_id: str | None = None,
        *,
        boundary: GraphBoundary | None = None,
        query_scope: GraphQueryScope | None = None,
    ) -> list[int]:
        """返回请求 scope 内最近更新的记忆标识符。"""
        params = self._query_params(
            boundary=boundary,
            query_scope=query_scope,
            session_id=session_id,
            persona_id=persona_id,
            limit=max(1, min(limit, 200)),
        )
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT source_memory_id, MAX(id) AS latest_entry_id
                FROM graph_entries
                WHERE (:session_id IS NULL OR session_id = :session_id)
                  AND (:persona_id IS NULL OR persona_id = :persona_id)
                  AND scope_key = :scope_key AND privacy_level = :privacy_level
                  AND scope_key IS NOT NULL AND privacy_level IS NOT NULL
                  AND revision_token IS NOT NULL
                  AND (
                    (edge_id IS NULL AND entry_type <> 'edge')
                    OR EXISTS (
                        SELECT 1
                        FROM graph_edges evidence
                        JOIN graph_semantic_edges semantic
                          ON semantic.id = evidence.semantic_edge_id
                         AND semantic.scope_key = evidence.scope_key
                         AND semantic.privacy_level = evidence.privacy_level
                         AND semantic.status = 'active'
                        WHERE evidence.id = graph_entries.edge_id
                          AND evidence.status = 'active'
                          AND evidence.semantic_edge_id IS NOT NULL
                          AND evidence.source_memory_id = graph_entries.source_memory_id
                          AND evidence.scope_key = graph_entries.scope_key
                          AND evidence.privacy_level = graph_entries.privacy_level
                          AND evidence.revision_token = graph_entries.revision_token
                    )
                  )
                  AND (:revision_token IS NULL OR revision_token = :revision_token)
                GROUP BY source_memory_id
                ORDER BY latest_entry_id DESC
                LIMIT :limit
                """,
                params,
            )
            rows = await cursor.fetchall()

        return [int(row["source_memory_id"]) for row in rows]
