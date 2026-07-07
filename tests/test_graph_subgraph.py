"""GraphSubgraphMixin 测试 — 记忆标识符的紧凑图快照。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────

def _row(values: dict[str, Any]) -> MagicMock:
    """Fake aiosqlite.Row as a MagicMock with __getitem__ and keys()."""
    row = MagicMock()
    row.__getitem__.side_effect = lambda k: values[k]
    row.keys.return_value = list(values.keys())
    row.values.return_value = list(values.values())
    for k, v in values.items():
        setattr(row, k, v)
    return row


def _make_graph_store() -> Any:
    """Create a minimal GraphStore with subgraph mixin for testing."""
    from core.storage.graph_store import GraphStore

    db_path = ":memory:"
    store = GraphStore.__new__(GraphStore)
    store._db_path = db_path
    # Replace _connect with an async context manager that yields a MagicMock
    return store


class TestGraphSubgraphMixin:
    """Tests for get_subgraph_for_memories and helper methods."""

    # ── get_subgraph_for_memories ─────────────────────────────────────

    @pytest.mark.asyncio
    async def test_empty_memory_ids_returns_empty(self) -> None:
        """Empty memory_ids → returns empty structure."""
        store = _make_graph_store()
        result = await store.get_subgraph_for_memories([])
        assert result == {"nodes": [], "edges": [], "entries": [], "memories": []}

    @pytest.mark.asyncio
    async def test_invalid_memory_ids_filtered_out(self) -> None:
        """Invalid memory_ids (non-int, None-like) are filtered (lines 29-30)."""
        store = _make_graph_store()
        # Non-numeric string and None are skipped via TypeError/ValueError
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)
        mock_db.row_factory = None
        mock_db.execute = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_db.execute.return_value = mock_cursor
        store._connect = MagicMock(return_value=mock_db)

        result = await store.get_subgraph_for_memories([None, "bad", 1, "also_bad"])
        # None and "bad", "also_bad" are filtered; only 1 remains
        assert result == {"nodes": [], "edges": [], "entries": [], "memories": []}

    @pytest.mark.asyncio
    async def test_duplicate_memory_ids_deduplicated(self) -> None:
        """Duplicate memory_ids are deduplicated (line 32)."""
        store = _make_graph_store()
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)
        mock_db.row_factory = None
        mock_db.execute = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_db.execute.return_value = mock_cursor
        store._connect = MagicMock(return_value=mock_db)

        await store.get_subgraph_for_memories([1, 1, 2, 2, 2, 3])
        # Check that the query used exactly 3 unique memory ids
        call_args = mock_db.execute.call_args_list[0]
        params = call_args[0][1]  # positional params tuple
        # Params structure: (*memory_ids, limit_entries). Last param is limit.
        memory_id_params = list(params[:-1])  # exclude limit_entries
        assert len(memory_id_params) == 3
        assert sorted(memory_id_params) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_empty_entry_rows_returns_empty(self) -> None:
        """When no entry rows found, returns empty structure early (line 47)."""
        store = _make_graph_store()
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)
        mock_db.row_factory = None
        mock_db.execute = AsyncMock()

        # First execute → entry rows empty
        entry_cursor = AsyncMock()
        entry_cursor.fetchall = AsyncMock(return_value=[])
        mock_db.execute.return_value = entry_cursor

        store._connect = MagicMock(return_value=mock_db)
        result = await store.get_subgraph_for_memories([1])
        assert result == {"nodes": [], "edges": [], "entries": [], "memories": []}

    # ── node limiting branch (lines 69-99) ────────────────────────────

    @pytest.mark.asyncio
    async def test_node_limiting_when_nodes_exceed_limit(self) -> None:
        """When node count > limit_nodes, nodes are ranked and filtered (lines 69-99)."""
        store = _make_graph_store()

        # Build rows for 5 nodes across 2 entries → limit_nodes=2 to trigger filtering
        entry_rows = [
            _row({"id": 1, "source_memory_id": 10, "session_id": "s1",
                  "persona_id": "p1", "entry_type": "fact", "relation_type": "has",
                  "content": "entry1", "metadata": "{}", "edge_id": None}),
            _row({"id": 2, "source_memory_id": 10, "session_id": "s1",
                  "persona_id": "p1", "entry_type": "summary", "relation_type": None,
                  "content": "summary1", "metadata": "{}", "edge_id": None}),
        ]

        node_rows = [
            _row({"entry_id": 1, "node_id": 101, "node_key": "k1", "node_type": "entity",
                  "node_value": "NodeA", "canonical_value": "node_a", "metadata": "{}"}),
            _row({"entry_id": 1, "node_id": 102, "node_key": "k2", "node_type": "entity",
                  "node_value": "NodeB", "canonical_value": "node_b", "metadata": "{}"}),
            _row({"entry_id": 2, "node_id": 103, "node_key": "k3", "node_type": "concept",
                  "node_value": "NodeC", "canonical_value": "node_c", "metadata": "{}"}),
            _row({"entry_id": 2, "node_id": 104, "node_key": "k4", "node_type": "topic",
                  "node_value": "NodeD", "canonical_value": "node_d", "metadata": "{}"}),
            _row({"entry_id": 2, "node_id": 105, "node_key": "k5", "node_type": "event",
                  "node_value": "NodeE", "canonical_value": "node_e", "metadata": "{}"}),
        ]

        edge_rows: list[MagicMock] = []

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)
        mock_db.row_factory = None

        # We need execute to return different cursors for each query
        entry_cursor = AsyncMock()
        entry_cursor.fetchall = AsyncMock(return_value=entry_rows)
        node_cursor = AsyncMock()
        node_cursor.fetchall = AsyncMock(return_value=node_rows)
        edge_cursor = AsyncMock()
        edge_cursor.fetchall = AsyncMock(return_value=edge_rows)

        mock_db.execute = AsyncMock()
        mock_db.execute.side_effect = [entry_cursor, node_cursor, edge_cursor]

        store._connect = MagicMock(return_value=mock_db)
        # Patch _from_json to return a dict
        with patch.object(store, "_from_json", return_value={"importance": 0.5}):
            result = await store.get_subgraph_for_memories(
                [10], limit_nodes=2,
            )
        assert "nodes" in result
        assert "edges" in result
        assert "entries" in result
        assert "memories" in result
        # With limit_nodes=2, at most 2 nodes survive ranking
        assert len(result["nodes"]) <= 2

    # ── _build_subgraph_entries — node not in node_map (line 284) ─────

    def test_build_entries_node_not_in_map(self) -> None:
        """When a node_id is not in node_map, it's skipped (line 284)."""
        store = _make_graph_store()
        entry_rows = [
            _row({"id": 1, "source_memory_id": 10, "session_id": "s1",
                  "persona_id": "p1", "entry_type": "fact", "relation_type": "has",
                  "content": "test", "metadata": "{}", "edge_id": None}),
        ]
        # entry_node_map references a node that doesn't exist in node_map
        entry_node_map = {1: [999]}  # node_id 999 not in node_map
        node_map: dict[int, dict[str, Any]] = {}
        memory_base: dict[int, dict[str, Any]] = {}

        with patch.object(store, "_from_json", return_value={}):
            entries = store._build_subgraph_entries(
                entry_rows, entry_node_map, node_map, memory_base,
            )
        assert len(entries) == 1
        assert entries[0]["node_ids"] == [999]
        # node 999 was skipped (not in node_map) → no crash

    def test_build_entries_node_in_map_increments_count(self) -> None:
        """Node in node_map gets entry_count incremented and _memory_ids updated."""
        store = _make_graph_store()
        entry_rows = [
            _row({"id": 1, "source_memory_id": 10, "session_id": "s1",
                  "persona_id": "p1", "entry_type": "fact", "relation_type": "has",
                  "content": "test", "metadata": "{}", "edge_id": None}),
        ]
        entry_node_map = {1: [101]}
        node_map = {
            101: {
                "id": 101, "key": "k1", "type": "entity",
                "label": "NodeA", "canonical_value": "node_a",
                "metadata": {}, "entry_count": 0, "memory_count": 0,
                "degree": 0, "weight": 0.0, "_memory_ids": set(),
            }
        }
        memory_base: dict[int, dict[str, Any]] = {}

        with patch.object(store, "_from_json", return_value={}):
            store._build_subgraph_entries(entry_rows, entry_node_map, node_map, memory_base)

        assert node_map[101]["entry_count"] == 1
        assert 10 in node_map[101]["_memory_ids"]

    # ── _build_subgraph_memories ──────────────────────────────────────

    def test_build_memories_nodes_limited_branch(self) -> None:
        """When nodes_were_limited=True, recount entries/edges from filtered data (lines 349-370)."""
        store = _make_graph_store()
        memory_base: dict[int, dict[str, Any]] = {
            10: {
                "memory_id": 10,
                "summary": "summary text",
                "session_id": "s1",
                "persona_id": "p1",
                "importance": 0.7,
                "entry_count": 5,  # overcounted, will be reset
                "edge_count": 3,   # overcounted, will be reset
                "node_ids": {101, 102, 103},
                "entry_types": {"fact", "summary"},
            },
        }
        entries = [
            {"memory_id": 10, "node_ids": [101], "entry_type": "fact"},
            {"memory_id": 10, "node_ids": [102], "entry_type": "summary"},
        ]
        edges = [
            {"memory_id": 10},
            {"memory_id": 10},
        ]

        memories = store._build_subgraph_memories(
            memory_base, entries, edges, nodes_were_limited=True,
        )
        assert len(memories) == 1
        mem = memories[0]
        assert mem["entry_count"] == 2  # recounted from entries
        assert mem["edge_count"] == 2   # recounted from edges
        assert mem["node_count"] == 2   # from node_ids

    def test_build_memories_nodes_not_limited(self) -> None:
        """When nodes_were_limited=False, original base counts are preserved."""
        store = _make_graph_store()
        memory_base: dict[int, dict[str, Any]] = {
            10: {
                "memory_id": 10,
                "summary": "summary text",
                "session_id": "s1",
                "persona_id": "p1",
                "importance": 0.7,
                "entry_count": 5,
                "edge_count": 3,
                "node_ids": {101, 102, 103},
                "entry_types": {"fact", "summary"},
            },
        }
        entries: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        memories = store._build_subgraph_memories(
            memory_base, entries, edges, nodes_were_limited=False,
        )
        assert len(memories) == 1
        mem = memories[0]
        assert mem["entry_count"] == 5
        assert mem["edge_count"] == 3

    def test_build_memories_empty_skipped(self) -> None:
        """Memories with 0 entries AND 0 edges after recount → skipped (line 375)."""
        store = _make_graph_store()
        memory_base: dict[int, dict[str, Any]] = {
            10: {
                "memory_id": 10,
                "summary": "summary text",
                "session_id": "s1",
                "persona_id": "p1",
                "importance": 0.0,
                "entry_count": 0,
                "edge_count": 0,
                "node_ids": set(),
                "entry_types": set(),
            },
        }
        entries: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []

        # Not limited → sees original counts (0,0) → skipped
        memories = store._build_subgraph_memories(
            memory_base, entries, edges, nodes_were_limited=False,
        )
        assert memories == []

    def test_build_memories_entry_with_unknown_memory_id(self) -> None:
        """Entries referencing unknown memory_id are ignored in limited mode (line 361-362)."""
        store = _make_graph_store()
        memory_base: dict[int, dict[str, Any]] = {
            10: {
                "memory_id": 10,
                "summary": "summary",
                "session_id": "s1",
                "persona_id": "p1",
                "importance": 0.5,
                "entry_count": 1,
                "edge_count": 1,
                "node_ids": {101},
                "entry_types": {"fact"},
            },
        }
        entries = [
            {"memory_id": 10, "node_ids": [101], "entry_type": "fact"},
            {"memory_id": 999, "node_ids": [999], "entry_type": "unknown"},  # not in memory_base
        ]
        edges = [
            {"memory_id": 10},
        ]

        memories = store._build_subgraph_memories(
            memory_base, entries, edges, nodes_were_limited=True,
        )
        assert len(memories) == 1

    def test_build_memories_edge_with_unknown_memory_id(self) -> None:
        """Edges referencing unknown memory_id are ignored (line 369)."""
        store = _make_graph_store()
        memory_base: dict[int, dict[str, Any]] = {
            10: {
                "memory_id": 10,
                "summary": "summary",
                "session_id": "s1",
                "persona_id": "p1",
                "importance": 0.5,
                "entry_count": 1,
                "edge_count": 1,
                "node_ids": {101},
                "entry_types": {"fact"},
            },
        }
        entries: list[dict[str, Any]] = []
        edges = [
            {"memory_id": 10},
            {"memory_id": 888},  # not in memory_base → ignored
        ]

        memories = store._build_subgraph_memories(
            memory_base, entries, edges, nodes_were_limited=True,
        )
        assert len(memories) == 1
        assert memories[0]["edge_count"] == 1  # only the matching edge counted

    # ── _build_subgraph_maps ──────────────────────────────────────────

    def test_build_maps_creates_correct_structure(self) -> None:
        """_build_subgraph_maps creates entry_node_map and node_map correctly."""
        store = _make_graph_store()
        node_rows = [
            _row({"entry_id": 1, "node_id": 101, "node_key": "k1", "node_type": "entity",
                  "node_value": "TestNode", "canonical_value": "test_node", "metadata": "{}"}),
            _row({"entry_id": 1, "node_id": 102, "node_key": "k2", "node_type": "concept",
                  "node_value": "TestConcept", "canonical_value": "test_concept", "metadata": "{}"}),
            _row({"entry_id": 2, "node_id": 101, "node_key": "k1", "node_type": "entity",
                  "node_value": "TestNode", "canonical_value": "test_node", "metadata": "{}"}),
        ]

        with patch.object(store, "_from_json", return_value={}):
            entry_node_map, node_map = store._build_subgraph_maps(node_rows)

        assert entry_node_map == {1: [101, 102], 2: [101]}
        assert len(node_map) == 2  # 101 and 102
        assert 101 in node_map
        assert node_map[101]["key"] == "k1"
        assert node_map[101]["entry_count"] == 0  # set later by _build_subgraph_entries

    # ── _build_subgraph_edges ─────────────────────────────────────────

    def test_build_edges_updates_degree_and_memory(self) -> None:
        """_build_subgraph_edges increments node degree and memory edge_count."""
        store = _make_graph_store()
        edge_rows = [
            _row({"id": 1, "edge_key": "e1", "source_node_id": 101, "target_node_id": 102,
                  "relation_type": "related_to", "source_memory_id": 10,
                  "weight": 0.8, "confidence": 0.9, "status": "active", "metadata": "{}",
                  "created_at": "2026-07-07T12:00:00+00:00"}),
        ]
        node_map = {
            101: {"id": 101, "key": "k1", "type": "entity", "label": "A",
                  "canonical_value": "a", "metadata": {}, "entry_count": 0,
                  "memory_count": 0, "degree": 0, "weight": 0.0, "_memory_ids": set()},
            102: {"id": 102, "key": "k2", "type": "entity", "label": "B",
                  "canonical_value": "b", "metadata": {}, "entry_count": 0,
                  "memory_count": 0, "degree": 0, "weight": 0.0, "_memory_ids": set()},
        }
        memory_base: dict[int, dict[str, Any]] = {
            10: {"memory_id": 10, "summary": "s", "session_id": "s1",
                 "persona_id": "p1", "importance": 0.5, "entry_count": 1,
                 "edge_count": 0, "node_ids": set(), "entry_types": set()},
        }

        with patch.object(store, "_from_json", return_value={}):
            edges = store._build_subgraph_edges(edge_rows, node_map, memory_base)

        assert len(edges) == 1
        assert edges[0]["type"] == "related_to"
        assert edges[0]["created_at"] == "2026-07-07T12:00:00+00:00"
        assert edges[0]["timestamp"] == 1783425600.0
        assert node_map[101]["degree"] == 1
        assert node_map[102]["degree"] == 1
        assert memory_base[10]["edge_count"] == 1

    def test_build_edges_prefers_entry_business_time(self) -> None:
        """Edges use memory/entry business time instead of graph insertion time."""
        store = _make_graph_store()
        edge_rows = [
            _row({"id": 1, "edge_key": "e1", "source_node_id": 101, "target_node_id": 102,
                  "relation_type": "related_to", "source_memory_id": 10,
                  "weight": 0.8, "confidence": 0.9, "status": "active", "metadata": "{}",
                  "created_at": "2026-07-07T12:00:00+00:00"}),
        ]
        node_map = {
            101: {"id": 101, "key": "k1", "type": "entity", "label": "A",
                  "canonical_value": "a", "metadata": {}, "entry_count": 0,
                  "memory_count": 0, "degree": 0, "weight": 0.0, "_memory_ids": set()},
            102: {"id": 102, "key": "k2", "type": "entity", "label": "B",
                  "canonical_value": "b", "metadata": {}, "entry_count": 0,
                  "memory_count": 0, "degree": 0, "weight": 0.0, "_memory_ids": set()},
        }

        with patch.object(store, "_from_json", return_value={}):
            edges = store._build_subgraph_edges(
                edge_rows,
                node_map,
                {},
                edge_time_by_id={1: 1700000000.0},
            )

        assert edges[0]["timestamp"] == 1700000000.0

    def test_build_edges_normalizes_millisecond_business_time(self) -> None:
        """Millisecond timestamps are normalized to Unix seconds for dashboard filters."""
        store = _make_graph_store()
        edge_rows = [
            _row({"id": 1, "edge_key": "e1", "source_node_id": 101, "target_node_id": 102,
                  "relation_type": "related_to", "source_memory_id": 10,
                  "weight": 0.8, "confidence": 0.9, "status": "active", "metadata": "{}",
                  "created_at": "2026-07-07T12:00:00+00:00"}),
        ]
        node_map = {
            101: {"id": 101, "key": "k1", "type": "entity", "label": "A",
                  "canonical_value": "a", "metadata": {}, "entry_count": 0,
                  "memory_count": 0, "degree": 0},
            102: {"id": 102, "key": "k2", "type": "entity", "label": "B",
                  "canonical_value": "b", "metadata": {}, "entry_count": 0,
                  "memory_count": 0, "degree": 0},
        }

        edges = store._build_subgraph_edges(
            edge_rows,
            node_map,
            {},
            edge_time_by_id={1: 1700000000000.0},
        )

        assert edges[0]["timestamp"] == 1700000000.0

    def test_build_edges_uses_temporal_edge_source_event_time(self) -> None:
        """Temporal BEFORE/AFTER edges use the source-side event time."""
        store = _make_graph_store()
        edge_rows = [
            _row({"id": 1, "edge_key": "e1", "source_node_id": 101, "target_node_id": 102,
                  "relation_type": "before", "source_memory_id": 10,
                  "weight": 1.0, "confidence": 0.9, "status": "active",
                  "metadata": '{"event_time_a": 1700000000.0, "event_time_b": 1700086400.0}',
                  "created_at": "2026-07-07T12:00:00+00:00"}),
            _row({"id": 2, "edge_key": "e2", "source_node_id": 102, "target_node_id": 101,
                  "relation_type": "after", "source_memory_id": 10,
                  "weight": 1.0, "confidence": 0.9, "status": "active",
                  "metadata": '{"event_time_a": 1700000000.0, "event_time_b": 1700086400.0}',
                  "created_at": "2026-07-07T12:00:00+00:00"}),
        ]
        node_map = {
            101: {"id": 101, "key": "k1", "type": "fact", "label": "A",
                  "canonical_value": "a", "metadata": {}, "entry_count": 0,
                  "memory_count": 0, "degree": 0, "weight": 0.0, "_memory_ids": set()},
            102: {"id": 102, "key": "k2", "type": "fact", "label": "B",
                  "canonical_value": "b", "metadata": {}, "entry_count": 0,
                  "memory_count": 0, "degree": 0, "weight": 0.0, "_memory_ids": set()},
        }

        edges = store._build_subgraph_edges(edge_rows, node_map, {})

        assert [edge["timestamp"] for edge in edges] == [1700000000.0, 1700086400.0]

    def test_build_edges_source_node_not_in_map(self) -> None:
        """Edge with source_node not in map → target degree still updated."""
        store = _make_graph_store()
        edge_rows = [
            _row({"id": 1, "edge_key": "e1", "source_node_id": 999, "target_node_id": 102,
                  "relation_type": "related_to", "source_memory_id": 10,
                  "weight": 0.5, "confidence": 0.5, "status": "active", "metadata": "{}"}),
        ]
        node_map = {
            102: {"id": 102, "key": "k2", "type": "entity", "label": "B",
                  "canonical_value": "b", "metadata": {}, "entry_count": 0,
                  "memory_count": 0, "degree": 0, "weight": 0.0, "_memory_ids": set()},
        }
        memory_base: dict[int, dict[str, Any]] = {}

        with patch.object(store, "_from_json", return_value={}):
            edges = store._build_subgraph_edges(edge_rows, node_map, memory_base)

        assert len(edges) == 1
        assert node_map[102]["degree"] == 1
        # source_node 999 not in map → no crash

    # ── limit clamping ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_limit_values_are_clamped(self) -> None:
        """limit_entries/limit_nodes/limit_edges are clamped to valid ranges."""
        store = _make_graph_store()
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)
        mock_db.row_factory = None
        mock_db.execute = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_db.execute.return_value = mock_cursor
        store._connect = MagicMock(return_value=mock_db)

        # Values below min are clamped to 1; above max are clamped to upper bound
        # This should not crash
        await store.get_subgraph_for_memories(
            [1], limit_entries=0, limit_nodes=0, limit_edges=0,
        )
        # Similarly for oversized values
        await store.get_subgraph_for_memories(
            [1], limit_entries=9999, limit_nodes=9999, limit_edges=9999,
        )
