"""面向管理面板画布的轻量全量图快照。"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from .....shared.memory_status import is_memory_recallable
from ....quality.application.gate_disposition_filter import is_mark_write
from ...domain.memory_atom import has_user_source_evidence
from ...domain.revision import memory_revision
from ...infrastructure.base import BaseStore
from ..domain.models import GraphBoundary
from .graph_subgraph import GraphSubgraphMixin

# 管理员总览的节点、边查询：来源边界由 Python 侧按当前 canonical 校验后传入
# ``:sources_json``（[{memory_id, scope_key, privacy_level, revision_token}]），
# 派生行必须与来源当前边界逐字段相等，legacy/NULL/陈旧 revision 行自然落选。
# 边还要求两个端点与 edge_id 关联的条目时间都归属该边自身的 source_memory_id，
# 同边界下的其它来源不得借用端点，也不得改写边的展示与过滤时间。
_ADMIN_CANVAS_NODES_SQL = """
WITH scoped_entries AS (
    SELECT ge.id, ge.source_memory_id, ge.scope_key, ge.privacy_level,
           ge.revision_token
    FROM graph_entries ge
    JOIN json_each(:sources_json) AS allowed
      ON ge.source_memory_id = json_extract(allowed.value, '$.memory_id')
     AND ge.scope_key = json_extract(allowed.value, '$.scope_key')
     AND ge.privacy_level = json_extract(allowed.value, '$.privacy_level')
     AND ge.revision_token = json_extract(allowed.value, '$.revision_token')
    WHERE (:session_id IS NULL OR ge.session_id = :session_id)
      AND (:persona_id IS NULL OR ge.persona_id = :persona_id)
)
SELECT gn.id, gn.node_key, gn.node_type, gn.node_value, gn.canonical_value,
       COUNT(DISTINCT se.id) AS entry_count,
       COUNT(DISTINCT se.source_memory_id) AS memory_count
FROM scoped_entries se
JOIN graph_entry_nodes gen ON gen.entry_id = se.id
JOIN graph_nodes gn ON gn.id = gen.node_id
  AND gn.scope_key = se.scope_key
  AND gn.privacy_level = se.privacy_level
  AND gn.revision_token = se.revision_token
GROUP BY gn.id
"""

_ADMIN_CANVAS_EDGES_SQL = """
WITH scoped_entries AS (
    SELECT ge.id, ge.source_memory_id, ge.edge_id, ge.metadata,
           ge.scope_key, ge.privacy_level, ge.revision_token
    FROM graph_entries ge
    JOIN json_each(:sources_json) AS allowed
      ON ge.source_memory_id = json_extract(allowed.value, '$.memory_id')
     AND ge.scope_key = json_extract(allowed.value, '$.scope_key')
     AND ge.privacy_level = json_extract(allowed.value, '$.privacy_level')
     AND ge.revision_token = json_extract(allowed.value, '$.revision_token')
    WHERE (:session_id IS NULL OR ge.session_id = :session_id)
      AND (:persona_id IS NULL OR ge.persona_id = :persona_id)
),
scoped_nodes AS (
    SELECT DISTINCT gen.node_id, se.source_memory_id, se.scope_key,
           se.privacy_level, se.revision_token
    FROM scoped_entries se
    JOIN graph_entry_nodes gen ON gen.entry_id = se.id
    JOIN graph_nodes gn ON gn.id = gen.node_id
      AND gn.scope_key = se.scope_key
      AND gn.privacy_level = se.privacy_level
      AND gn.revision_token = se.revision_token
),
edge_entry_ids AS (
    SELECT se.edge_id, MAX(se.id) AS entry_id
    FROM scoped_entries se
    JOIN graph_edges edge
      ON edge.id = se.edge_id
     AND edge.source_memory_id = se.source_memory_id
     AND edge.scope_key = se.scope_key
     AND edge.privacy_level = se.privacy_level
     AND edge.revision_token = se.revision_token
    WHERE se.edge_id IS NOT NULL
    GROUP BY se.edge_id, se.source_memory_id, se.scope_key, se.privacy_level,
             se.revision_token
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
JOIN json_each(:sources_json) AS allowed
  ON edge.source_memory_id = json_extract(allowed.value, '$.memory_id')
 AND edge.scope_key = json_extract(allowed.value, '$.scope_key')
 AND edge.privacy_level = json_extract(allowed.value, '$.privacy_level')
 AND edge.revision_token = json_extract(allowed.value, '$.revision_token')
JOIN scoped_nodes source_node ON source_node.node_id = edge.source_node_id
 AND source_node.source_memory_id = edge.source_memory_id
 AND source_node.scope_key = edge.scope_key
 AND source_node.privacy_level = edge.privacy_level
 AND source_node.revision_token = edge.revision_token
JOIN scoped_nodes target_node ON target_node.node_id = edge.target_node_id
 AND target_node.source_memory_id = edge.source_memory_id
 AND target_node.scope_key = edge.scope_key
 AND target_node.privacy_level = edge.privacy_level
 AND target_node.revision_token = edge.revision_token
LEFT JOIN edge_entry_times entry_time ON entry_time.edge_id = edge.id
WHERE EXISTS (
    SELECT 1
    FROM scoped_entries se
    WHERE se.source_memory_id = edge.source_memory_id
      AND se.scope_key = edge.scope_key
      AND se.privacy_level = edge.privacy_level
      AND se.revision_token = edge.revision_token
)
ORDER BY edge.id DESC
"""


class GraphCanvasMixin(BaseStore):
    """提供不携带条目正文与记忆摘要的全量图画布读取能力。"""

    async def get_canvas_snapshot(
        self,
        *,
        boundary: GraphBoundary,
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
        GraphBoundary.require(boundary)
        params = {
            **boundary.as_params(),
            "session_id": session_id,
            "persona_id": persona_id,
        }

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
                  AND ge.scope_key = :scope_key AND ge.privacy_level = :privacy_level
                  AND ge.revision_token = :revision_token
                  AND gn.scope_key = :scope_key AND gn.privacy_level = :privacy_level
                  AND gn.revision_token = :revision_token
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
                      AND ge.scope_key = :scope_key AND ge.privacy_level = :privacy_level
                      AND ge.revision_token = :revision_token
                ),
                scoped_nodes AS (
                    SELECT DISTINCT gen.node_id
                    FROM graph_entry_nodes gen
                    JOIN scoped_entries se ON se.id = gen.entry_id
                    JOIN graph_nodes gn ON gn.id = gen.node_id
                    WHERE gn.scope_key = :scope_key AND gn.privacy_level = :privacy_level
                      AND gn.revision_token = :revision_token
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
                  AND edge.scope_key = :scope_key AND edge.privacy_level = :privacy_level
                  AND edge.revision_token = :revision_token
                ORDER BY edge.id DESC
                """,
                params,
            )
            edge_rows = list(await edge_cursor.fetchall())

        return self._assemble_canvas_snapshot(
            node_rows,
            edge_rows,
            oldest_timestamp=oldest_timestamp,
            newest_timestamp=newest_timestamp,
        )

    async def get_admin_canvas_snapshot(
        self,
        *,
        session_id: str | None = None,
        persona_id: str | None = None,
        oldest_timestamp: float | None = None,
        newest_timestamp: float | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """返回管理面板总览使用的跨来源轻量画布快照。

        每个来源都按图写入时的同一套 canonical 合同重新校验当前
        scope/privacy/revision、可召回状态、mark_write 门禁与事实用户证据；
        未通过的来源整体丢弃，节点与边只由同一合法来源条目产生，
        端点与边时间不得借用其它来源，也不会跨来源合并或泄漏无效来源。
        canonical 证据与派生行在同一个只读事务快照内读取，读取失败原样上报
        而不伪装成空画布。

        Args:
            session_id: 可选会话展示过滤。
            persona_id: 可选人格展示过滤。
            oldest_timestamp: 允许显示的最旧 Unix 秒；缺失时不限制。
            newest_timestamp: 允许显示的最新 Unix 秒；缺失时不限制。

        Returns:
            不携带条目正文与记忆摘要的节点、边快照。
        """
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            # 显式只读事务：canonical 证据与派生行必须来自同一快照，
            # 避免校验与聚合之间来源再次变更。BEGIN 也在守卫内，
            # 取消时回滚，避免把未结事务的连接归还共享池。
            try:
                await db.execute("BEGIN")
                source_cursor = await db.execute(
                    """
                    SELECT DISTINCT ge.source_memory_id AS memory_id,
                           d.metadata, d.created_at, d.updated_at
                    FROM graph_entries ge
                    LEFT JOIN documents d ON d.id = ge.source_memory_id
                    """
                )
                source_rows = list(await source_cursor.fetchall())
                allowed_sources = self._allowed_admin_canvas_sources(source_rows)
                node_rows: list[aiosqlite.Row] = []
                edge_rows: list[aiosqlite.Row] = []
                if allowed_sources:
                    # ensure_ascii 保持默认：canonical 里可能带转义孤立 surrogate，
                    # 非 ASCII 直接绑定会 UnicodeEncodeError，转义后 SQLite 仍比较解码值。
                    params = {
                        "sources_json": json.dumps(allowed_sources),
                        "session_id": session_id,
                        "persona_id": persona_id,
                    }
                    node_cursor = await db.execute(_ADMIN_CANVAS_NODES_SQL, params)
                    node_rows = list(await node_cursor.fetchall())
                    edge_cursor = await db.execute(_ADMIN_CANVAS_EDGES_SQL, params)
                    edge_rows = list(await edge_cursor.fetchall())
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

        return self._assemble_canvas_snapshot(
            node_rows,
            edge_rows,
            oldest_timestamp=oldest_timestamp,
            newest_timestamp=newest_timestamp,
        )

    def _allowed_admin_canvas_sources(
        self,
        source_rows: list[aiosqlite.Row],
    ) -> list[dict[str, Any]]:
        """校验每个来源当前 canonical 快照，返回允许展示的边界集合。"""
        allowed: list[dict[str, Any]] = []
        for row in source_rows:
            boundary = self._admin_canvas_source_boundary(row)
            if boundary is None:
                continue
            allowed.append({"memory_id": int(row["memory_id"]), **boundary.as_params()})
        return allowed

    def _admin_canvas_source_boundary(
        self,
        row: aiosqlite.Row,
    ) -> GraphBoundary | None:
        """按图写入的同一套 canonical 合同校验来源，非法来源返回 ``None``。"""
        try:
            raw_metadata = row["metadata"]
            revision_token = memory_revision(
                {"created_at": row["created_at"], "updated_at": row["updated_at"]}
            )
        except (IndexError, KeyError):
            return None
        # canonical metadata 必须是文本：BLOB 或损坏行只让该来源失效，
        # 不吞任何数据库错误。
        if not isinstance(raw_metadata, str):
            return None
        try:
            metadata = self._from_json(raw_metadata)
        except (RecursionError, ValueError):
            # 超深 JSON 或超限数字等解码异常只跳过该来源，
            # 不把整页总览升级为错误，也不掩盖查询阶段的数据库错误。
            return None
        metadata["revision_token"] = revision_token
        try:
            boundary = GraphBoundary.from_metadata(metadata)
        except ValueError:
            return None
        if not is_memory_recallable(metadata) or is_mark_write(metadata):
            return None
        facts = metadata.get("key_facts")
        evidence = metadata.get("fact_source_evidence")
        if (
            not isinstance(facts, list)
            or not facts
            or any(not isinstance(fact, str) or not fact.strip() for fact in facts)
            or not isinstance(evidence, list)
            or len(evidence) != len(facts)
            or not all(has_user_source_evidence(refs) for refs in evidence)
        ):
            return None
        return boundary

    def _assemble_canvas_snapshot(
        self,
        node_rows: list[aiosqlite.Row],
        edge_rows: list[aiosqlite.Row],
        *,
        oldest_timestamp: float | None,
        newest_timestamp: float | None,
    ) -> dict[str, list[dict[str, Any]]]:
        """构建画布节点与边，并按时间范围裁剪边与孤立节点。"""
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
