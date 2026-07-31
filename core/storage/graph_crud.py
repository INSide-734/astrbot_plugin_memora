"""GraphStore 的节点、边与条目 CRUD 实现。"""

from __future__ import annotations

import aiosqlite

from ..models.graph_models import GraphEdge, GraphEntry, GraphNode
from .base import BaseStore


class GraphCRUDMixin(BaseStore):
    """GraphStore 的节点、边与条目 CRUD 混入类。"""

    async def upsert_node(self, node: GraphNode) -> int:
        """插入或更新单个图节点，并返回其标识符。"""
        now = self._now_iso()
        async with self._connect() as db:
            node_id = await self._upsert_node(db, node, now)
            await db.commit()
            return node_id

    async def upsert_nodes(self, nodes: list[GraphNode]) -> dict[str, int]:
        """在单个事务中插入或更新多个节点。"""
        if not nodes:
            return {}

        now = self._now_iso()
        async with self._connect() as db:
            node_key_to_id = await self._upsert_nodes(db, nodes, now)
            await db.commit()
        return node_key_to_id

    async def _upsert_nodes(
        self,
        db: aiosqlite.Connection,
        nodes: list[GraphNode],
        now: str,
    ) -> dict[str, int]:
        """使用调用方连接插入或更新多个节点。"""
        node_key_to_id: dict[str, int] = {}
        for node in nodes:
            node_key_to_id[node.node_key] = await self._upsert_node(db, node, now)
        return node_key_to_id

    async def _upsert_node(
        self,
        db: aiosqlite.Connection,
        node: GraphNode,
        now: str,
    ) -> int:
        """使用调用方连接插入或更新单个节点。"""
        cursor = await db.execute(
            """
            INSERT INTO graph_nodes(
                node_key, node_type, node_value, canonical_value,
                metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_key) DO UPDATE SET
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
            ),
        )
        cursor = await db.execute(
            "SELECT id FROM graph_nodes WHERE node_key = ?",
            (node.node_key,),
        )
        row = await cursor.fetchone()
        return int(row[0])

    async def add_edge(
        self,
        edge: GraphEdge,
        node_key_to_id: dict[str, int],
    ) -> int:
        """插入或更新单条图边，并返回其标识符。

        使用 `semantic_edge_key` 支持跨记忆合并：
        当相同语义边已存在于其他记忆中时，会通过 EMA 更新置信度，
        同时累积权重作为额外证据。
        """
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
            )
            await db.commit()
            return edge_id

    async def add_edges(
        self,
        edges: list[GraphEdge],
        node_key_to_id: dict[str, int],
    ) -> dict[str, int]:
        """在单个事务中插入或更新多条边。"""
        if not edges:
            return {}

        now = self._now_iso()
        async with self._connect() as db:
            edge_key_to_id = await self._add_edges(
                db,
                edges,
                node_key_to_id,
                now,
            )
            await db.commit()
        return edge_key_to_id

    async def _add_edges(
        self,
        db: aiosqlite.Connection,
        edges: list[GraphEdge],
        node_key_to_id: dict[str, int],
        now: str,
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
            )
        return edge_key_to_id

    async def _add_edge(
        self,
        db: aiosqlite.Connection,
        edge: GraphEdge,
        source_node_id: int,
        target_node_id: int,
        now: str,
    ) -> int:
        """使用调用方连接插入或合并单条边。"""
        # 先做精确键匹配（同一记忆、同一条边）
        cursor = await db.execute(
            "SELECT id FROM graph_edges WHERE edge_key = ?",
            (edge.edge_key,),
        )
        row = await cursor.fetchone()
        if row:
            await db.execute(
                """
                UPDATE graph_edges
                SET weight = ?, confidence = ?, status = ?, metadata = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    edge.weight,
                    edge.confidence,
                    edge.status,
                    self._to_json(edge.metadata),
                    now,
                    row[0],
                ),
            )
            return int(row[0])

        # 跨记忆语义合并：查找相同节点之间的同类关系
        semantic_cursor = await db.execute(
            """
            SELECT id, confidence, weight FROM graph_edges
            WHERE source_node_id = ? AND target_node_id = ?
              AND relation_type = ?
            ORDER BY id ASC LIMIT 1
            """,
            (source_node_id, target_node_id, edge.relation_type),
        )
        semantic_row = await semantic_cursor.fetchone()

        if semantic_row:
            existing_id = int(semantic_row[0])
            old_conf = float(semantic_row[1] or 0.8)
            old_weight = float(semantic_row[2] or 1.0)
            merged_confidence = old_conf * 0.7 + edge.confidence * 0.3
            merged_weight = old_weight + 0.15
            await db.execute(
                """
                UPDATE graph_edges
                SET confidence = ?, weight = ?, updated_at = ?
                WHERE id = ?
                """,
                (merged_confidence, merged_weight, now, existing_id),
            )
            return existing_id

        cursor = await db.execute(
            """
            INSERT INTO graph_edges(
                edge_key, source_node_id, target_node_id, relation_type,
                source_memory_id, weight, confidence, status,
                metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge.edge_key,
                source_node_id,
                target_node_id,
                edge.relation_type,
                edge.source_memory_id,
                edge.weight,
                edge.confidence,
                edge.status,
                self._to_json(edge.metadata),
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    async def add_entry(
        self,
        entry: GraphEntry,
        node_key_to_id: dict[str, int],
        edge_id: int | None = None,
    ) -> int:
        """插入或更新可搜索的图条目。"""
        now = self._now_iso()
        async with self._connect() as db:
            entry_id = await self._add_entry(db, entry, node_key_to_id, edge_id, now)
            await db.commit()
            return entry_id

    async def add_entries(
        self,
        entries: list[GraphEntry],
        node_key_to_id: dict[str, int],
        edge_key_to_id: dict[str, int],
    ) -> list[int]:
        """在单个事务中插入或更新可搜索的图条目。"""
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
                await self._add_entry(db, entry, node_key_to_id, edge_id, now)
            )
        return entry_ids

    async def _add_entry(
        self,
        db: aiosqlite.Connection,
        entry: GraphEntry,
        node_key_to_id: dict[str, int],
        edge_id: int | None,
        now: str,
    ) -> int:
        """使用调用方连接插入或更新单个可搜索图条目。"""
        cursor = await db.execute(
            "SELECT id FROM graph_entries WHERE entry_key = ?",
            (entry.entry_key,),
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
                    edge_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
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
        self, entry_id: int, vector_doc_id: int
    ) -> None:
        """持久化单个图条目的向量存储标识符。"""
        async with self._connect() as db:
            await db.execute(
                "UPDATE graph_entries SET vector_doc_id = ?, updated_at = ? WHERE id = ?",
                (vector_doc_id, self._now_iso(), entry_id),
            )
            await db.commit()

    async def update_entry_vector_doc_ids(
        self,
        entry_vector_doc_ids: dict[int, int],
    ) -> None:
        """在单个事务中持久化多个图条目的向量存储标识符。"""
        if not entry_vector_doc_ids:
            return

        now = self._now_iso()
        async with self._connect() as db:
            await db.executemany(
                "UPDATE graph_entries SET vector_doc_id = ?, updated_at = ? WHERE id = ?",
                [
                    (vector_doc_id, now, entry_id)
                    for entry_id, vector_doc_id in entry_vector_doc_ids.items()
                ],
            )
            await db.commit()
