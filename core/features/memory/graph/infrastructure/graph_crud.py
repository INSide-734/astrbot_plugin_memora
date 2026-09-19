"""GraphStore 的节点、边与条目 CRUD 实现。"""

from __future__ import annotations

import json

import aiosqlite

from ...infrastructure.base import BaseStore
from ..domain.models import GraphBoundary, GraphEdge, GraphEntry, GraphNode


class GraphCRUDMixin(BaseStore):
    """GraphStore 的节点、边与条目 CRUD 混入类。"""

    async def upsert_node(self, node: GraphNode, *, boundary: GraphBoundary) -> int:
        """插入或更新单个图节点，并返回其标识符。"""
        GraphBoundary.require(boundary)
        now = self._now_iso()
        async with self._connect() as db:
            node_id = await self._upsert_node(db, node, now, boundary=boundary)
            await db.commit()
            return node_id

    async def upsert_nodes(
        self, nodes: list[GraphNode], *, boundary: GraphBoundary
    ) -> dict[str, int]:
        """在单个事务中插入或更新多个节点。"""
        GraphBoundary.require(boundary)
        if not nodes:
            return {}

        now = self._now_iso()
        async with self._connect() as db:
            node_key_to_id = await self._upsert_nodes(db, nodes, now, boundary=boundary)
            await db.commit()
        return node_key_to_id

    async def _upsert_nodes(
        self,
        db: aiosqlite.Connection,
        nodes: list[GraphNode],
        now: str,
        *,
        boundary: GraphBoundary,
    ) -> dict[str, int]:
        """使用调用方连接插入或更新多个节点。"""
        node_key_to_id: dict[str, int] = {}
        for node in nodes:
            node_key_to_id[node.node_key] = await self._upsert_node(
                db, node, now, boundary=boundary
            )
        return node_key_to_id

    async def _upsert_node(
        self,
        db: aiosqlite.Connection,
        node: GraphNode,
        now: str,
        *,
        boundary: GraphBoundary,
    ) -> int:
        """使用调用方连接插入或更新单个节点。"""
        boundary.validate_metadata(node.metadata)
        cursor = await db.execute(
            """
            INSERT INTO graph_nodes(
                node_key, node_type, node_value, canonical_value,
                metadata, created_at, updated_at, scope_key, privacy_level, revision_token
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_key, scope_key, privacy_level, revision_token) DO UPDATE SET
                node_value = excluded.node_value,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                node.node_key,
                node.node_type,
                node.value,
                node.canonical_value,
                self._to_json(node.metadata),
                now,
                now,
                boundary.scope_key,
                boundary.privacy_level,
                boundary.revision_token,
            ),
        )
        cursor = await db.execute(
            "SELECT id FROM graph_nodes WHERE node_key = ? "
            "AND scope_key = ? AND privacy_level = ? AND revision_token = ?",
            (
                node.node_key,
                boundary.scope_key,
                boundary.privacy_level,
                boundary.revision_token,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("graph_node_write_failed")
        return int(row[0])

    async def add_edge(
        self,
        edge: GraphEdge,
        node_key_to_id: dict[str, int],
        *,
        boundary: GraphBoundary,
    ) -> int:
        """Insert or update an edge within one source and canonical boundary."""
        GraphBoundary.require(boundary)
        source_node_id = node_key_to_id[edge.source_key]
        target_node_id = node_key_to_id[edge.target_key]
        now = self._now_iso()
        async with self._connect() as db:
            edge_id = await self._add_edge(
                db,
                edge,
                source_node_id,
                target_node_id,
                now,
                boundary=boundary,
            )
            await db.commit()
            return edge_id

    async def add_edges(
        self,
        edges: list[GraphEdge],
        node_key_to_id: dict[str, int],
        *,
        boundary: GraphBoundary,
    ) -> dict[str, int]:
        """在单个事务中插入或更新多条边。"""
        GraphBoundary.require(boundary)
        if not edges:
            return {}

        now = self._now_iso()
        async with self._connect() as db:
            edge_key_to_id = await self._add_edges(
                db,
                edges,
                node_key_to_id,
                now,
                boundary=boundary,
            )
            await db.commit()
        return edge_key_to_id

    async def _add_edges(
        self,
        db: aiosqlite.Connection,
        edges: list[GraphEdge],
        node_key_to_id: dict[str, int],
        now: str,
        *,
        boundary: GraphBoundary,
    ) -> dict[str, int]:
        """使用调用方连接插入或更新多条边。"""
        edge_key_to_id: dict[str, int] = {}
        for edge in edges:
            source_node_id = node_key_to_id.get(edge.source_key)
            target_node_id = node_key_to_id.get(edge.target_key)
            if source_node_id is None or target_node_id is None:
                continue
            edge_key_to_id[edge.edge_key] = await self._add_edge(
                db,
                edge,
                source_node_id,
                target_node_id,
                now,
                boundary=boundary,
            )
        return edge_key_to_id

    async def _add_edge(
        self,
        db: aiosqlite.Connection,
        edge: GraphEdge,
        source_node_id: int,
        target_node_id: int,
        now: str,
        *,
        boundary: GraphBoundary,
    ) -> int:
        """Deduplicate only within source memory, endpoints and boundary."""
        boundary.validate_metadata(edge.metadata)
        await self._validate_node_boundary(
            db, [source_node_id, target_node_id], boundary
        )
        params = {
            **boundary.as_params(),
            "source_memory_id": edge.source_memory_id,
            "source_node_id": source_node_id,
            "target_node_id": target_node_id,
            "relation_type": edge.relation_type,
        }
        await db.execute(
            """INSERT INTO graph_edges (
                edge_key, source_node_id, target_node_id, relation_type,
                source_memory_id, weight, confidence, status, metadata,
                created_at, updated_at, scope_key, privacy_level, revision_token
            ) VALUES (
                :edge_key, :source_node_id, :target_node_id, :relation_type,
                :source_memory_id, :weight, :confidence, :status, :metadata,
                :now, :now, :scope_key, :privacy_level, :revision_token
            ) ON CONFLICT(source_memory_id, source_node_id, target_node_id,
                          relation_type, scope_key, privacy_level, revision_token)
            DO UPDATE SET weight = excluded.weight, confidence = excluded.confidence,
                status = excluded.status, metadata = excluded.metadata, updated_at = excluded.updated_at""",
            {
                **params,
                "edge_key": edge.edge_key,
                "weight": edge.weight,
                "confidence": edge.confidence,
                "status": edge.status,
                "metadata": self._to_json(edge.metadata),
                "now": now,
            },
        )
        cursor = await db.execute(
            """SELECT id FROM graph_edges
            WHERE source_memory_id = :source_memory_id
              AND source_node_id = :source_node_id AND target_node_id = :target_node_id
              AND relation_type = :relation_type AND scope_key = :scope_key
              AND privacy_level = :privacy_level AND revision_token = :revision_token""",
            params,
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("graph_edge_write_failed")
        return int(row[0])

    async def _validate_node_boundary(
        self, db: aiosqlite.Connection, node_ids: list[int], boundary: GraphBoundary
    ) -> None:
        node_ids = list(set(node_ids))
        if not node_ids:
            return
        cursor = await db.execute(
            """SELECT COUNT(*) FROM graph_nodes
            WHERE id IN (SELECT value FROM json_each(:node_ids))
              AND scope_key = :scope_key AND privacy_level = :privacy_level
              AND revision_token = :revision_token""",
            {**boundary.as_params(), "node_ids": json.dumps(node_ids)},
        )
        row = await cursor.fetchone()
        if row is None or int(row[0]) != len(node_ids):
            raise ValueError("graph_boundary_mismatch")

    async def add_entry(
        self,
        entry: GraphEntry,
        node_key_to_id: dict[str, int],
        edge_id: int | None = None,
        *,
        boundary: GraphBoundary,
    ) -> int:
        """插入或更新可搜索的图条目。"""
        GraphBoundary.require(boundary)
        now = self._now_iso()
        async with self._connect() as db:
            entry_id = await self._add_entry(
                db, entry, node_key_to_id, edge_id, now, boundary=boundary
            )
            await db.commit()
            return entry_id

    async def add_entries(
        self,
        entries: list[GraphEntry],
        node_key_to_id: dict[str, int],
        edge_key_to_id: dict[str, int],
        *,
        boundary: GraphBoundary,
    ) -> list[int]:
        """在单个事务中插入或更新可搜索的图条目。"""
        GraphBoundary.require(boundary)
        if not entries:
            return []

        now = self._now_iso()
        async with self._connect() as db:
            entry_ids = await self._add_entries(
                db,
                entries,
                node_key_to_id,
                edge_key_to_id,
                now,
                boundary=boundary,
            )
            await db.commit()
        return entry_ids

    async def _add_entries(
        self,
        db: aiosqlite.Connection,
        entries: list[GraphEntry],
        node_key_to_id: dict[str, int],
        edge_key_to_id: dict[str, int],
        now: str,
        *,
        boundary: GraphBoundary,
    ) -> list[int]:
        """使用调用方连接插入或更新多个可搜索图条目。"""
        entry_ids: list[int] = []
        for entry in entries:
            edge_id = None
            if entry.relation_type and len(entry.node_keys) >= 2:
                edge_key = (
                    f"{entry.node_keys[0]}|{entry.relation_type}|"
                    f"{entry.node_keys[1]}|{entry.source_memory_id}"
                )
                edge_id = edge_key_to_id.get(edge_key)
            entry_ids.append(
                await self._add_entry(
                    db, entry, node_key_to_id, edge_id, now, boundary=boundary
                )
            )
        return entry_ids

    async def _add_entry(
        self,
        db: aiosqlite.Connection,
        entry: GraphEntry,
        node_key_to_id: dict[str, int],
        edge_id: int | None,
        now: str,
        *,
        boundary: GraphBoundary,
    ) -> int:
        """使用调用方连接插入或更新单个可搜索图条目。"""
        boundary.validate_metadata(entry.metadata)
        await self._validate_node_boundary(
            db,
            [node_key_to_id[key] for key in entry.node_keys if key in node_key_to_id],
            boundary,
        )
        if edge_id is not None:
            cursor = await db.execute(
                """SELECT 1 FROM graph_edges WHERE id = :edge_id
                AND source_memory_id = :source_memory_id AND scope_key = :scope_key
                AND privacy_level = :privacy_level AND revision_token = :revision_token""",
                {
                    **boundary.as_params(),
                    "edge_id": edge_id,
                    "source_memory_id": entry.source_memory_id,
                },
            )
            if await cursor.fetchone() is None:
                raise ValueError("graph_boundary_mismatch")
        cursor = await db.execute(
            "SELECT id FROM graph_entries WHERE entry_key = ? AND source_memory_id = ? "
            "AND scope_key = ? AND privacy_level = ? AND revision_token = ?",
            (
                entry.entry_key,
                entry.source_memory_id,
                boundary.scope_key,
                boundary.privacy_level,
                boundary.revision_token,
            ),
        )
        row = await cursor.fetchone()

        if row:
            entry_id = int(row[0])
            await db.execute(
                """
                UPDATE graph_entries
                SET session_id = ?, persona_id = ?, entry_type = ?, relation_type = ?,
                    content = ?, metadata = ?, edge_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    entry.session_id,
                    entry.persona_id,
                    entry.entry_type,
                    entry.relation_type,
                    entry.content,
                    self._to_json(entry.metadata),
                    edge_id,
                    now,
                    entry_id,
                ),
            )
            await db.execute(
                "DELETE FROM memora_graph_entries_fts WHERE entry_id = ?",
                (entry_id,),
            )
            await db.execute(
                "DELETE FROM graph_entry_nodes WHERE entry_id = ?",
                (entry_id,),
            )
        else:
            cursor = await db.execute(
                """
                INSERT INTO graph_entries(
                    entry_key, source_memory_id, session_id, persona_id,
                    entry_type, relation_type, content, metadata,
                    edge_id, created_at, updated_at, scope_key, privacy_level, revision_token
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.entry_key,
                    entry.source_memory_id,
                    entry.session_id,
                    entry.persona_id,
                    entry.entry_type,
                    entry.relation_type,
                    entry.content,
                    self._to_json(entry.metadata),
                    edge_id,
                    now,
                    now,
                    boundary.scope_key,
                    boundary.privacy_level,
                    boundary.revision_token,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("graph_entry_write_failed")
            entry_id = int(cursor.lastrowid)

        await db.execute(
            "INSERT INTO memora_graph_entries_fts(entry_id, content) VALUES (?, ?)",
            (entry_id, entry.content),
        )
        entry_node_rows = [
            (entry_id, node_id)
            for node_id in (
                node_key_to_id.get(node_key) for node_key in entry.node_keys
            )
            if node_id is not None
        ]
        if entry_node_rows:
            await db.executemany(
                "INSERT OR IGNORE INTO graph_entry_nodes(entry_id, node_id) VALUES (?, ?)",
                entry_node_rows,
            )
        return entry_id

    async def update_entry_vector_doc_id(
        self, entry_id: int, vector_doc_id: int, *, boundary: GraphBoundary
    ) -> None:
        """持久化单个图条目的向量存储标识符。"""
        GraphBoundary.require(boundary)
        async with self._connect() as db:
            await db.execute(
                "UPDATE graph_entries SET vector_doc_id = ?, updated_at = ? WHERE id = ? "
                "AND scope_key = ? AND privacy_level = ? AND revision_token = ?",
                (
                    vector_doc_id,
                    self._now_iso(),
                    entry_id,
                    boundary.scope_key,
                    boundary.privacy_level,
                    boundary.revision_token,
                ),
            )
            await db.commit()

    async def update_entry_vector_doc_ids(
        self,
        entry_vector_doc_ids: dict[int, int],
        *,
        boundary: GraphBoundary,
    ) -> None:
        """在单个事务中持久化多个图条目的向量存储标识符。"""
        GraphBoundary.require(boundary)
        if not entry_vector_doc_ids:
            return

        now = self._now_iso()
        async with self._connect() as db:
            await db.executemany(
                "UPDATE graph_entries SET vector_doc_id = ?, updated_at = ? WHERE id = ? "
                "AND scope_key = ? AND privacy_level = ? AND revision_token = ?",
                [
                    (
                        vector_doc_id,
                        now,
                        entry_id,
                        boundary.scope_key,
                        boundary.privacy_level,
                        boundary.revision_token,
                    )
                    for entry_id, vector_doc_id in entry_vector_doc_ids.items()
                ],
            )
            await db.commit()
