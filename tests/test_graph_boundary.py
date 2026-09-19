"""Graph source boundaries isolate every derived read and source deletion."""

from dataclasses import FrozenInstanceError
from typing import Any, cast

import aiosqlite
import pytest

from core.features.memory.application.retrieval_optimizer import RetrievalOptimizer
from core.features.memory.graph.domain.models import (
    GraphBoundary,
    GraphEdge,
    GraphEntry,
    GraphNode,
    GraphQueryScope,
)
from core.features.memory.graph.infrastructure.graph_store import GraphStore
from core.features.retrieval.rrf_fusion import HybridResult


async def _seed(store: GraphStore, boundary: GraphBoundary, memory_id: int):
    nodes = [
        GraphNode("person", "Alice", "alice"),
        GraphNode("fact", "Coffee", "coffee"),
    ]
    edge = GraphEdge(nodes[0].node_key, nodes[1].node_key, "likes", memory_id)
    entry = GraphEntry(
        "same-entry",
        memory_id,
        "session",
        "persona",
        "edge",
        "Alice likes coffee",
        node_keys=[node.node_key for node in nodes],
        relation_type="likes",
    )
    result = await store.replace_memory_graph(
        memory_id, nodes, [edge], [entry], boundary=boundary
    )
    found = await store.search_nodes_by_tokens(["alice", "coffee"], boundary=boundary)
    return {node["node_key"]: node["id"] for node in found}, result.entry_ids[0]


@pytest.mark.asyncio
async def test_shared_node_expansion_rechecks_current_canonical_boundary(tmp_db_path):
    store = GraphStore(tmp_db_path)
    await store.initialize()
    boundary = GraphBoundary("scope-a", "public", "r1")
    foreign = GraphBoundary("scope-b", "public", "r1")
    other_revision = GraphBoundary("scope-a", "public", "r2")
    seed_nodes, _ = await _seed(store, boundary, 1)
    await _seed(store, other_revision, 2)
    _, foreign_entry = await _seed(store, foreign, 3)
    await _seed(store, boundary, 4)
    _, other_session_entry = await _seed(store, boundary, 5)
    canonical = {
        memory_id: {
            "updated_at": source_boundary.revision_token,
            "metadata": source_boundary.as_params(),
        }
        for memory_id, source_boundary in (
            (1, boundary),
            (2, other_revision),
            (3, foreign),
            (4, boundary),
            (5, boundary),
        )
    }
    canonical[4]["updated_at"] = "r2"

    async def get_memory(memory_id):
        return canonical.get(memory_id)

    async with store._connect() as db:
        db.row_factory = aiosqlite.Row
        # Corrupt legacy links must not turn node adjacency into scope authority.
        await db.execute(
            "INSERT INTO graph_entry_nodes(entry_id, node_id) VALUES (?, ?)",
            (foreign_entry, seed_nodes["person:alice"]),
        )
        await db.execute(
            "UPDATE graph_entries SET session_id = 'other' WHERE id = ?",
            (other_session_entry,),
        )
        await db.commit()
        optimizer = RetrievalOptimizer(
            {"recall_engine.chain_topic_expansion_enabled": False},
            db_connection=db,
            get_memory_cb=get_memory,
        )
        seed = HybridResult(
            doc_id=1,
            final_score=1.0,
            rrf_score=1.0,
            bm25_score=None,
            vector_score=None,
            content="Alice likes coffee",
            metadata={},
        )
        result = await optimizer.chain_expand_multi_hop(
            [seed],
            10,
            "session",
            "persona",
            max_hops=1,
            query_scope=GraphQueryScope.from_boundary(boundary),
        )
        assert [item.doc_id for item in result] == [1, 2]
        canonical[2]["metadata"] = foreign.as_params()
        result = await optimizer.chain_expand_multi_hop(
            [seed],
            10,
            "session",
            "persona",
            max_hops=1,
            query_scope=GraphQueryScope.from_boundary(boundary),
        )
        assert [item.doc_id for item in result] == [1]
        without_scope = await optimizer.chain_expand_multi_hop(
            [seed], 10, "session", "persona", max_hops=1
        )
        assert [item.doc_id for item in without_scope] == [1]


@pytest.mark.asyncio
async def test_same_names_are_isolated_by_scope_privacy_and_revision(tmp_db_path):
    store = GraphStore(tmp_db_path)
    await store.initialize()
    boundaries = [
        GraphBoundary("scope-a", "public", "r1"),
        GraphBoundary("scope-b", "public", "r1"),
        GraphBoundary("scope-a", "confidential", "r1"),
        GraphBoundary("scope-a", "public", "r2"),
    ]
    mappings = []
    for memory_id, boundary in enumerate(boundaries, 1):
        mappings.append((await _seed(store, boundary, memory_id))[0])
    assert len({mapping["person:alice"] for mapping in mappings}) == 4
    for memory_id, (boundary, mapping) in enumerate(
        zip(boundaries, mappings, strict=True), 1
    ):
        assert await store.get_neighbor_node_ids(
            [mapping["person:alice"]], 10, boundary=boundary
        ) == [mapping["fact:coffee"]]
        hits = await store.search_entries_by_bm25("coffee", 10, boundary=boundary)
        assert [hit["source_memory_id"] for hit in hits] == [memory_id]
        assert await store.get_recent_memory_ids(boundary=boundary) == [memory_id]
        for snapshot in (
            await store.get_subgraph_for_memories([1, 2, 3, 4], boundary=boundary),
            await store.get_graph_snapshot(full=True, boundary=boundary),
        ):
            assert [item["memory_id"] for item in snapshot["memories"]] == [memory_id]
            assert {node["id"] for node in snapshot["nodes"]} == set(mapping.values())
            assert len(snapshot["edges"]) == 1
        canvas = await store.get_canvas_snapshot(boundary=boundary)
        assert {node["id"] for node in canvas["nodes"]} == set(mapping.values())
        assert len(canvas["edges"]) == 1
        other_mapping = mappings[memory_id % len(mappings)]
        assert (
            await store.get_neighbor_node_ids(
                [other_mapping["person:alice"]], 10, boundary=boundary
            )
            == []
        )
        assert (
            await store.get_entries_for_node_ids(
                [other_mapping["person:alice"]], 10, boundary=boundary
            )
            == []
        )


@pytest.mark.asyncio
async def test_deleting_one_source_preserves_other_source_and_revision(tmp_db_path):
    store = GraphStore(tmp_db_path)
    await store.initialize()
    boundary = GraphBoundary("scope", "public", "r1")
    newer = GraphBoundary("scope", "public", "r2")
    mapping, entry_id = await _seed(store, boundary, 1)
    await store.update_entry_vector_doc_id(entry_id, 77, boundary=boundary)
    other_mapping, _ = await _seed(store, boundary, 2)
    newer_mapping, _ = await _seed(store, newer, 3)
    assert mapping == other_mapping
    assert await store.delete_memory(1, boundary=boundary) == [77]
    assert await store.get_recent_memory_ids(boundary=boundary) == [2]
    assert await store.get_neighbor_node_ids(
        [mapping["person:alice"]], 10, boundary=boundary
    ) == [mapping["fact:coffee"]]
    assert await store.get_neighbor_node_ids(
        [newer_mapping["person:alice"]], 10, boundary=newer
    ) == [newer_mapping["fact:coffee"]]
    await store.delete_memory(2, boundary=boundary)
    assert await store.search_nodes_by_tokens(["alice"], boundary=boundary) == []
    assert await store.get_recent_memory_ids(boundary=newer) == [3]


@pytest.mark.asyncio
async def test_boundary_missing_and_cross_boundary_links_fail_closed(tmp_db_path):
    store = GraphStore(tmp_db_path)
    await store.initialize()
    boundary = GraphBoundary("scope", "public", "r1")
    other = GraphBoundary("other", "public", "r1")
    mapping, _ = await _seed(store, boundary, 1)
    with pytest.raises(ValueError, match="graph_query_scope_required"):
        await store.search_nodes_by_tokens(["alice"], boundary=cast(Any, None))
    with pytest.raises(ValueError, match="graph_boundary_required"):
        await store.upsert_node(
            GraphNode("person", "Alice", "alice"), boundary=cast(Any, None)
        )
    with pytest.raises(ValueError, match="graph_boundary_mismatch"):
        await store.add_edge(
            GraphEdge("person:alice", "fact:coffee", "likes", 2),
            mapping,
            boundary=other,
        )
    with pytest.raises(ValueError, match="graph_boundary_mismatch"):
        await store.replace_memory_graph(
            9, [], [GraphEdge("x", "y", "likes", 2)], [], boundary=boundary
        )
    assert await store.get_recent_memory_ids(boundary=boundary) == [1]


def test_boundary_requires_canonical_fields_and_is_immutable():
    with pytest.raises(ValueError, match="graph_boundary_required"):
        GraphBoundary.from_metadata(
            {"session_id": "scope", "privacy_level": "public", "revision_token": "r1"}
        )
    with pytest.raises(ValueError, match="graph_boundary_required"):
        GraphBoundary("scope", "unknown", "r1")
    boundary = GraphBoundary("scope", "public", "r1")
    with pytest.raises(FrozenInstanceError):
        setattr(boundary, "scope_key", "other")


async def _legacy_graph(db_path, *, incompatible_edges: bool = False):
    async with aiosqlite.connect(db_path) as db:
        await db.executescript("""
            CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT);
            INSERT INTO documents VALUES (99, 'canonical survives migration');
            CREATE TABLE graph_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, node_key TEXT NOT NULL UNIQUE,
                node_type TEXT NOT NULL, node_value TEXT NOT NULL, canonical_value TEXT NOT NULL,
                metadata TEXT DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE graph_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT, edge_key TEXT NOT NULL UNIQUE,
                source_node_id INTEGER NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
                target_node_id INTEGER NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
                relation_type TEXT NOT NULL, source_memory_id INTEGER NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0, confidence REAL NOT NULL DEFAULT 0.8,
                status TEXT NOT NULL DEFAULT 'active', metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE graph_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, entry_key TEXT NOT NULL UNIQUE,
                source_memory_id INTEGER NOT NULL, session_id TEXT, persona_id TEXT,
                entry_type TEXT NOT NULL, relation_type TEXT, content TEXT NOT NULL,
                metadata TEXT DEFAULT '{}', edge_id INTEGER REFERENCES graph_edges(id) ON DELETE CASCADE,
                vector_doc_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE graph_entry_nodes (
                entry_id INTEGER REFERENCES graph_entries(id) ON DELETE CASCADE,
                node_id INTEGER REFERENCES graph_nodes(id) ON DELETE CASCADE,
                PRIMARY KEY (entry_id, node_id)
            );
            CREATE VIRTUAL TABLE memora_graph_entries_fts
                USING fts5(content, entry_id UNINDEXED, tokenize='unicode61');
            INSERT INTO graph_nodes VALUES (7, 'person:alice', 'person', 'Alice', 'alice', '{}', 'old', 'old');
            INSERT INTO graph_nodes VALUES (8, 'fact:coffee', 'fact', 'Coffee', 'coffee', '{}', 'old', 'old');
            INSERT INTO graph_edges VALUES (11, 'old-edge', 7, 8, 'likes', 1, 1.0, 0.8, 'active', '{}', 'old', 'old');
            INSERT INTO graph_entries VALUES (13, 'same-entry', 1, 'session', 'persona', 'edge', 'likes', 'Alice likes coffee', '{}', 11, 77, 'old', 'old');
            INSERT INTO graph_entry_nodes VALUES (13, 7), (13, 8);
            INSERT INTO memora_graph_entries_fts(content, entry_id) VALUES ('Alice likes coffee', 13);
        """)
        if incompatible_edges:
            await db.execute(
                "ALTER TABLE graph_edges ADD COLUMN unknown_legacy_field TEXT"
            )
        await db.commit()


@pytest.mark.asyncio
async def test_legacy_global_unique_table_migrates_without_trusting_rows(tmp_db_path):
    await _legacy_graph(tmp_db_path)
    store = GraphStore(tmp_db_path)
    await store.initialize()
    boundary = GraphBoundary("scope", "public", "r1")
    assert await store.search_nodes_by_tokens(["alice"], boundary=boundary) == []
    assert await store.get_neighbor_node_ids([7], 10, boundary=boundary) == []
    assert await store.search_entries_by_bm25("coffee", 10, boundary=boundary) == []
    assert await store.get_entries_for_node_ids([7], 10, boundary=boundary) == []
    assert await store.get_recent_memory_ids(boundary=boundary) == []
    assert await store.get_subgraph_for_memories([1], boundary=boundary) == {
        "nodes": [],
        "edges": [],
        "entries": [],
        "memories": [],
    }
    mapping, _ = await _seed(store, boundary, 1)
    assert mapping["person:alice"] != 7
    async with store._connect() as db:
        row = await (
            await db.execute(
                "SELECT scope_key, privacy_level, revision_token FROM graph_nodes WHERE id = 7"
            )
        ).fetchone()
        assert row is None
        for table, row_id in (("graph_edges", 11), ("graph_entries", 13)):
            row = await (
                await db.execute(
                    f"SELECT scope_key, privacy_level, revision_token FROM {table} WHERE id = ?",
                    (row_id,),
                )
            ).fetchone()
            assert row is None
        links = await (
            await db.execute(
                "SELECT entry_id, node_id FROM graph_entry_nodes WHERE entry_id = 13 ORDER BY node_id"
            )
        ).fetchall()
        assert links == []
        with pytest.raises(aiosqlite.IntegrityError, match="graph_boundary_required"):
            await db.execute(
                "INSERT INTO graph_nodes(node_key,node_type,node_value,canonical_value,created_at,updated_at) VALUES ('x','x','x','x','now','now')"
            )
    await store.initialize()
    assert await store.get_recent_memory_ids(boundary=boundary) == [1]


@pytest.mark.asyncio
async def test_failed_graph_migration_rolls_back_without_changing_canonical(
    tmp_db_path,
):
    await _legacy_graph(tmp_db_path, incompatible_edges=True)
    store = GraphStore(tmp_db_path)
    with pytest.raises(aiosqlite.OperationalError):
        await store.initialize()
    async with aiosqlite.connect(tmp_db_path) as db:
        row = await (
            await db.execute("SELECT text FROM documents WHERE id = 99")
        ).fetchone()
        assert row == ("canonical survives migration",)
        columns = await (await db.execute("PRAGMA table_info(graph_nodes)")).fetchall()
        assert "scope_key" not in {row[1] for row in columns}
        rows = await (
            await db.execute("SELECT node_key FROM graph_nodes ORDER BY id")
        ).fetchall()
        assert rows == [("person:alice",), ("fact:coffee",)]
        links = await (
            await db.execute(
                "SELECT entry_id, node_id FROM graph_entry_nodes ORDER BY node_id"
            )
        ).fetchall()
        assert links == [(13, 7), (13, 8)]


def test_two_level_cache_never_reuses_another_graph_scope() -> None:
    """同 session 的请求也不能跨 scope、privacy 或缺少授权时复用图命中。"""
    optimizer = RetrievalOptimizer({})
    scope = GraphQueryScope("scope-a", "public")
    candidate = HybridResult(7, 1.0, 1.0, None, None, "允许事实", {})
    key = optimizer.cache_key("q", 1, "session", None, query_scope=scope)
    optimizer.set_cached(key, [candidate])
    optimizer.set_session_cached(
        "q", 1, "session", None, [candidate], query_scope=scope
    )
    assert optimizer.get_cached(key)[0].doc_id == 7
    assert (
        optimizer.get_session_cached("q", 1, "session", None, query_scope=scope)[
            0
        ].doc_id
        == 7
    )
    for other in (
        None,
        GraphQueryScope("scope-b", "public"),
        GraphQueryScope("scope-a", "confidential"),
    ):
        foreign_key = optimizer.cache_key("q", 1, "session", None, query_scope=other)
        assert optimizer.get_cached(foreign_key) is None
        assert (
            optimizer.get_session_cached("q", 1, "session", None, query_scope=other)
            is None
        )
