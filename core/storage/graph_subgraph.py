"""图子图检索，按记忆 ID 返回紧凑的图快照。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from ..utils.number_utils import safe_float
from .base import BaseStore


class GraphSubgraphMixin(BaseStore):
    """为 GraphStore 提供子图检索能力。"""

    async def get_subgraph_for_memories(
        self,
        memory_ids: list[int],
        limit_entries: int = 36,
        limit_nodes: int = 48,
        limit_edges: int = 72,
    ) -> dict[str, Any]:
        """为给定记忆 ID 返回紧凑的图快照。"""
        normalized_memory_ids: list[int] = []
        seen_memory_ids: set[int] = set()
        for memory_id in memory_ids:
            try:
                normalized = int(memory_id)
            except (TypeError, ValueError):
                continue
            if normalized in seen_memory_ids:
                continue
            seen_memory_ids.add(normalized)
            normalized_memory_ids.append(normalized)

        if not normalized_memory_ids:
            return {"nodes": [], "edges": [], "entries": [], "memories": []}

        limit_entries = max(1, min(limit_entries, 400))
        limit_nodes = max(1, min(limit_nodes, 200))
        limit_edges = max(1, min(limit_edges, 400))

        entry_rows, node_rows, edge_rows = await self._fetch_subgraph_rows(
            normalized_memory_ids, limit_entries, limit_edges
        )
        return self._assemble_graph_snapshot(
            entry_rows,
            node_rows,
            edge_rows,
            limit_nodes=limit_nodes,
        )

    async def _get_full_graph_snapshot(
        self,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> dict[str, Any]:
        """返回指定会话与人格范围内未经数量裁剪的完整图快照。"""
        entry_rows, node_rows, edge_rows = await self._fetch_full_graph_rows(
            session_id=session_id,
            persona_id=persona_id,
        )
        return self._assemble_graph_snapshot(
            entry_rows,
            node_rows,
            edge_rows,
            limit_nodes=None,
        )

    def _assemble_graph_snapshot(
        self,
        entry_rows: list[aiosqlite.Row],
        node_rows: list[aiosqlite.Row],
        edge_rows: list[aiosqlite.Row],
        *,
        limit_nodes: int | None,
    ) -> dict[str, Any]:
        """将图查询行组装成统一快照，并按需执行节点数量裁剪。"""
        if not entry_rows:
            return {"nodes": [], "edges": [], "entries": [], "memories": []}

        entry_node_map, node_map = self._build_subgraph_maps(node_rows)

        memory_base: dict[int, dict[str, Any]] = {}
        entries = self._build_subgraph_entries(
            entry_rows, entry_node_map, node_map, memory_base
        )
        edge_time_by_id = self._build_edge_time_by_id(entries)
        edges = self._build_subgraph_edges(
            edge_rows,
            node_map,
            memory_base,
            edge_time_by_id=edge_time_by_id,
        )

        for node in node_map.values():
            memory_ids_for_node = node.pop("_memory_ids", set())
            node["memory_count"] = len(memory_ids_for_node)
            node["weight"] = round(
                node["entry_count"]
                + node["memory_count"] * 0.75
                + node["degree"] * 0.35,
                4,
            )

        nodes_were_limited = limit_nodes is not None and len(node_map) > limit_nodes
        if nodes_were_limited and limit_nodes is not None:
            ranked_nodes = sorted(
                node_map.values(),
                key=lambda item: (
                    -safe_float(item.get("weight"), 0.0),
                    -int(item.get("entry_count", 0)),
                    -int(item.get("degree", 0)),
                    str(item.get("label", "")),
                ),
            )
            allowed_node_ids = {node["id"] for node in ranked_nodes[:limit_nodes]}
            node_map = {
                node_id: node
                for node_id, node in node_map.items()
                if node_id in allowed_node_ids
            }
            edges = [
                edge
                for edge in edges
                if edge["source"] in allowed_node_ids
                and edge["target"] in allowed_node_ids
            ]
            filtered_entries: list[dict[str, Any]] = []
            for entry in entries:
                entry["node_ids"] = [
                    node_id
                    for node_id in entry["node_ids"]
                    if node_id in allowed_node_ids
                ]
                if entry["node_ids"] or entry["entry_type"] == "summary":
                    filtered_entries.append(entry)
            entries = filtered_entries

        memories = self._build_subgraph_memories(
            memory_base, entries, edges, nodes_were_limited
        )

        nodes = sorted(
            node_map.values(),
            key=lambda item: (
                -safe_float(item.get("weight"), 0.0),
                -int(item.get("entry_count", 0)),
                -int(item.get("degree", 0)),
                str(item.get("label", "")),
            ),
        )
        memories.sort(
            key=lambda item: (
                -int(item.get("entry_count", 0)),
                -int(item.get("node_count", 0)),
                -int(item.get("edge_count", 0)),
                -safe_float(item.get("importance"), 0.0),
            )
        )

        return {
            "nodes": nodes,
            "edges": edges,
            "entries": entries,
            "memories": memories,
        }

    async def _fetch_subgraph_rows(
        self,
        normalized_memory_ids: list[int],
        limit_entries: int,
        limit_edges: int,
    ) -> tuple[list[aiosqlite.Row], list[aiosqlite.Row], list[aiosqlite.Row]]:
        """查询给定记忆 ID 对应的条目、节点和边数据。"""
        memory_params = {"memory_ids_json": json.dumps(normalized_memory_ids)}

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            entry_cursor = await db.execute(
                """
                SELECT id, source_memory_id, session_id, persona_id,
                       entry_type, relation_type, content, metadata, edge_id
                FROM graph_entries
                WHERE source_memory_id IN (
                    SELECT value FROM json_each(:memory_ids_json)
                )
                ORDER BY id DESC
                LIMIT :limit_entries
                """,
                {**memory_params, "limit_entries": limit_entries},
            )
            entry_rows = list(await entry_cursor.fetchall())

            if not entry_rows:
                return [], [], []

            entry_ids = [int(row["id"]) for row in entry_rows]
            node_cursor = await db.execute(
                """
                SELECT gen.entry_id,
                       gn.id AS node_id,
                       gn.node_key,
                       gn.node_type,
                       gn.node_value,
                       gn.canonical_value,
                       gn.metadata
                FROM graph_entry_nodes gen
                JOIN graph_nodes gn ON gn.id = gen.node_id
                WHERE gen.entry_id IN (
                    SELECT value FROM json_each(:entry_ids_json)
                )
                ORDER BY gn.id ASC
                """,
                {"entry_ids_json": json.dumps(entry_ids)},
            )
            node_rows = list(await node_cursor.fetchall())

            node_ids = sorted({int(row["node_id"]) for row in node_rows})
            edge_rows: list[aiosqlite.Row] = []
            if node_ids:
                edge_cursor = await db.execute(
                    """
                    SELECT id, edge_key, source_node_id, target_node_id,
                           relation_type, source_memory_id, weight,
                           confidence, status, metadata, created_at
                    FROM graph_edges
                    WHERE source_memory_id IN (
                        SELECT value FROM json_each(:memory_ids_json)
                    )
                      AND source_node_id IN (
                        SELECT value FROM json_each(:node_ids_json)
                      )
                      AND target_node_id IN (
                        SELECT value FROM json_each(:node_ids_json)
                      )
                    ORDER BY id DESC
                    LIMIT :limit_edges
                    """,
                    {
                        **memory_params,
                        "node_ids_json": json.dumps(node_ids),
                        "limit_edges": limit_edges,
                    },
                )
                edge_rows = list(await edge_cursor.fetchall())

        return entry_rows, node_rows, edge_rows

    async def _fetch_full_graph_rows(
        self,
        *,
        session_id: str | None,
        persona_id: str | None,
    ) -> tuple[list[aiosqlite.Row], list[aiosqlite.Row], list[aiosqlite.Row]]:
        """一次读取指定作用域内的全部图条目、关联节点和边。"""
        params = {"session_id": session_id, "persona_id": persona_id}

        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            entry_cursor = await db.execute(
                """
                SELECT ge.id, ge.source_memory_id, ge.session_id, ge.persona_id,
                       ge.entry_type, ge.relation_type, ge.content,
                       ge.metadata, ge.edge_id
                FROM graph_entries ge
                WHERE (:session_id IS NULL OR ge.session_id = :session_id)
                  AND (:persona_id IS NULL OR ge.persona_id = :persona_id)
                ORDER BY ge.id DESC
                """,
                params,
            )
            entry_rows = list(await entry_cursor.fetchall())
            if not entry_rows:
                return [], [], []

            node_cursor = await db.execute(
                """
                SELECT gen.entry_id,
                       gn.id AS node_id,
                       gn.node_key,
                       gn.node_type,
                       gn.node_value,
                       gn.canonical_value,
                       gn.metadata
                FROM graph_entry_nodes gen
                JOIN graph_entries ge ON ge.id = gen.entry_id
                JOIN graph_nodes gn ON gn.id = gen.node_id
                WHERE (:session_id IS NULL OR ge.session_id = :session_id)
                  AND (:persona_id IS NULL OR ge.persona_id = :persona_id)
                ORDER BY gn.id ASC
                """,
                params,
            )
            node_rows = list(await node_cursor.fetchall())

            edge_cursor = await db.execute(
                """
                SELECT graph_edge.id, graph_edge.edge_key,
                       graph_edge.source_node_id, graph_edge.target_node_id,
                       graph_edge.relation_type, graph_edge.source_memory_id,
                       graph_edge.weight, graph_edge.confidence,
                       graph_edge.status, graph_edge.metadata,
                       graph_edge.created_at
                FROM graph_edges graph_edge
                WHERE EXISTS (
                    SELECT 1
                    FROM graph_entries ge
                    WHERE ge.source_memory_id = graph_edge.source_memory_id
                      AND (:session_id IS NULL OR ge.session_id = :session_id)
                      AND (:persona_id IS NULL OR ge.persona_id = :persona_id)
                )
                ORDER BY graph_edge.id DESC
                """,
                params,
            )
            edge_rows = list(await edge_cursor.fetchall())

        return entry_rows, node_rows, edge_rows

    def _build_subgraph_maps(
        self,
        node_rows: list[aiosqlite.Row],
    ) -> tuple[dict[int, list[int]], dict[int, dict[str, Any]]]:
        """根据节点查询结果构建条目到节点的映射及节点元数据表。"""
        entry_node_map: dict[int, list[int]] = {}
        node_map: dict[int, dict[str, Any]] = {}

        for row in node_rows:
            entry_id = int(row["entry_id"])
            node_id = int(row["node_id"])
            entry_node_map.setdefault(entry_id, []).append(node_id)
            if node_id not in node_map:
                node_map[node_id] = {
                    "id": node_id,
                    "key": row["node_key"],
                    "type": row["node_type"],
                    "label": row["node_value"],
                    "canonical_value": row["canonical_value"],
                    "metadata": self._from_json(row["metadata"]),
                    "entry_count": 0,
                    "memory_count": 0,
                    "degree": 0,
                    "weight": 0.0,
                    "_memory_ids": set(),
                }

        return entry_node_map, node_map

    def _build_subgraph_entries(
        self,
        entry_rows: list[aiosqlite.Row],
        entry_node_map: dict[int, list[int]],
        node_map: dict[int, dict[str, Any]],
        memory_base: dict[int, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """构建条目列表，并同步填充 memory_base 与节点计数。"""
        entries: list[dict[str, Any]] = []
        for row in entry_rows:
            entry_id = int(row["id"])
            memory_id = int(row["source_memory_id"])
            metadata = self._from_json(row["metadata"])
            node_ids_for_entry = list(dict.fromkeys(entry_node_map.get(entry_id, [])))

            entries.append(
                {
                    "id": entry_id,
                    "memory_id": memory_id,
                    "entry_type": row["entry_type"],
                    "relation_type": row["relation_type"],
                    "content": row["content"],
                    "metadata": metadata,
                    "session_id": row["session_id"],
                    "persona_id": row["persona_id"],
                    "edge_id": int(row["edge_id"]) if row["edge_id"] else None,
                    "node_ids": node_ids_for_entry,
                }
            )

            base = memory_base.setdefault(
                memory_id,
                {
                    "memory_id": memory_id,
                    "summary": metadata.get("canonical_summary") or row["content"],
                    "session_id": metadata.get("session_id") or row["session_id"],
                    "persona_id": metadata.get("persona_id") or row["persona_id"],
                    "importance": safe_float(metadata.get("importance"), 0.0),
                    "entry_count": 0,
                    "edge_count": 0,
                    "node_ids": set(),
                    "entry_types": set(),
                },
            )
            base["entry_count"] += 1
            base["entry_types"].add(row["entry_type"])
            base["node_ids"].update(node_ids_for_entry)

            for node_id in node_ids_for_entry:
                node = node_map.get(node_id)
                if node is None:
                    continue
                node["entry_count"] += 1
                node["_memory_ids"].add(memory_id)

        return entries

    def _build_subgraph_edges(
        self,
        edge_rows: list[aiosqlite.Row],
        node_map: dict[int, dict[str, Any]],
        memory_base: dict[int, dict[str, Any]],
        edge_time_by_id: dict[int, float] | None = None,
    ) -> list[dict[str, Any]]:
        """构建边列表，并更新节点度数与记忆边计数。"""
        edges: list[dict[str, Any]] = []
        edge_time_by_id = edge_time_by_id or {}
        for row in edge_rows:
            edge_id = int(row["id"])
            source_node_id = int(row["source_node_id"])
            target_node_id = int(row["target_node_id"])
            relation_type = row["relation_type"]
            created_at = row["created_at"] if "created_at" in row.keys() else None
            metadata = self._from_json(row["metadata"])
            timestamp = self._edge_timestamp(
                relation_type=relation_type,
                metadata=metadata,
                created_at=created_at,
                entry_time=edge_time_by_id.get(edge_id),
            )
            edge = {
                "id": edge_id,
                "key": row["edge_key"],
                "source": source_node_id,
                "target": target_node_id,
                "relation_type": relation_type,
                "type": relation_type,
                "memory_id": int(row["source_memory_id"]),
                "weight": float(row["weight"]),
                "confidence": float(row["confidence"]),
                "status": row["status"],
                "metadata": metadata,
                "created_at": created_at,
                "timestamp": timestamp,
            }
            edges.append(edge)

            if source_node_id in node_map:
                node_map[source_node_id]["degree"] += 1
            if target_node_id in node_map:
                node_map[target_node_id]["degree"] += 1
            if edge["memory_id"] in memory_base:
                memory_base[edge["memory_id"]]["edge_count"] += 1

        return edges

    def _build_edge_time_by_id(self, entries: list[dict[str, Any]]) -> dict[int, float]:
        """从条目 metadata 中提取与 edge_id 绑定的业务时间。"""
        edge_time_by_id: dict[int, float] = {}
        for entry in entries:
            edge_id = entry.get("edge_id")
            if edge_id is None:
                continue
            timestamp = self._metadata_timestamp(entry.get("metadata") or {})
            if timestamp is None:
                continue
            edge_time_by_id.setdefault(int(edge_id), timestamp)
        return edge_time_by_id

    def _edge_timestamp(
        self,
        *,
        relation_type: Any,
        metadata: dict[str, Any],
        created_at: Any,
        entry_time: float | None,
    ) -> float | None:
        """选择图边展示与筛选使用的时间戳。"""
        metadata_time = self._metadata_timestamp(metadata, relation_type=relation_type)
        if metadata_time is not None:
            return metadata_time
        if entry_time is not None:
            return self._coerce_timestamp(entry_time)
        return self._timestamp_from_iso(created_at)

    @classmethod
    def _metadata_timestamp(
        cls,
        metadata: dict[str, Any],
        *,
        relation_type: Any = None,
    ) -> float | None:
        """从图元数据及关系类型中提取优先业务时间戳。"""
        for key in ("event_time", "timestamp", "create_time"):
            timestamp = cls._coerce_timestamp(metadata.get(key))
            if timestamp is not None:
                return timestamp

        relation = str(relation_type or "").lower()
        if relation == "after":
            return cls._coerce_timestamp(metadata.get("event_time_b"))
        if relation in {"before", "during"}:
            return cls._coerce_timestamp(metadata.get("event_time_a"))
        return None

    @staticmethod
    def _timestamp_from_iso(value: Any) -> float | None:
        """将 ISO 时间字符串转换为前端图谱筛选使用的 Unix 秒。"""
        return GraphSubgraphMixin._coerce_timestamp(value)

    @staticmethod
    def _coerce_timestamp(value: Any) -> float | None:
        """把 Unix 秒、毫秒或 ISO 字符串转换为正数 Unix 秒。"""
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp <= 0:
                return None
            while timestamp > 100_000_000_000:
                timestamp /= 1000.0
            return timestamp
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip()
        try:
            timestamp = float(normalized)
        except ValueError:
            timestamp = 0.0
        if timestamp > 0:
            while timestamp > 100_000_000_000:
                timestamp /= 1000.0
            return timestamp
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    @staticmethod
    def _build_subgraph_memories(
        memory_base: dict[int, dict[str, Any]],
        entries: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        nodes_were_limited: bool,
    ) -> list[dict[str, Any]]:
        """根据 memory_base 生成并返回最终的记忆列表。"""
        memory_view: dict[int, dict[str, Any]] = {}
        for memory_id, base in memory_base.items():
            memory_view[memory_id] = {
                "memory_id": memory_id,
                "summary": base["summary"],
                "session_id": base["session_id"],
                "persona_id": base["persona_id"],
                "importance": base["importance"],
                "entry_count": base["entry_count"],
                "edge_count": base["edge_count"],
                "node_ids": set(base["node_ids"]),
                "entry_types": set(base["entry_types"]),
            }

        if not nodes_were_limited:
            filtered_memory_map = memory_view
        else:
            filtered_memory_map = {
                memory_id: {
                    **base,
                    "entry_count": 0,
                    "edge_count": 0,
                    "node_ids": set(),
                    "entry_types": set(),
                }
                for memory_id, base in memory_view.items()
            }
            for entry in entries:
                memory = filtered_memory_map.get(entry["memory_id"])
                if memory is None:
                    continue
                memory["entry_count"] += 1
                memory["node_ids"].update(entry["node_ids"])
                memory["entry_types"].add(entry["entry_type"])

            for edge in edges:
                memory = filtered_memory_map.get(edge["memory_id"])
                if memory is not None:
                    memory["edge_count"] += 1

        memories: list[dict[str, Any]] = []
        for memory in filtered_memory_map.values():
            if memory["entry_count"] == 0 and memory["edge_count"] == 0:
                continue
            node_ids_for_memory = memory.pop("node_ids")
            entry_types = memory.pop("entry_types")
            memory["node_count"] = len(node_ids_for_memory)
            memory["entry_types"] = sorted(entry_types)
            memories.append(memory)

        return memories
