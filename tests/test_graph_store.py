"""GraphStore 测试 — 初始化、删除、查询、子图、统计、快照。"""

import pytest

from core.features.memory.graph.domain.models import GraphEdge, GraphEntry, GraphNode
from core.features.memory.graph.infrastructure.graph_store import GraphStore


class TestGraphStoreInitialize:
    """GraphStore initialization."""

    @pytest.mark.asyncio
    async def test_initialize_creates_tables(self, tmp_db_path):
        """initialize creates all graph tables without error."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        # Verify tables exist by running harmless queries
        async with store._connect() as db:
            tables = [
                "graph_nodes",
                "graph_edges",
                "graph_entries",
                "graph_entry_nodes",
                "memora_graph_entries_fts",
            ]
            for table in tables:
                cursor = await db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                )
                row = await cursor.fetchone()
                assert row is not None, f"Table {table} should exist"

    @pytest.mark.asyncio
    async def test_get_memory_entry_stats(self, tmp_db_path):
        """get_memory_entry_stats returns node/edge/entry counts."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        stats = await store.get_memory_entry_stats()
        assert stats["graph_nodes"] == 0
        assert stats["graph_edges"] == 0
        assert stats["graph_entries"] == 0

        node = GraphNode(node_type="entity", value="test", canonical_value="test")
        await store.upsert_node(node)

        stats = await store.get_memory_entry_stats()
        assert stats["graph_nodes"] == 1


class TestGraphDelete:
    """Graph deletion operations."""

    async def _setup_graph_data(self, store):
        """Create nodes, edges, and entries for testing deletes."""
        nodes = [
            GraphNode(node_type="entity", value="A", canonical_value="a"),
            GraphNode(node_type="entity", value="B", canonical_value="b"),
            GraphNode(node_type="entity", value="C", canonical_value="c"),
        ]
        node_map = await store.upsert_nodes(nodes)

        edge = GraphEdge(
            source_key="entity:a",
            target_key="entity:b",
            relation_type="related",
            source_memory_id=100,
        )
        edge_id = await store.add_edge(edge, node_map)
        edge_map = {edge.edge_key: edge_id}

        entry = GraphEntry(
            entry_key="mem100:entry1",
            source_memory_id=100,
            session_id="s1",
            persona_id=None,
            entry_type="fact",
            content="A and B are related",
            node_keys=["entity:a", "entity:b"],
            relation_type="related",
        )
        await store.add_entry(entry, node_map, edge_id)
        return node_map, edge_map

    @pytest.mark.asyncio
    async def test_delete_memory(self, tmp_db_path):
        """delete_memory removes all graph artifacts for a source_memory_id."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        await self._setup_graph_data(store)
        vector_doc_ids = await store.delete_memory(100)
        assert vector_doc_ids == []
        # Verify entries/edges cleaned up
        stats = await store.get_memory_entry_stats()
        assert stats["graph_entries"] == 0
        assert stats["graph_edges"] == 0

    @pytest.mark.asyncio
    async def test_delete_memory_no_data(self, tmp_db_path):
        """delete_memory on unknown id returns empty list."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        result = await store.delete_memory(99999)
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_delete_memories(self, tmp_db_path):
        """batch_delete_memories removes artifacts for multiple memories."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        # Create data for memory 200 and 201
        for mem_id in [200, 201]:
            nodes = [
                GraphNode(
                    node_type="entity", value=f"X{mem_id}", canonical_value=f"x{mem_id}"
                ),
                GraphNode(
                    node_type="entity", value=f"Y{mem_id}", canonical_value=f"y{mem_id}"
                ),
            ]
            node_map = await store.upsert_nodes(nodes)
            edge = GraphEdge(
                source_key=f"entity:x{mem_id}",
                target_key=f"entity:y{mem_id}",
                relation_type="related",
                source_memory_id=mem_id,
            )
            await store.add_edge(edge, node_map)

        await store.batch_delete_memories([200, 201])
        stats = await store.get_memory_entry_stats()
        assert stats["graph_edges"] == 0

    @pytest.mark.asyncio
    async def test_batch_delete_empty(self, tmp_db_path):
        """batch_delete_memories with empty list returns empty dict."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        assert await store.batch_delete_memories([]) == {}


class TestGraphQuery:
    """Graph query operations."""

    async def _setup_searchable_data(self, store):
        """Create data suitable for search queries."""
        nodes = [
            GraphNode(node_type="entity", value="WestLake", canonical_value="westlake"),
            GraphNode(node_type="entity", value="Hangzhou", canonical_value="hangzhou"),
        ]
        node_map = await store.upsert_nodes(nodes)
        entry = GraphEntry(
            entry_key="mem1:search_entry",
            source_memory_id=1,
            session_id="s-search",
            persona_id="p1",
            entry_type="fact",
            content="WestLake is located in Hangzhou",
            node_keys=["entity:westlake", "entity:hangzhou"],
            relation_type="located_in",
        )
        await store.add_entry(entry, node_map)
        return node_map

    @pytest.mark.asyncio
    async def test_search_entries_by_bm25(self, tmp_db_path):
        """search_entries_by_bm25 finds entries matching FTS query."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        await self._setup_searchable_data(store)
        results = await store.search_entries_by_bm25("WestLake", limit=10)
        assert len(results) >= 1
        assert any("WestLake" in r["content"] for r in results)

    @pytest.mark.asyncio
    async def test_search_entries_no_match(self, tmp_db_path):
        """search_entries_by_bm25 returns empty on no match."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        results = await store.search_entries_by_bm25("珠穆朗玛峰", limit=10)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_nodes_by_tokens(self, tmp_db_path):
        """search_nodes_by_tokens finds nodes by canonical value."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        await self._setup_searchable_data(store)
        results = await store.search_nodes_by_tokens(["westlake"], limit=10)
        assert len(results) >= 1
        assert any("westlake" in r["canonical_value"] for r in results)

    @pytest.mark.asyncio
    async def test_search_nodes_empty_tokens(self, tmp_db_path):
        """search_nodes_by_tokens with empty tokens returns empty."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        assert await store.search_nodes_by_tokens([]) == []

    @pytest.mark.asyncio
    async def test_get_entries_for_node_ids(self, tmp_db_path):
        """get_entries_for_node_ids returns entries linked to given nodes."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        node_map = await self._setup_searchable_data(store)
        westlake_id = node_map["entity:westlake"]

        results = await store.get_entries_for_node_ids([westlake_id], limit=10)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_get_entries_for_node_ids_empty(self, tmp_db_path):
        """get_entries_for_node_ids with empty list returns empty."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        assert await store.get_entries_for_node_ids([], limit=10) == []

    @pytest.mark.asyncio
    async def test_get_neighbor_node_ids(self, tmp_db_path):
        """get_neighbor_node_ids finds adjacent nodes through active edges."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        nodes = [
            GraphNode(node_type="entity", value="A", canonical_value="a"),
            GraphNode(node_type="entity", value="B", canonical_value="b"),
            GraphNode(node_type="entity", value="C", canonical_value="c"),
        ]
        node_map = await store.upsert_nodes(nodes)
        edge = GraphEdge(
            source_key="entity:a",
            target_key="entity:b",
            relation_type="linked",
            source_memory_id=1,
        )
        await store.add_edge(edge, node_map)

        neighbors = await store.get_neighbor_node_ids([node_map["entity:a"]], limit=10)
        assert node_map["entity:b"] in neighbors
        assert node_map["entity:a"] not in neighbors  # not in own neighbors

    @pytest.mark.asyncio
    async def test_get_recent_memory_ids(self, tmp_db_path):
        """get_recent_memory_ids returns memory IDs from graph entries."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        await self._setup_searchable_data(store)
        memory_ids = await store.get_recent_memory_ids(limit=5)
        assert len(memory_ids) >= 1
        assert 1 in memory_ids


class TestGraphSubgraph:
    """Subgraph retrieval tests."""

    async def _setup_subgraph_data(self, store):
        nodes = [
            GraphNode(node_type="entity", value="小明", canonical_value="xiaoming"),
            GraphNode(node_type="entity", value="小红", canonical_value="xiaohong"),
            GraphNode(node_type="entity", value="朋友", canonical_value="friend"),
        ]
        node_map = await store.upsert_nodes(nodes)

        edge = GraphEdge(
            source_key="entity:xiaoming",
            target_key="entity:xiaohong",
            relation_type="朋友",
            source_memory_id=1,
        )
        edge_id = await store.add_edge(edge, node_map)

        entry = GraphEntry(
            entry_key="mem1:sub_entry",
            source_memory_id=1,
            session_id="s-sub",
            persona_id="p1",
            entry_type="relation",
            content="小明和小红是朋友",
            node_keys=["entity:xiaoming", "entity:xiaohong"],
            relation_type="朋友",
            metadata={"canonical_summary": "朋友关系", "importance": 0.8},
        )
        await store.add_entry(entry, node_map, edge_id)
        return node_map

    @pytest.mark.asyncio
    async def test_get_subgraph_for_memories(self, tmp_db_path):
        """get_subgraph_for_memories returns a complete subgraph snapshot."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        await self._setup_subgraph_data(store)
        snapshot = await store.get_subgraph_for_memories([1])

        assert "nodes" in snapshot
        assert "edges" in snapshot
        assert "entries" in snapshot
        assert "memories" in snapshot
        assert len(snapshot["entries"]) >= 1
        assert len(snapshot["nodes"]) >= 2

    @pytest.mark.asyncio
    async def test_get_subgraph_empty_memories(self, tmp_db_path):
        """get_subgraph_for_memories with empty list returns empty structures."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        snapshot = await store.get_subgraph_for_memories([])
        assert snapshot == {"nodes": [], "edges": [], "entries": [], "memories": []}

    @pytest.mark.asyncio
    async def test_get_subgraph_no_match(self, tmp_db_path):
        """get_subgraph_for_memories with unknown IDs returns empty."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        snapshot = await store.get_subgraph_for_memories([99999])
        assert snapshot == {"nodes": [], "edges": [], "entries": [], "memories": []}

    @pytest.mark.asyncio
    async def test_get_graph_snapshot(self, tmp_db_path):
        """get_graph_snapshot returns a recent graph overview."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        await self._setup_subgraph_data(store)
        snapshot = await store.get_graph_snapshot(limit_memories=5)
        assert "nodes" in snapshot
        assert "memories" in snapshot
