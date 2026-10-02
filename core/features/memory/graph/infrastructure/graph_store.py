"""基于 SQLite 的图记忆存储。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...domain.revision import memory_revision
from ..domain.models import GraphBoundary, GraphEdge, GraphEntry, GraphNode
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
        """Migrate only derived tables, preserving legacy rows as boundary-incomplete."""
        schemas = {
            "graph_nodes": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_key TEXT NOT NULL,
                node_type TEXT NOT NULL,
                node_value TEXT NOT NULL,
                canonical_value TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                scope_key TEXT, privacy_level TEXT, revision_token TEXT,
                UNIQUE(node_key, scope_key, privacy_level, revision_token)
            """,
            "graph_edges": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edge_key TEXT NOT NULL,
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
                scope_key TEXT, privacy_level TEXT, revision_token TEXT,
                semantic_edge_id INTEGER,
                evidence_kind TEXT,
                binding_key TEXT,
                UNIQUE(source_memory_id, source_node_id, target_node_id,
                       relation_type, scope_key, privacy_level, revision_token),
                FOREIGN KEY(source_node_id) REFERENCES graph_nodes(id) ON DELETE CASCADE,
                FOREIGN KEY(target_node_id) REFERENCES graph_nodes(id) ON DELETE CASCADE
            """,
            "graph_entries": """
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_key TEXT NOT NULL,
                source_memory_id INTEGER NOT NULL,
                session_id TEXT, persona_id TEXT,
                entry_type TEXT NOT NULL,
                relation_type TEXT,
                content TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                edge_id INTEGER,
                vector_doc_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                scope_key TEXT, privacy_level TEXT, revision_token TEXT,
                UNIQUE(entry_key, source_memory_id, scope_key, privacy_level, revision_token),
                FOREIGN KEY(edge_id) REFERENCES graph_edges(id) ON DELETE CASCADE
            """,
        }
        async with self._connect() as db:
            # SQLite's table replacement must not cascade into the preserved links.
            await db.execute("PRAGMA foreign_keys = OFF")
            await db.execute("BEGIN IMMEDIATE")
            try:
                for table, schema in schemas.items():
                    cursor = await db.execute(f"PRAGMA table_info({table})")
                    columns = [str(row[1]) for row in await cursor.fetchall()]
                    if columns and not {
                        "scope_key",
                        "privacy_level",
                        "revision_token",
                    }.issubset(columns):
                        await db.execute(f"CREATE TABLE {table}_boundary ({schema})")
                        # Column names come only from the existing SQLite schema.
                        names = ", ".join(
                            '"' + name.replace('"', '""') + '"' for name in columns
                        )
                        await db.execute(
                            f"INSERT INTO {table}_boundary ({names}) SELECT {names} FROM {table}"
                        )
                        await db.execute(f"DROP TABLE {table}")
                        await db.execute(
                            f"ALTER TABLE {table}_boundary RENAME TO {table}"
                        )
                    else:
                        await db.execute(
                            f"CREATE TABLE IF NOT EXISTS {table} ({schema})"
                        )
                    await db.execute(
                        f"CREATE INDEX IF NOT EXISTS idx_{table}_boundary "
                        f"ON {table}(scope_key, privacy_level, revision_token)"
                    )
                    for operation in ("INSERT", "UPDATE"):
                        await db.execute(
                            f"""CREATE TRIGGER IF NOT EXISTS {table}_boundary_{operation.lower()}
                            BEFORE {operation} ON {table}
                            WHEN NEW.scope_key IS NULL OR trim(NEW.scope_key) = ''
                              OR NEW.privacy_level IS NULL
                              OR NEW.privacy_level NOT IN ('public', 'shared', 'confidential')
                              OR NEW.revision_token IS NULL OR trim(NEW.revision_token) = ''
                            BEGIN SELECT RAISE(ABORT, 'graph_boundary_required'); END"""
                        )
                # graph_edges 行是「某来源对某语义边」的 evidence；语义边本体按
                # scope/privacy + 端点 node_key + 关系类型唯一，跨来源、跨 revision
                # 共享。旧库缺少 evidence 列时增量补列，旧行 semantic_edge_id 为
                # NULL，读取路径不把它们当作有效关系，等待重建写入新 evidence。
                cursor = await db.execute("PRAGMA table_info(graph_edges)")
                edge_columns = {str(row[1]) for row in await cursor.fetchall()}
                for column, ddl in (
                    ("semantic_edge_id", "INTEGER"),
                    ("evidence_kind", "TEXT"),
                    ("binding_key", "TEXT"),
                ):
                    if column not in edge_columns:
                        await db.execute(
                            f"ALTER TABLE graph_edges ADD COLUMN {column} {ddl}"
                        )
                await db.execute(
                    """CREATE TABLE IF NOT EXISTS graph_semantic_edges (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        semantic_key TEXT NOT NULL,
                        scope_key TEXT NOT NULL,
                        privacy_level TEXT NOT NULL,
                        source_node_key TEXT NOT NULL,
                        target_node_key TEXT NOT NULL,
                        relation_type TEXT NOT NULL,
                        weight REAL NOT NULL DEFAULT 1.0,
                        confidence REAL NOT NULL DEFAULT 0.8,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(scope_key, privacy_level, semantic_key)
                    )"""
                )
                await db.execute(
                    """CREATE TABLE IF NOT EXISTS graph_entry_nodes (
                        entry_id INTEGER NOT NULL, node_id INTEGER NOT NULL,
                        PRIMARY KEY(entry_id, node_id),
                        FOREIGN KEY(entry_id) REFERENCES graph_entries(id) ON DELETE CASCADE,
                        FOREIGN KEY(node_id) REFERENCES graph_nodes(id) ON DELETE CASCADE
                    )"""
                )
                await db.execute(
                    """CREATE VIRTUAL TABLE IF NOT EXISTS memora_graph_entries_fts
                    USING fts5(content, entry_id UNINDEXED, tokenize='unicode61')"""
                )
                indexes = {
                    "idx_graph_nodes_canonical": "graph_nodes(canonical_value)",
                    "idx_graph_edges_memory_id": "graph_edges(source_memory_id, scope_key, privacy_level, revision_token)",
                    "idx_graph_edges_semantic": "graph_edges(semantic_edge_id)",
                    "idx_graph_entries_memory_id": "graph_entries(source_memory_id, scope_key, privacy_level, revision_token)",
                    "idx_graph_entries_scope_latest": "graph_entries(session_id, persona_id, source_memory_id, id DESC)",
                    "idx_graph_entries_session_id": "graph_entries(session_id)",
                    "idx_graph_entries_persona_id": "graph_entries(persona_id)",
                    "idx_graph_entry_nodes_node": "graph_entry_nodes(node_id)",
                }
                for name, target in indexes.items():
                    await db.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
            finally:
                await db.execute("PRAGMA foreign_keys = ON")

    async def load_source_memory(
        self, source_memory_id: int
    ) -> tuple[str, dict[str, Any]]:
        """Read the canonical source; graph metadata is never its own authority."""
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT text, metadata, created_at, updated_at FROM documents WHERE id = ?",
                (source_memory_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise ValueError("graph_boundary_required")
        metadata = dict(self._from_json(row[1]))
        revision = memory_revision({"created_at": row[2], "updated_at": row[3]})
        metadata["revision_token"] = revision
        try:
            GraphBoundary.from_metadata(metadata)
        except ValueError as error:
            # canonical 边界已失效：来源不可派生，由调用方按来源级清理处理。
            raise ValueError("graph_source_boundary_invalid") from error
        return str(row[0]), metadata

    async def replace_memory_graph(
        self,
        source_memory_id: int,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        entries: list[GraphEntry],
        *,
        boundary: GraphBoundary,
    ) -> GraphReplaceResult:
        """在一个 SQLite 事务中替换源记忆的全部结构化图产物。

        事务内先按 canonical 源记忆回收该来源全部 revision 与 legacy 的 entry
        和 edge evidence，再写入新边界产物；共享语义边只在事务末尾按 evidence
        重新汇总：仍被其他来源（或本次新 evidence）支持的语义边保留同一 ID，
        最后一条 evidence 消失时才回收。其他来源的 evidence 不受影响。
        """
        GraphBoundary.require(boundary)
        if any(
            item.source_memory_id != source_memory_id for item in (*edges, *entries)
        ):
            raise ValueError("graph_boundary_mismatch")
        now = self._now_iso()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                affected_semantic_ids: set[int] = set()
                await self._delete_memories_rows(
                    db,
                    [source_memory_id],
                    affected_semantic_ids=affected_semantic_ids,
                )
                node_key_to_id = await self._upsert_nodes(
                    db, nodes, now, boundary=boundary
                )
                edge_key_to_id = await self._add_edges(
                    db,
                    edges,
                    node_key_to_id,
                    now,
                    boundary=boundary,
                )
                entry_ids = await self._add_entries(
                    db,
                    entries,
                    node_key_to_id,
                    edge_key_to_id,
                    now,
                    boundary=boundary,
                )
                await self._refresh_semantic_edges(db, affected_semantic_ids, now)
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
        boundary: GraphBoundary,
        full: bool = False,
    ) -> dict[str, Any]:
        """返回图概览；全量模式跳过记忆、条目、节点和边数量裁剪。"""
        GraphBoundary.require(boundary)
        if full:
            return await self._get_full_graph_snapshot(
                session_id=session_id,
                persona_id=persona_id,
                boundary=boundary,
            )
        memory_ids = await self.get_recent_memory_ids(
            limit=limit_memories,
            session_id=session_id,
            persona_id=persona_id,
            boundary=boundary,
        )
        return await self.get_subgraph_for_memories(
            memory_ids,
            boundary=boundary,
            limit_entries=limit_entries,
            limit_nodes=limit_nodes,
            limit_edges=limit_edges,
        )

    async def get_memory_entry_stats(self) -> dict[str, int]:
        """返回图存储计数，用于状态报告。

        ``graph_edges`` 统计仍有有效 evidence 的语义边：多来源共享的一条关系只
        计一次，没有 evidence 的 legacy 边行不计入。
        """
        async with self._connect() as db:
            node_cursor = await db.execute("SELECT COUNT(*) FROM graph_nodes")
            edge_cursor = await db.execute(
                """SELECT COUNT(*) FROM graph_semantic_edges semantic
                WHERE semantic.status = 'active' AND EXISTS (
                    SELECT 1 FROM graph_edges evidence
                    WHERE evidence.semantic_edge_id = semantic.id
                      AND evidence.status = 'active'
                )"""
            )
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
