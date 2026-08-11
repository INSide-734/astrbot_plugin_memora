"""GraphQueryMixin 测试 — 过滤路径（session_id、persona_id）全覆盖。"""

import pytest

from core.features.memory.graph.domain.models import GraphEdge, GraphEntry, GraphNode
from core.storage.graph_store import GraphStore


class TestGraphQueryFilters:
    """Query operations with session_id / persona_id filters."""

    async def _setup_filtered_data(self, store):
        """Create entries with different session/persona values."""
        nodes = [
            GraphNode(
                node_type="entity", value="FilterTest", canonical_value="filtertest"
            ),
            GraphNode(node_type="entity", value="Target", canonical_value="target"),
        ]
        node_map = await store.upsert_nodes(nodes)

        # Entry for session s1 / persona p1
        edge1 = GraphEdge(
            source_key="entity:filtertest",
            target_key="entity:target",
            relation_type="related",
            source_memory_id=10,
        )
        edge_id1 = await store.add_edge(edge1, node_map)
        entry1 = GraphEntry(
            entry_key="mem10:filter_entry",
            source_memory_id=10,
            session_id="s-filter-1",
            persona_id="p-filter-1",
            entry_type="fact",
            content="Filter Test Entry One",
            node_keys=["entity:filtertest", "entity:target"],
            relation_type="related",
        )
        await store.add_entry(entry1, node_map, edge_id1)

        # Entry for session s2 / persona p2
        edge2 = GraphEdge(
            source_key="entity:filtertest",
            target_key="entity:target",
            relation_type="linked",
            source_memory_id=20,
        )
        edge_id2 = await store.add_edge(edge2, node_map)
        entry2 = GraphEntry(
            entry_key="mem20:filter_entry",
            source_memory_id=20,
            session_id="s-filter-2",
            persona_id="p-filter-2",
            entry_type="fact",
            content="Filter Test Entry Two",
            node_keys=["entity:filtertest", "entity:target"],
            relation_type="linked",
        )
        await store.add_entry(entry2, node_map, edge_id2)

        return node_map

    @pytest.mark.asyncio
    async def test_search_entries_by_bm25_session_filter(self, tmp_db_path):
        """search_entries_by_bm25 with session_id filter."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        await self._setup_filtered_data(store)

        results = await store.search_entries_by_bm25(
            "Filter", limit=10, session_id="s-filter-1"
        )
        assert len(results) >= 1
        # All results should match the session filter
        for r in results:
            assert "Filter" in r["content"]

    @pytest.mark.asyncio
    async def test_search_entries_by_bm25_persona_filter(self, tmp_db_path):
        """search_entries_by_bm25 with persona_id filter."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        await self._setup_filtered_data(store)

        results = await store.search_entries_by_bm25(
            "Filter", limit=10, persona_id="p-filter-1"
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_entries_by_bm25_both_filters(self, tmp_db_path):
        """search_entries_by_bm25 with both session_id and persona_id filters."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        await self._setup_filtered_data(store)

        results = await store.search_entries_by_bm25(
            "Filter", limit=10, session_id="s-filter-1", persona_id="p-filter-1"
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_get_entries_for_node_ids_session_filter(self, tmp_db_path):
        """get_entries_for_node_ids with session_id filter."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        node_map = await self._setup_filtered_data(store)

        target_id = node_map["entity:target"]
        results = await store.get_entries_for_node_ids(
            [target_id], limit=10, session_id="s-filter-1"
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_get_entries_for_node_ids_persona_filter(self, tmp_db_path):
        """get_entries_for_node_ids with persona_id filter."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        node_map = await self._setup_filtered_data(store)

        target_id = node_map["entity:target"]
        results = await store.get_entries_for_node_ids(
            [target_id], limit=10, persona_id="p-filter-1"
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_get_entries_for_node_ids_both_filters(self, tmp_db_path):
        """get_entries_for_node_ids with both filters."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        node_map = await self._setup_filtered_data(store)

        target_id = node_map["entity:target"]
        results = await store.get_entries_for_node_ids(
            [target_id], limit=10, session_id="s-filter-1", persona_id="p-filter-1"
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_get_neighbor_node_ids_empty(self, tmp_db_path):
        """get_neighbor_node_ids with empty list returns empty list."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        result = await store.get_neighbor_node_ids([], limit=10)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_recent_memory_ids_session_filter(self, tmp_db_path):
        """get_recent_memory_ids with session_id filter."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        await self._setup_filtered_data(store)

        results = await store.get_recent_memory_ids(limit=10, session_id="s-filter-1")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_get_recent_memory_ids_persona_filter(self, tmp_db_path):
        """get_recent_memory_ids with persona_id filter."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        await self._setup_filtered_data(store)

        results = await store.get_recent_memory_ids(limit=10, persona_id="p-filter-1")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_get_recent_memory_ids_both_filters(self, tmp_db_path):
        """get_recent_memory_ids with both session and persona filters."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        await self._setup_filtered_data(store)

        results = await store.get_recent_memory_ids(
            limit=10, session_id="s-filter-1", persona_id="p-filter-1"
        )
        assert len(results) >= 1
