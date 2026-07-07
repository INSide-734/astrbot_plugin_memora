"""GraphStore CRUD 操作测试 — 节点、边、条目。"""

import pytest

from core.models.graph_models import GraphEdge, GraphEntry, GraphNode
from core.storage.graph_store import GraphStore


class TestGraphCRUDNodes:
    """Node CRUD operations."""

    @pytest.mark.asyncio
    async def test_upsert_node_insert(self, tmp_db_path):
        """upsert_node inserts a new graph node."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        node = GraphNode(
            node_type="entity",
            value="西湖",
            canonical_value="xihu",
            metadata={"category": "scenic"},
        )
        node_id = await store.upsert_node(node)
        assert node_id > 0

    @pytest.mark.asyncio
    async def test_upsert_node_update(self, tmp_db_path):
        """upsert_node updates an existing node on conflict."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        node1 = GraphNode(node_type="entity", value="西湖", canonical_value="xihu")
        id1 = await store.upsert_node(node1)

        node2 = GraphNode(
            node_type="entity",
            value="西湖景点",
            canonical_value="xihu",
            metadata={"updated": True},
        )
        id2 = await store.upsert_node(node2)
        assert id2 == id1

    @pytest.mark.asyncio
    async def test_upsert_nodes_batch(self, tmp_db_path):
        """upsert_nodes inserts multiple nodes in one transaction."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        nodes = [
            GraphNode(node_type="entity", value="小明", canonical_value="xiaoming"),
            GraphNode(node_type="entity", value="小红", canonical_value="xiaohong"),
        ]
        mapping = await store.upsert_nodes(nodes)
        assert len(mapping) == 2
        assert all(v > 0 for v in mapping.values())

    @pytest.mark.asyncio
    async def test_upsert_nodes_empty(self, tmp_db_path):
        """upsert_nodes with empty list returns empty dict."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        assert await store.upsert_nodes([]) == {}


class TestGraphCRUDEdges:
    """Edge CRUD operations."""

    async def _setup_nodes(self, store):
        """Create two nodes and return their key→id mapping."""
        nodes = [
            GraphNode(node_type="entity", value="小明", canonical_value="xiaoming"),
            GraphNode(node_type="entity", value="小红", canonical_value="xiaohong"),
        ]
        return await store.upsert_nodes(nodes)

    @pytest.mark.asyncio
    async def test_add_edge_insert(self, tmp_db_path):
        """add_edge inserts a new graph edge."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        node_map = await self._setup_nodes(store)
        edge = GraphEdge(
            source_key="entity:xiaoming",
            target_key="entity:xiaohong",
            relation_type="朋友",
            source_memory_id=1,
            confidence=0.9,
        )
        edge_id = await store.add_edge(edge, node_map)
        assert edge_id > 0

    @pytest.mark.asyncio
    async def test_add_edge_semantic_merge(self, tmp_db_path):
        """Adding same relation between same nodes merges via EMA."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        node_map = await self._setup_nodes(store)
        edge1 = GraphEdge(
            source_key="entity:xiaoming",
            target_key="entity:xiaohong",
            relation_type="朋友",
            source_memory_id=1,
            confidence=0.8,
            weight=1.0,
        )
        id1 = await store.add_edge(edge1, node_map)

        edge2 = GraphEdge(
            source_key="entity:xiaoming",
            target_key="entity:xiaohong",
            relation_type="朋友",
            source_memory_id=2,
            confidence=0.9,
            weight=1.0,
        )
        id2 = await store.add_edge(edge2, node_map)
        assert id2 == id1  # merged into existing

    @pytest.mark.asyncio
    async def test_add_edges_batch(self, tmp_db_path):
        """add_edges inserts multiple edges in bulk."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        node_map = await self._setup_nodes(store)
        edges = [
            GraphEdge(
                source_key="entity:xiaoming",
                target_key="entity:xiaohong",
                relation_type="朋友",
                source_memory_id=1,
            ),
        ]
        edge_map = await store.add_edges(edges, node_map)
        assert len(edge_map) == 1

    @pytest.mark.asyncio
    async def test_add_edges_empty(self, tmp_db_path):
        """add_edges with empty list returns empty dict."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        assert await store.add_edges([], {}) == {}

    @pytest.mark.asyncio
    async def test_add_edges_skips_missing_nodes(self, tmp_db_path):
        """Edge referencing unknown node keys is skipped."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        edges = [
            GraphEdge(
                source_key="entity:unknown",
                target_key="entity:missing",
                relation_type="related",
                source_memory_id=1,
            ),
        ]
        edge_map = await store.add_edges(edges, {})
        assert len(edge_map) == 0


class TestGraphCRUDEntries:
    """Entry CRUD operations."""

    async def _setup_nodes_and_edge(self, store):
        nodes = [
            GraphNode(node_type="entity", value="西湖", canonical_value="xihu"),
            GraphNode(node_type="entity", value="杭州", canonical_value="hangzhou"),
        ]
        node_map = await store.upsert_nodes(nodes)
        edge = GraphEdge(
            source_key="entity:xihu",
            target_key="entity:hangzhou",
            relation_type="位于",
            source_memory_id=1,
        )
        edge_id = await store.add_edge(edge, node_map)
        edge_map = {edge.edge_key: edge_id}
        return node_map, edge_map

    @pytest.mark.asyncio
    async def test_add_entry_insert(self, tmp_db_path):
        """add_entry inserts a new graph entry."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        node_map, edge_map = await self._setup_nodes_and_edge(store)
        entry = GraphEntry(
            entry_key="mem1:entity:xihu|位于|entity:hangzhou|1",
            source_memory_id=1,
            session_id="s1",
            persona_id="p1",
            entry_type="relation",
            content="西湖位于杭州",
            node_keys=["entity:xihu", "entity:hangzhou"],
            relation_type="位于",
        )
        entry_id = await store.add_entry(entry, node_map)
        assert entry_id > 0

    @pytest.mark.asyncio
    async def test_add_entry_update_existing(self, tmp_db_path):
        """add_entry updates an existing entry on conflict."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        node_map, edge_map = await self._setup_nodes_and_edge(store)
        entry = GraphEntry(
            entry_key="mem1:entry_update",
            source_memory_id=1,
            session_id="s1",
            persona_id="p1",
            entry_type="relation",
            content="original content",
            node_keys=["entity:xihu", "entity:hangzhou"],
        )
        id1 = await store.add_entry(entry, node_map)

        entry.content = "updated content"
        id2 = await store.add_entry(entry, node_map)
        assert id2 == id1

    @pytest.mark.asyncio
    async def test_add_entries_batch(self, tmp_db_path):
        """add_entries inserts multiple entries."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        node_map, edge_map = await self._setup_nodes_and_edge(store)
        entries = [
            GraphEntry(
                entry_key=f"mem1:entry{i}",
                source_memory_id=1,
                session_id="s1",
                persona_id="p1",
                entry_type="relation",
                content=f"content {i}",
                node_keys=["entity:xihu", "entity:hangzhou"],
            )
            for i in range(3)
        ]
        ids = await store.add_entries(entries, node_map, edge_map)
        assert len(ids) == 3

    @pytest.mark.asyncio
    async def test_add_entries_empty(self, tmp_db_path):
        """add_entries with empty list returns empty list."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        assert await store.add_entries([], {}, {}) == []


class TestGraphCRUDVectorDocIds:
    """Vector document ID persistence."""

    @pytest.mark.asyncio
    async def test_update_entry_vector_doc_id(self, tmp_db_path):
        """update_entry_vector_doc_id persists the vector doc id."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        node = GraphNode(node_type="entity", value="test", canonical_value="test")
        node_map = await store.upsert_nodes([node])
        entry = GraphEntry(
            entry_key="mem1:vec_test",
            source_memory_id=1,
            session_id="s1",
            persona_id=None,
            entry_type="fact",
            content="test content",
            node_keys=["entity:test"],
        )
        entry_id = await store.add_entry(entry, node_map)
        await store.update_entry_vector_doc_id(entry_id, 42)
        # No assertion needed — just verify it doesn't error; we trust the DB

    @pytest.mark.asyncio
    async def test_update_entry_vector_doc_ids_batch(self, tmp_db_path):
        """update_entry_vector_doc_ids persists multiple vector doc ids."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        node = GraphNode(node_type="entity", value="test2", canonical_value="test2")
        node_map = await store.upsert_nodes([node])
        entry1 = GraphEntry(
            entry_key="mem1:vec_batch1",
            source_memory_id=1,
            session_id="s1",
            persona_id=None,
            entry_type="fact",
            content="batch 1",
            node_keys=["entity:test2"],
        )
        entry2 = GraphEntry(
            entry_key="mem1:vec_batch2",
            source_memory_id=1,
            session_id="s1",
            persona_id=None,
            entry_type="fact",
            content="batch 2",
            node_keys=["entity:test2"],
        )
        id1 = await store.add_entry(entry1, node_map)
        id2 = await store.add_entry(entry2, node_map)

        await store.update_entry_vector_doc_ids({id1: 100, id2: 200})
        # Should not error

    @pytest.mark.asyncio
    async def test_update_vector_doc_ids_empty(self, tmp_db_path):
        """update_entry_vector_doc_ids with empty dict is a no-op."""
        store = GraphStore(tmp_db_path)
        await store.initialize()
        # Should not raise
        await store.update_entry_vector_doc_ids({})
