"""Issue 88：多来源 evidence、最后来源回收与读取门回归。

全部存储断言使用真实 SQLite GraphStore；向量平面使用内存替身，只用于观察
来源级清理是否越界。
"""

from __future__ import annotations

import json
from typing import Any, cast

import aiosqlite
import pytest

from core.features.memory.application.graph_memory_manager import (
    GraphMemoryManager,
    GraphSourceIneligibleError,
)
from core.features.memory.graph.domain.models import (
    GraphBoundary,
    GraphEdge,
    GraphNode,
    GraphQueryScope,
)
from core.features.memory.graph.infrastructure.graph_store import GraphStore
from core.features.recall.processors.graph_extractor import GraphExtractor
from tests.fact_evidence_helpers import fact_evidence

SCOPE = "scope-a"
FACT = "Alice 喜欢咖啡"
TOPIC = "饮品"


class _Vectors:
    """按来源记录的内存图向量替身。"""

    def __init__(self) -> None:
        self.records: dict[int, tuple[str, dict[str, Any]]] = {}
        self.next_id = 1

    async def add_entry(
        self, content: str, metadata: dict[str, Any], *, boundary: GraphBoundary
    ) -> int:
        vector_id = self.next_id
        self.next_id += 1
        self.records[vector_id] = (content, {**metadata, **boundary.as_params()})
        return vector_id

    async def reap_entries_for_memory(self, memory_id: int) -> int:
        doomed = [
            key
            for key, (_, metadata) in self.records.items()
            if metadata.get("source_memory_id") == memory_id
        ]
        for key in doomed:
            del self.records[key]
        return len(doomed)

    def sources(self) -> set[int]:
        return {int(meta["source_memory_id"]) for _, meta in self.records.values()}


def _metadata(fact: str = FACT, **overrides: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "scope_key": SCOPE,
        "privacy_level": "public",
        "key_facts": [fact],
        "fact_source_evidence": fact_evidence([fact]),
        "topics": [TOPIC, "其他主题"],
        "participants": ["Alice", "Bob"],
        "fact_bindings": [
            {"fact_index": 0, "target": TOPIC, "target_type": "topic"},
        ],
    }
    metadata.update(overrides)
    return metadata


async def _write_source(
    store: GraphStore,
    memory_id: int,
    *,
    revision: str = "r1",
    fact: str = FACT,
    **overrides: Any,
) -> GraphBoundary:
    metadata = _metadata(fact, **overrides)
    async with store._connect() as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY "
            "AUTOINCREMENT, text TEXT, metadata TEXT, created_at TEXT, "
            "updated_at TEXT)"
        )
        await db.execute(
            "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?)",
            (memory_id, fact, json.dumps(metadata), revision, revision),
        )
        await db.commit()
    return GraphBoundary(SCOPE, "public", revision)


async def _delete_source(store: GraphStore, memory_id: int) -> None:
    async with store._connect() as db:
        await db.execute("DELETE FROM documents WHERE id = ?", (memory_id,))
        await db.commit()


async def _setup(tmp_db_path: str) -> tuple[GraphMemoryManager, GraphStore, _Vectors]:
    store = GraphStore(tmp_db_path)
    await store.initialize()
    vectors = _Vectors()
    manager = GraphMemoryManager(store, cast(Any, vectors), GraphExtractor())
    return manager, store, vectors


async def _semantic_rows(store: GraphStore) -> list[tuple[int, str]]:
    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT id, semantic_key FROM graph_semantic_edges ORDER BY id"
        )
        return [(int(row[0]), str(row[1])) for row in await cursor.fetchall()]


async def _evidence_rows(store: GraphStore) -> list[tuple[int, int, str]]:
    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT semantic_edge_id, source_memory_id, revision_token "
            "FROM graph_edges ORDER BY source_memory_id, revision_token"
        )
        return [
            (int(row[0]), int(row[1]), str(row[2])) for row in await cursor.fetchall()
        ]


async def _neighbors(store: GraphStore, node_key: str) -> list[str]:
    scope = GraphQueryScope(SCOPE, "public")
    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT id FROM graph_nodes WHERE node_key = ?", (node_key,)
        )
        node_ids = [int(row[0]) for row in await cursor.fetchall()]
    neighbor_ids = await store.get_neighbor_node_ids(node_ids, 20, query_scope=scope)
    if not neighbor_ids:
        return []
    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT DISTINCT node_key FROM graph_nodes WHERE id IN "
            "(SELECT value FROM json_each(?))",
            (json.dumps(neighbor_ids),),
        )
        return sorted(str(row[0]) for row in await cursor.fetchall())


# ---------------------------------------------------------------------------
# no-cartesian：真实 SQLite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_source_persists_only_bound_relations(tmp_db_path: str) -> None:
    manager, store, _ = await _setup(tmp_db_path)
    facts = [FACT, "Bob 周末去爬山"]
    await _write_source(
        store,
        1,
        fact=facts[0],
        key_facts=facts,
        fact_source_evidence=fact_evidence(facts),
        topics=[TOPIC, "户外"],
        participants=["Alice", "Bob"],
        fact_bindings=[{"fact_index": 1, "target": "户外", "target_type": "topic"}],
    )
    async with store._connect() as db:
        await db.execute(
            "UPDATE documents SET text = ? WHERE id = 1", ("；".join(facts),)
        )
        await db.commit()

    await manager.index_memory(1, "", {})

    assert [key for _, key in await _semantic_rows(store)] == [
        "topic:户外|describes|fact:Bob 周末去爬山"
    ]
    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT relation_type FROM graph_edges WHERE relation_type IN "
            "('co_occurs_with', 'mentioned_in')"
        )
        assert await cursor.fetchall() == []


# ---------------------------------------------------------------------------
# 多来源共享语义边与最后 evidence 回收
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_sources_share_one_semantic_edge_with_two_evidence(
    tmp_db_path: str,
) -> None:
    manager, store, vectors = await _setup(tmp_db_path)
    await _write_source(store, 1)
    await _write_source(store, 2)

    await manager.index_memory(1, "", {})
    await manager.index_memory(2, "", {})

    semantic = await _semantic_rows(store)
    assert len(semantic) == 1
    semantic_id = semantic[0][0]
    assert await _evidence_rows(store) == [
        (semantic_id, 1, "r1"),
        (semantic_id, 2, "r1"),
    ]
    assert (await store.get_memory_entry_stats())["graph_edges"] == 1

    await manager.delete_memory(1)

    assert await _semantic_rows(store) == semantic
    assert await _evidence_rows(store) == [(semantic_id, 2, "r1")]
    assert await _neighbors(store, f"topic:{TOPIC}") == [f"fact:{FACT}"]
    assert vectors.sources() == {2}

    await manager.delete_memory(2)

    assert await _semantic_rows(store) == []
    assert await _evidence_rows(store) == []
    assert await _neighbors(store, f"topic:{TOPIC}") == []
    assert (await store.get_memory_entry_stats())["graph_edges"] == 0


@pytest.mark.asyncio
async def test_neighbor_deduplicates_canonical_keys_across_revisions(
    tmp_db_path: str,
) -> None:
    manager, store, _ = await _setup(tmp_db_path)
    await _write_source(store, 1, revision="r1")
    await _write_source(store, 2, revision="r2")
    await manager.index_memory(1, "", {})
    await manager.index_memory(2, "", {})

    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT id FROM graph_nodes WHERE node_key = ? ORDER BY revision_token",
            (f"topic:{TOPIC}",),
        )
        topic_ids = [int(row[0]) for row in await cursor.fetchall()]
    assert len(topic_ids) == 2

    scope = GraphQueryScope(SCOPE, "public")
    neighbor_ids = await store.get_neighbor_node_ids(
        [topic_ids[0]], limit=10, query_scope=scope
    )
    assert len(neighbor_ids) == 1

    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT node_key FROM graph_nodes WHERE id = ?", (neighbor_ids[0],)
        )
        row = await cursor.fetchone()
    assert row is not None and row[0] == f"fact:{FACT}"

    entries = await store.get_entries_for_node_ids(
        neighbor_ids, limit=20, query_scope=scope
    )
    assert {int(entry["source_memory_id"]) for entry in entries} == {1, 2}


@pytest.mark.asyncio
async def test_neighbor_scope_expands_canonical_middle_node_across_revisions(
    tmp_db_path: str,
) -> None:
    store = GraphStore(tmp_db_path)
    await store.initialize()
    boundary_r1 = GraphBoundary(SCOPE, "public", "r1")
    boundary_r2 = GraphBoundary(SCOPE, "public", "r2")
    nodes_r1 = await store.upsert_nodes(
        [
            GraphNode("entity", "起点", "起点"),
            GraphNode("entity", "中间", "中间"),
        ],
        boundary=boundary_r1,
    )
    nodes_r2 = await store.upsert_nodes(
        [
            GraphNode("entity", "中间", "中间"),
            GraphNode("entity", "终点", "终点"),
        ],
        boundary=boundary_r2,
    )
    await store.add_edge(
        GraphEdge("entity:起点", "entity:中间", "relates_to", 1),
        nodes_r1,
        boundary=boundary_r1,
    )
    await store.add_edge(
        GraphEdge("entity:中间", "entity:终点", "relates_to", 2),
        nodes_r2,
        boundary=boundary_r2,
    )

    scope = GraphQueryScope(SCOPE, "public")
    middle = await store.get_neighbor_node_ids(
        [nodes_r1["entity:起点"]], limit=10, query_scope=scope
    )
    assert len(middle) == 1
    end = await store.get_neighbor_node_ids(middle, limit=10, query_scope=scope)
    assert nodes_r2["entity:终点"] in end


@pytest.mark.asyncio
async def test_second_hop_excludes_matched_canonical_key_across_revisions(
    tmp_db_path: str,
) -> None:
    store = GraphStore(tmp_db_path)
    await store.initialize()
    nodes: dict[str, dict[str, int]] = {}
    for memory_id, revision in ((1, "r1"), (2, "r2")):
        boundary = GraphBoundary(SCOPE, "public", revision)
        nodes[revision] = await store.upsert_nodes(
            [GraphNode("entity", "甲", "甲"), GraphNode("entity", "乙", "乙")],
            boundary=boundary,
        )
        await store.add_edge(
            GraphEdge("entity:甲", "entity:乙", "relates_to", memory_id),
            nodes[revision],
            boundary=boundary,
        )

    scope = GraphQueryScope(SCOPE, "public")
    matched = [nodes["r2"]["entity:甲"]]
    first_hop = await store.get_neighbor_node_ids(matched, 10, query_scope=scope)
    assert len(first_hop) == 1
    assert (
        await store.get_neighbor_node_ids(
            first_hop, 10, query_scope=scope, exclude_node_ids=matched
        )
        == []
    )


@pytest.mark.asyncio
async def test_batch_delete_and_residue_reap_keep_other_evidence(
    tmp_db_path: str,
) -> None:
    manager, store, _ = await _setup(tmp_db_path)
    for memory_id in (1, 2, 3):
        await _write_source(store, memory_id)
        await manager.index_memory(memory_id, "", {})
    semantic_id = (await _semantic_rows(store))[0][0]

    await manager.batch_delete_memories([1, 2])

    assert await _evidence_rows(store) == [(semantic_id, 3, "r1")]
    assert len(await _semantic_rows(store)) == 1

    await store.reap_source_graphs([3])

    assert await _semantic_rows(store) == []


@pytest.mark.asyncio
async def test_revision_update_replaces_only_own_evidence(tmp_db_path: str) -> None:
    manager, store, _ = await _setup(tmp_db_path)
    await _write_source(store, 1)
    await _write_source(store, 2)
    await manager.index_memory(1, "", {})
    await manager.index_memory(2, "", {})
    semantic_id = (await _semantic_rows(store))[0][0]

    await _write_source(store, 1, revision="r2")
    await manager.index_memory(1, "", {})

    assert await _evidence_rows(store) == [
        (semantic_id, 1, "r2"),
        (semantic_id, 2, "r1"),
    ]

    # 来源 1 改写为不再支持该关系的事实：只移除自身 evidence。
    await _write_source(
        store,
        1,
        revision="r3",
        fact="Alice 喜欢茶",
        fact_bindings=[],
    )
    await manager.index_memory(1, "", {})

    assert await _evidence_rows(store) == [(semantic_id, 2, "r1")]


@pytest.mark.asyncio
async def test_archive_reaps_own_evidence_and_restore_rebuilds_current(
    tmp_db_path: str,
) -> None:
    manager, store, vectors = await _setup(tmp_db_path)
    await _write_source(store, 1)
    await _write_source(store, 2)
    await manager.index_memory(1, "", {})
    await manager.index_memory(2, "", {})
    semantic_id = (await _semantic_rows(store))[0][0]

    await _write_source(store, 1, revision="r2", memory_status="archived")
    with pytest.raises(GraphSourceIneligibleError, match="graph_source_not_recallable"):
        await manager.index_memory(1, "", {})

    assert await _evidence_rows(store) == [(semantic_id, 2, "r1")]
    assert vectors.sources() == {2}

    # 恢复后按当前 canonical/binding 重新写入：当前 binding 已不再绑定主题，
    # 不复活归档前的旧 evidence。
    await _write_source(store, 1, revision="r3", fact_bindings=[])
    await manager.index_memory(1, "", {})

    assert await _evidence_rows(store) == [(semantic_id, 2, "r1")]
    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM graph_entries WHERE source_memory_id = 1 "
            "AND revision_token = 'r3'"
        )
        row = await cursor.fetchone()
    assert row is not None and int(row[0]) > 0


@pytest.mark.asyncio
async def test_failed_replace_rolls_back_without_touching_shared_edge(
    tmp_db_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, store, _ = await _setup(tmp_db_path)
    await _write_source(store, 1)
    await _write_source(store, 2)
    await manager.index_memory(1, "", {})
    await manager.index_memory(2, "", {})
    before_semantic = await _semantic_rows(store)
    before_evidence = await _evidence_rows(store)

    async def fail_entries(*_args: Any, **_kwargs: Any) -> list[int]:
        raise RuntimeError("entry_write_failed")

    monkeypatch.setattr(store, "_add_entries", fail_entries)
    await _write_source(store, 1, revision="r2")
    with pytest.raises(RuntimeError, match="entry_write_failed"):
        await manager.index_memory(1, "", {})

    assert await _semantic_rows(store) == before_semantic
    assert await _evidence_rows(store) == before_evidence


@pytest.mark.asyncio
async def test_scope_partitions_semantic_edges(tmp_db_path: str) -> None:
    manager, store, _ = await _setup(tmp_db_path)
    await _write_source(store, 1)
    await _write_source(store, 2, scope_key="scope-b")

    await manager.index_memory(1, "", {})
    await manager.index_memory(2, "", {})

    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT scope_key FROM graph_semantic_edges ORDER BY scope_key"
        )
        scopes = [str(row[0]) for row in await cursor.fetchall()]
    assert scopes == ["scope-a", "scope-b"]
    other = GraphQueryScope("scope-b", "public")
    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT id FROM graph_nodes WHERE node_key = ? AND scope_key = ?",
            (f"topic:{TOPIC}", SCOPE),
        )
        scope_a_topic = [int(row[0]) for row in await cursor.fetchall()]
    assert await store.get_neighbor_node_ids(scope_a_topic, 10, query_scope=other) == []


# ---------------------------------------------------------------------------
# legacy 边行不作为有效关系
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_edge_rows_without_evidence_are_not_readable(
    tmp_db_path: str,
) -> None:
    async with aiosqlite.connect(tmp_db_path) as db:
        await db.executescript(
            """
            CREATE TABLE graph_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, node_key TEXT NOT NULL,
                node_type TEXT NOT NULL, node_value TEXT NOT NULL,
                canonical_value TEXT NOT NULL, metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                scope_key TEXT, privacy_level TEXT, revision_token TEXT,
                UNIQUE(node_key, scope_key, privacy_level, revision_token)
            );
            CREATE TABLE graph_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT, edge_key TEXT NOT NULL,
                source_node_id INTEGER NOT NULL, target_node_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL, source_memory_id INTEGER NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0, confidence REAL NOT NULL DEFAULT 0.8,
                status TEXT NOT NULL DEFAULT 'active', metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                scope_key TEXT, privacy_level TEXT, revision_token TEXT,
                UNIQUE(source_memory_id, source_node_id, target_node_id,
                       relation_type, scope_key, privacy_level, revision_token)
            );
            INSERT INTO graph_nodes VALUES
              (1, 'person:alice', 'person', 'Alice', 'alice', '{}', 't', 't',
               'scope-a', 'public', 'r1'),
              (2, 'person:bob', 'person', 'Bob', 'bob', '{}', 't', 't',
               'scope-a', 'public', 'r1');
            INSERT INTO graph_edges VALUES
              (1, 'legacy', 1, 2, 'co_occurs_with', 9, 1.0, 0.7, 'active', '{}',
               't', 't', 'scope-a', 'public', 'r1');
            """
        )
        await db.commit()

    store = GraphStore(tmp_db_path)
    await store.initialize()

    boundary = GraphBoundary(SCOPE, "public", "r1")
    assert await store.get_neighbor_node_ids([1], 10, boundary=boundary) == []
    assert (await store.get_memory_entry_stats())["graph_edges"] == 0
    async with store._connect() as db:
        cursor = await db.execute(
            "SELECT semantic_edge_id FROM graph_edges WHERE id = 1"
        )
        row = await cursor.fetchone()
    # 旧行保留但不补造 evidence。
    assert row is not None and row[0] is None
    # 重复初始化保持幂等。
    await store.initialize()
