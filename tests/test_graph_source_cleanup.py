"""源记忆图产物回收：跨 revision 与 legacy 行的重索引和删除契约。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import aiosqlite
import pytest

from core.features.memory.application.graph_memory_manager import GraphMemoryManager
from core.features.memory.graph.domain.models import GraphBoundary
from core.features.memory.graph.infrastructure.graph_store import GraphStore
from core.features.recall.processors.graph_extractor import GraphExtractor
from core.features.retrieval.graph_vector_retriever import GraphVectorRetriever
from tests.fact_evidence_helpers import fact_evidence, source_evidence

_LEGACY_SCHEMAS = (
    """
    CREATE TABLE graph_nodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        node_key TEXT NOT NULL UNIQUE,
        node_type TEXT NOT NULL,
        node_value TEXT NOT NULL,
        canonical_value TEXT NOT NULL,
        metadata TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE graph_edges (
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
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE graph_entries (
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
        updated_at TEXT NOT NULL
    )
    """,
)


class _VectorDocuments:
    """按 metadata 过滤的向量文档列表，复刻宿主文档存储的最小契约。"""

    def __init__(self, owner: _VectorBackend) -> None:
        self._owner = owner

    async def get_documents(
        self,
        *,
        metadata_filters: dict[str, Any] | None = None,
        ids: Sequence[int] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        for key, (text, metadata) in self._owner.records.items():
            if ids is not None and key not in {str(item) for item in ids}:
                continue
            if metadata_filters and any(
                metadata.get(field) != value
                for field, value in metadata_filters.items()
            ):
                continue
            documents.append({"doc_id": key, "text": text, "metadata": dict(metadata)})
        return documents[offset : offset + limit]


class _VectorBackend:
    """内存图向量后端：批量插入、按文档删除与失败注入。"""

    def __init__(self) -> None:
        self.records: dict[str, tuple[str, dict[str, Any]]] = {}
        self.document_storage = _VectorDocuments(self)
        self.fail_delete = False
        self._next_id = 1000

    async def insert_batch(
        self,
        *,
        contents: list[str],
        metadatas: list[dict[str, Any]],
        batch_size: int,
    ) -> list[int]:
        ids: list[int] = []
        for content, metadata in zip(contents, metadatas, strict=True):
            key = f"vec-{self._next_id}"
            self.records[key] = (content, dict(metadata))
            ids.append(self._next_id)
            self._next_id += 1
        return ids

    async def delete(self, doc_id: str) -> bool:
        if self.fail_delete:
            raise RuntimeError("vector_delete_failed")
        if doc_id not in self.records:
            return False
        del self.records[doc_id]
        return True


async def _manager(
    tmp_db_path: str,
) -> tuple[GraphMemoryManager, GraphStore, _VectorBackend]:
    """构造真实 GraphStore + 真实图向量检索器（内存后端）的 manager。"""

    store = GraphStore(tmp_db_path)
    await store.initialize()
    backend = _VectorBackend()
    manager = GraphMemoryManager(store, GraphVectorRetriever(backend), GraphExtractor())
    return manager, store, backend


async def _seed_legacy_graph(db_path: str, source_memory_id: int) -> None:
    """写入边界字段出现之前的 schema 与行，供 initialize() 迁移为 legacy 行。"""

    db = await aiosqlite.connect(db_path)
    try:
        for schema in _LEGACY_SCHEMAS:
            await db.execute(schema)
        for node_id, key, value in (
            (1, "person:alice", "Alice"),
            (2, "fact:coffee", "coffee"),
            (3, "fact:orphan", "orphan"),
        ):
            await db.execute(
                """
                INSERT INTO graph_nodes(
                    id, node_key, node_type, node_value, canonical_value,
                    metadata, created_at, updated_at
                ) VALUES (?, ?, 'entity', ?, ?, '{}', 'legacy', 'legacy')
                """,
                (node_id, key, value, value.casefold()),
            )
        await db.execute(
            """
            INSERT INTO graph_edges(
                id, edge_key, source_node_id, target_node_id, relation_type,
                source_memory_id, weight, confidence, status, metadata,
                created_at, updated_at
            ) VALUES (11, 'legacy-edge', 1, 2, 'likes', ?, 1.0, 0.8, 'active',
                      '{}', 'legacy', 'legacy')
            """,
            (source_memory_id,),
        )
        await db.execute(
            """
            INSERT INTO graph_entries(
                id, entry_key, source_memory_id, entry_type, relation_type,
                content, metadata, edge_id, vector_doc_id, created_at, updated_at
            ) VALUES (21, 'legacy-entry', ?, 'fact', 'likes', 'Alice likes coffee',
                      '{}', 11, 77, 'legacy', 'legacy')
            """,
            (source_memory_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def _legacy_manager(
    tmp_db_path: str, source_memory_id: int
) -> tuple[GraphMemoryManager, GraphStore, _VectorBackend]:
    """迁移 legacy 图库后构造 manager，并补上 legacy 条目关联与 FTS 行。"""

    await _seed_legacy_graph(tmp_db_path, source_memory_id)
    store = GraphStore(tmp_db_path)
    await store.initialize()
    async with store._connect() as db:
        await db.execute(
            "INSERT INTO graph_entry_nodes(entry_id, node_id) VALUES (21, 1)"
        )
        await db.execute(
            "INSERT INTO memora_graph_entries_fts(entry_id, content) VALUES (21, ?)",
            ("Alice likes coffee",),
        )
        await db.commit()
    backend = _VectorBackend()
    backend.records["legacy-vector"] = (
        "Alice likes coffee",
        {"source_memory_id": source_memory_id},
    )
    manager = GraphMemoryManager(store, GraphVectorRetriever(backend), GraphExtractor())
    return manager, store, backend


async def _source(
    store: GraphStore,
    memory_id: int = 1,
    *,
    revision: str = "r1",
    scope: str = "scope-a",
    facts: Sequence[str] = ("Alice likes coffee", "Alice likes tea"),
) -> GraphBoundary:
    """写入一条 canonical 文档行并返回其边界。"""

    fact_list = list(facts)
    metadata = {
        "scope_key": scope,
        "privacy_level": "public",
        "key_facts": fact_list,
        "fact_source_evidence": fact_evidence(fact_list),
        "source_evidence": source_evidence("; ".join(fact_list)),
        "canonical_summary": "; ".join(fact_list),
        "topics": ["drink"],
        "participants": ["Alice"],
    }
    async with store._connect() as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS documents ("
            "id INTEGER PRIMARY KEY, text TEXT, metadata TEXT, "
            "created_at TEXT, updated_at TEXT)"
        )
        await db.execute(
            "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?)",
            (
                memory_id,
                metadata["canonical_summary"],
                json.dumps(metadata),
                revision,
                revision,
            ),
        )
        await db.commit()
    return GraphBoundary.from_metadata({**metadata, "revision_token": revision})


async def _entry_rows(store: GraphStore, memory_id: int) -> list[tuple[Any, ...]]:
    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT entry_key, vector_doc_id, scope_key, revision_token "
            "FROM graph_entries WHERE source_memory_id = ? ORDER BY id",
            (memory_id,),
        )
        return [tuple(row) for row in await cursor.fetchall()]


async def _node_keys(store: GraphStore) -> set[str]:
    async with store._connect() as db:
        cursor = await db.execute("SELECT node_key FROM graph_nodes")
        return {str(row[0]) for row in await cursor.fetchall()}


async def _source_node_keys(store: GraphStore, memory_id: int) -> set[str]:
    async with store._connect() as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT gn.node_key
            FROM graph_entry_nodes gen
            JOIN graph_nodes gn ON gn.id = gen.node_id
            JOIN graph_entries ge ON ge.id = gen.entry_id
            WHERE ge.source_memory_id = ?
            """,
            (memory_id,),
        )
        return {str(row[0]) for row in await cursor.fetchall()}


async def _revision_row_counts(
    store: GraphStore, revision: str
) -> tuple[int, int, int]:
    async with store._connect() as db:
        cursor = await db.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM graph_entries WHERE revision_token = :revision),
                (SELECT COUNT(*) FROM graph_edges WHERE revision_token = :revision),
                (SELECT COUNT(*) FROM graph_nodes WHERE revision_token = :revision)
            """,
            {"revision": revision},
        )
        row = await cursor.fetchone()
    assert row is not None
    return (int(row[0]), int(row[1]), int(row[2]))


@pytest.mark.asyncio
async def test_reindex_after_revision_change_reaps_previous_revision(tmp_db_path):
    """源记忆升到新 revision 后，旧 revision 的图行与向量都必须消失。"""

    manager, store, backend = await _manager(tmp_db_path)
    await _source(store, 1, revision="r1")
    await manager.index_memory(1, "", {})
    assert await _entry_rows(store, 1)
    assert backend.records

    await _source(store, 1, revision="r2")
    await manager.index_memory(1, "", {})

    rows = await _entry_rows(store, 1)
    assert rows
    assert {row[3] for row in rows} == {"r2"}
    assert backend.records
    r2_boundary = GraphBoundary("scope-a", "public", "r2")
    assert all(
        GraphBoundary.from_metadata(metadata) == r2_boundary
        for _, metadata in backend.records.values()
    )
    assert await _revision_row_counts(store, "r1") == (0, 0, 0)


@pytest.mark.asyncio
async def test_reindex_reaps_legacy_rows_without_boundary(tmp_db_path):
    """重索引必须同时回收 NULL 边界的 legacy 图行与向量。"""

    manager, store, backend = await _legacy_manager(tmp_db_path, 1)
    assert await _node_keys(store) == {"person:alice", "fact:coffee", "fact:orphan"}

    await _source(store, 1, revision="r2")
    await manager.index_memory(1, "", {})

    async with store._connect() as db:
        cursor = await db.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM graph_entries WHERE scope_key IS NULL),
                (SELECT COUNT(*) FROM graph_edges WHERE scope_key IS NULL),
                (SELECT COUNT(*) FROM graph_nodes WHERE scope_key IS NULL)
            """
        )
        legacy_counts = await cursor.fetchone()
    assert legacy_counts is not None
    assert (int(legacy_counts[0]), int(legacy_counts[1]), int(legacy_counts[2])) == (
        0,
        0,
        0,
    )
    assert {row[3] for row in await _entry_rows(store, 1)} == {"r2"}
    assert "legacy-vector" not in backend.records


@pytest.mark.asyncio
async def test_legacy_source_delete_reaps_rows_and_vectors(tmp_db_path):
    """删除 legacy 源记忆时，NULL 边界行、关联行、FTS 与向量一并回收。"""

    manager, store, backend = await _legacy_manager(tmp_db_path, 1)
    other_boundary = await _source(store, 2)
    await manager.index_memory(2, "", {})
    other_rows = await _entry_rows(store, 2)
    assert other_rows

    await manager.delete_memory(1)

    assert await _entry_rows(store, 1) == []
    assert "legacy-vector" not in backend.records
    assert await _entry_rows(store, 2) == other_rows
    assert await store.get_recent_memory_ids(boundary=other_boundary) == [2]
    async with store._connect() as db:
        cursor = await db.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM memora_graph_entries_fts WHERE entry_id = 21),
                (SELECT COUNT(*) FROM graph_entry_nodes WHERE entry_id = 21),
                (SELECT COUNT(*) FROM memora_graph_entries_fts)
            """
        )
        link_counts = await cursor.fetchone()
    assert link_counts is not None
    assert (int(link_counts[0]), int(link_counts[1])) == (0, 0)
    assert int(link_counts[2]) == len(other_rows)


@pytest.mark.asyncio
async def test_reap_keeps_other_sources_and_shared_nodes(tmp_db_path):
    """回收一条源记忆不得影响其他源记忆，共享节点必须保留。"""

    manager, store, backend = await _manager(tmp_db_path)
    boundary = await _source(store, 1)
    await _source(store, 2, facts=("Alice likes tea", "Alice lives in Shanghai"))
    await manager.index_memory(1, "", {})
    await manager.index_memory(2, "", {})

    memory_one_nodes = await _source_node_keys(store, 1)
    memory_two_nodes = await _source_node_keys(store, 2)
    assert memory_one_nodes & memory_two_nodes
    assert memory_one_nodes - memory_two_nodes

    await manager.delete_memory(1)

    remaining_nodes = await _node_keys(store)
    assert await _entry_rows(store, 1) == []
    assert await _entry_rows(store, 2)
    assert memory_two_nodes <= remaining_nodes
    assert (memory_one_nodes - memory_two_nodes).isdisjoint(remaining_nodes)
    assert await store.get_recent_memory_ids(boundary=boundary) == [2]
    assert all(
        metadata["source_memory_id"] == 2 for _, metadata in backend.records.values()
    )


@pytest.mark.asyncio
async def test_failed_vector_reap_keeps_graph_rows_for_retry(tmp_db_path):
    """向量回收失败时图行与其向量映射保留为清理证据，重试后完成回收。"""

    manager, store, backend = await _manager(tmp_db_path)
    boundary = await _source(store, 1)
    await manager.index_memory(1, "", {})
    rows_before = await _entry_rows(store, 1)
    assert rows_before
    assert all(row[1] is not None for row in rows_before)

    backend.fail_delete = True
    with pytest.raises(RuntimeError, match="vector_delete_failed"):
        await manager.delete_memory(1)

    assert backend.records
    assert await _entry_rows(store, 1) == rows_before
    assert await store.get_recent_memory_ids(boundary=boundary) == [1]

    backend.fail_delete = False
    await manager.delete_memory(1)

    assert backend.records == {}
    assert await _entry_rows(store, 1) == []
    assert await store.get_recent_memory_ids(boundary=boundary) == []


@pytest.mark.asyncio
async def test_reap_entries_verifies_source_and_keeps_other_sources():
    """向量回收按来源 ID 核对，legacy 无证明向量同源回收，其他来源不动。"""

    backend = _VectorBackend()
    retriever = GraphVectorRetriever(backend)
    backend.records.update(
        {
            "own-modern": (
                "own",
                {
                    "source_memory_id": 1,
                    "scope_key": "scope-a",
                    "privacy_level": "public",
                    "revision_token": "r1",
                },
            ),
            "own-legacy": ("legacy", {"source_memory_id": 1}),
            "other": (
                "other",
                {
                    "source_memory_id": 2,
                    "scope_key": "scope-a",
                    "privacy_level": "public",
                    "revision_token": "r1",
                },
            ),
            "unproven": ("unknown", {"note": "no source id"}),
        }
    )

    assert await retriever.reap_entries_for_memory(1) == 2

    assert set(backend.records) == {"other", "unproven"}


@pytest.mark.asyncio
async def test_list_residual_sources_reports_deleted_only(tmp_db_path):
    """已删来源枚举以 canonical 快照为准，覆盖条目与边两类图行。"""

    manager, store, _backend = await _manager(tmp_db_path)
    boundary = await _source(store, 1)
    await _source(store, 2, scope="scope-b")
    await manager.index_memory(1, "Alice likes coffee", {})
    await manager.index_memory(2, "Alice likes tea", {})

    assert await store.list_residual_source_memory_ids({1, 2}) == []
    assert await store.list_residual_source_memory_ids({1}) == [2]

    # 只剩边残留（条目行已删）的来源同样必须被枚举为已删来源。
    async with store._connect() as db:
        await db.execute("DELETE FROM graph_entries WHERE source_memory_id = 2")
        await db.commit()
    assert await store.list_residual_source_memory_ids({1}) == [2]
    assert boundary is not None


@pytest.mark.asyncio
async def test_list_unreferenced_vector_doc_ids_rechecks_references(tmp_db_path):
    """孤儿向量核对只放行当前图表不引用的候选。"""

    manager, store, backend = await _manager(tmp_db_path)
    await _source(store, 1)
    await manager.index_memory(1, "Alice likes coffee", {})
    referenced = [
        vector_doc_id for _key, vector_doc_id, _s, _r in await _entry_rows(store, 1)
    ]

    assert referenced
    assert await store.list_unreferenced_vector_doc_ids(referenced) == []
    assert await store.list_unreferenced_vector_doc_ids([999999]) == [999999]
    assert backend.records != {}

    # 删除条目后同一向量 ID 变成未被引用，允许清理。
    async with store._connect() as db:
        await db.execute("DELETE FROM graph_entries")
        await db.commit()
    assert await store.list_unreferenced_vector_doc_ids(referenced) == referenced
