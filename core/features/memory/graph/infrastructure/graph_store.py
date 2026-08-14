"""基于 SQLite 的图记忆存储。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain.models import GraphEdge, GraphEntry, GraphNode
from .graph_canvas import GraphCanvasMixin
from .graph_crud import GraphCRUDMixin
from .graph_delete import GraphDeleteMixin
from .graph_query import GraphQueryMixin
from .graph_subgraph import GraphSubgraphMixin


@dataclass(frozen=True, slots=True)
class GraphReplaceResult:
    """描述一次 SQLite 图产物原子替换的提交结果。"""

    entry_ids: list[int]


class GraphStore(
    GraphQueryMixin,
    GraphCanvasMixin,
    GraphSubgraphMixin,
    GraphCRUDMixin,
    GraphDeleteMixin,
):
    """持久化图节点、边和可搜索条目。"""

    _SQLITE_BATCH_SIZE = 500

    def __init__(self, db_path: str):
        """保存 SQLite 数据库路径，供各图存储混入类共享连接。"""
        self.db_path = db_path

    async def initialize(self) -> None:
        """创建图记忆层使用的数据表。"""
        async with self._connect() as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_key TEXT NOT NULL UNIQUE,
                    node_type TEXT NOT NULL,
                    node_value TEXT NOT NULL,
                    canonical_value TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    edge_key TEXT NOT NULL UNIQUE,
                    source_node_id INTEGER NOT NULL,
                    target_node_id INTEGER NOT NULL,
                    relation_type TEXT NOT NULL,
                    source_memory_id INTEGER NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    confidence REAL NOT NULL DEFAULT 0.8,
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(source_node_id) REFERENCES graph_nodes(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_node_id) REFERENCES graph_nodes(id) ON DELETE CASCADE
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_key TEXT NOT NULL UNIQUE,
                    source_memory_id INTEGER NOT NULL,
                    session_id TEXT,
                    persona_id TEXT,
                    entry_type TEXT NOT NULL,
                    relation_type TEXT,
                    content TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    edge_id INTEGER,
                    vector_doc_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(edge_id) REFERENCES graph_edges(id) ON DELETE CASCADE
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_entry_nodes (
                    entry_id INTEGER NOT NULL,
                    node_id INTEGER NOT NULL,
                    PRIMARY KEY(entry_id, node_id),
                    FOREIGN KEY(entry_id) REFERENCES graph_entries(id) ON DELETE CASCADE,
                    FOREIGN KEY(node_id) REFERENCES graph_nodes(id) ON DELETE CASCADE
                )
                """
            )
            await db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memora_graph_entries_fts
                USING fts5(content, entry_id UNINDEXED, tokenize='unicode61')
                """
            )
            await db.commit()
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_nodes_canonical ON graph_nodes(canonical_value)"
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_graph_edges_semantic
                ON graph_edges(source_node_id, target_node_id, relation_type)
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_edges_memory_id ON graph_edges(source_memory_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entries_memory_id ON graph_entries(source_memory_id)"
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_graph_entries_scope_latest
                ON graph_entries(session_id, persona_id, source_memory_id, id DESC)
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entries_session_id ON graph_entries(session_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entries_persona_id ON graph_entries(persona_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_entry_nodes_node ON graph_entry_nodes(node_id)"
            )
            await db.commit()

    async def replace_memory_graph(
        self,
        source_memory_id: int,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        entries: list[GraphEntry],
    ) -> GraphReplaceResult:
        """在一个 SQLite 事务中替换源记忆的全部结构化图产物。"""
        now = self._now_iso()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await self._delete_memory_rows(db, source_memory_id)
                node_key_to_id = await self._upsert_nodes(db, nodes, now)
                edge_key_to_id = await self._add_edges(
                    db,
                    edges,
                    node_key_to_id,
                    now,
                )
                entry_ids = await self._add_entries(
                    db,
                    entries,
                    node_key_to_id,
                    edge_key_to_id,
                    now,
                )
                await self._delete_orphan_nodes(db)
                await db.commit()
                return GraphReplaceResult(entry_ids=entry_ids)
            except BaseException:
                await db.rollback()
                raise

    @staticmethod
    def _chunked(items: list[int], size: int) -> list[list[int]]:
        """按固定大小把整数列表拆分为连续批次。"""
        return [items[index : index + size] for index in range(0, len(items), size)]

    async def get_graph_snapshot(
        self,
        session_id: str | None = None,
        persona_id: str | None = None,
        limit_memories: int = 12,
        limit_entries: int = 36,
        limit_nodes: int = 48,
        limit_edges: int = 72,
        *,
        full: bool = False,
    ) -> dict[str, Any]:
        """返回图概览；全量模式跳过记忆、条目、节点和边数量裁剪。"""
        if full:
            return await self._get_full_graph_snapshot(
                session_id=session_id,
                persona_id=persona_id,
            )
        memory_ids = await self.get_recent_memory_ids(
            limit=limit_memories,
            session_id=session_id,
            persona_id=persona_id,
        )
        return await self.get_subgraph_for_memories(
            memory_ids,
            limit_entries=limit_entries,
            limit_nodes=limit_nodes,
            limit_edges=limit_edges,
        )

    async def get_memory_entry_stats(self) -> dict[str, int]:
        """返回图存储计数，用于状态报告。"""
        async with self._connect() as db:
            node_cursor = await db.execute("SELECT COUNT(*) FROM graph_nodes")
            edge_cursor = await db.execute("SELECT COUNT(*) FROM graph_edges")
            entry_cursor = await db.execute("SELECT COUNT(*) FROM graph_entries")
            node_count_row = await node_cursor.fetchone()
            edge_count_row = await edge_cursor.fetchone()
            entry_count_row = await entry_cursor.fetchone()
        return {
            "graph_nodes": int(node_count_row[0]) if node_count_row else 0,
            "graph_edges": int(edge_count_row[0]) if edge_count_row else 0,
            "graph_entries": int(entry_count_row[0]) if entry_count_row else 0,
        }


__all__ = ["GraphReplaceResult", "GraphStore"]
