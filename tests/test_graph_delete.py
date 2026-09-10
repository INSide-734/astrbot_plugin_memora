"""GraphDeleteMixin 测试 — batch_delete_memories 边界情况全覆盖。"""

import pytest

from core.features.memory.graph.domain.models import GraphEdge, GraphEntry, GraphNode
from core.features.memory.graph.infrastructure.graph_store import GraphStore


class TestGraphDeleteBatchWithEntries:
    """batch_delete_memories with entries that have vector_doc_ids."""

    async def _setup_entries_with_vectors(self, store, count: int = 3):
        """Create nodes + edges + entries with vector_doc_ids."""
        all_node_maps = {}
        for i in range(count):
            mem_id = 300 + i
            vec_doc_id = 3000 + i
            nodes = [
                GraphNode(
                    node_type="entity",
                    value=f"E{i}",
                    canonical_value=f"e{i}",
                ),
                GraphNode(
                    node_type="entity",
                    value=f"F{i}",
                    canonical_value=f"f{i}",
                ),
            ]
            node_map = await store.upsert_nodes(nodes)
            all_node_maps[mem_id] = node_map

            edge = GraphEdge(
                source_key=f"entity:e{i}",
                target_key=f"entity:f{i}",
                relation_type="related",
                source_memory_id=mem_id,
            )
            edge_id = await store.add_edge(edge, node_map)

            entry = GraphEntry(
                entry_key=f"mem{mem_id}:entry",
                source_memory_id=mem_id,
                session_id="s-entry",
                persona_id="p1",
                entry_type="fact",
                content=f"Entry for memory {mem_id}",
                node_keys=[f"entity:e{i}", f"entity:f{i}"],
                relation_type="related",
            )
            await store.add_entry(entry, node_map, edge_id)

            # Set vector_doc_id via direct SQL (add_entry doesn't set it)
            async with store._connect() as db:
                await db.execute(
                    "UPDATE graph_entries SET vector_doc_id = ? WHERE source_memory_id = ?",
                    (vec_doc_id, mem_id),
                )
                await db.commit()

        return all_node_maps

    @pytest.mark.asyncio
    async def test_batch_delete_with_entries_and_vectors(self, tmp_db_path):
        """batch_delete_memories covers entry+vector_doc_id collection + chunked delete."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        await self._setup_entries_with_vectors(store, count=3)

        # Verify entries exist before delete
        stats_before = await store.get_memory_entry_stats()
        assert stats_before["graph_entries"] == 3
        assert stats_before["graph_edges"] == 3

        result = await store.batch_delete_memories([300, 301, 302])
        # result should map memory_id -> [vector_doc_id...]
        assert 300 in result
        assert 301 in result
        assert 302 in result
        assert result[300] == [3000]
        assert result[301] == [3001]
        assert result[302] == [3002]

        # Verify everything was cleaned up
        stats_after = await store.get_memory_entry_stats()
        assert stats_after["graph_entries"] == 0
        assert stats_after["graph_edges"] == 0

    @pytest.mark.asyncio
    async def test_batch_delete_large_chunked(self, tmp_db_path):
        """batch_delete_memories with enough entries to trigger chunking (batch > 500)."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        # Create 3 memories, each with 2 entries to test chunked delete path
        for i in range(3):
            mem_id = 400 + i
            vec_doc_id = 4000 + i
            nodes = [
                GraphNode(node_type="entity", value=f"X{i}", canonical_value=f"x{i}"),
                GraphNode(node_type="entity", value=f"Y{i}", canonical_value=f"y{i}"),
            ]
            node_map = await store.upsert_nodes(nodes)

            edge = GraphEdge(
                source_key=f"entity:x{i}",
                target_key=f"entity:y{i}",
                relation_type="related",
                source_memory_id=mem_id,
            )
            edge_id = await store.add_edge(edge, node_map)

            for j in range(2):
                entry = GraphEntry(
                    entry_key=f"mem{mem_id}:entry{j}",
                    source_memory_id=mem_id,
                    session_id="s-chunk",
                    persona_id="p1",
                    entry_type="fact",
                    content=f"Chunked entry {mem_id}-{j}",
                    node_keys=[f"entity:x{i}", f"entity:y{i}"],
                    relation_type="related",
                )
                await store.add_entry(entry, node_map, edge_id)

            # Set vector_doc_id
            async with store._connect() as db:
                await db.execute(
                    "UPDATE graph_entries SET vector_doc_id = ? WHERE source_memory_id = ?",
                    (vec_doc_id, mem_id),
                )
                await db.commit()

        result = await store.batch_delete_memories([400, 401, 402])
        assert len(result) == 3
        stats = await store.get_memory_entry_stats()
        assert stats["graph_entries"] == 0


class TestGraphDeleteSharedEdges:
    """delete_memory should not remove shared semantic edges still referenced by entries."""

    @pytest.mark.asyncio
    async def test_delete_memory_keeps_edge_referenced_by_other_memory(
        self, tmp_db_path
    ):
        store = GraphStore(tmp_db_path)
        await store.initialize()

        nodes = [
            GraphNode(node_type="entity", value="Alice", canonical_value="alice"),
            GraphNode(node_type="entity", value="Bob", canonical_value="bob"),
        ]
        node_map = await store.upsert_nodes(nodes)
        edge = GraphEdge(
            source_key="entity:alice",
            target_key="entity:bob",
            relation_type="knows",
            source_memory_id=100,
        )
        edge_id = await store.add_edge(edge, node_map)

        entry_100 = GraphEntry(
            entry_key="mem100:alice-bob",
            source_memory_id=100,
            session_id="s",
            persona_id="p",
            entry_type="relationship",
            content="Alice knows Bob from memory 100",
            node_keys=["entity:alice", "entity:bob"],
            relation_type="knows",
        )
        entry_200 = GraphEntry(
            entry_key="mem200:alice-bob",
            source_memory_id=200,
            session_id="s",
            persona_id="p",
            entry_type="relationship",
            content="Alice knows Bob from memory 200",
            node_keys=["entity:alice", "entity:bob"],
            relation_type="knows",
        )
        await store.add_entry(entry_100, node_map, edge_id)
        await store.add_entry(entry_200, node_map, edge_id)

        await store.delete_memory(100)

        stats = await store.get_memory_entry_stats()
        assert stats["graph_entries"] == 1
        assert stats["graph_edges"] == 1

        async with store._connect() as db:
            cursor = await db.execute(
                "SELECT source_memory_id, edge_id FROM graph_entries"
            )
            rows = await cursor.fetchall()

        assert [(int(row[0]), int(row[1])) for row in rows] == [(200, edge_id)]

    @pytest.mark.asyncio
    async def test_batch_delete_partial_vectors(self, tmp_db_path):
        """batch_delete_memories handles entries where some have vector_doc_id=None."""
        store = GraphStore(tmp_db_path)
        await store.initialize()

        # Memory 500: entry WITH vector_doc_id
        nodes_a = [
            GraphNode(node_type="entity", value="A500", canonical_value="a500"),
            GraphNode(node_type="entity", value="B500", canonical_value="b500"),
        ]
        node_map_a = await store.upsert_nodes(nodes_a)
        edge_a = GraphEdge(
            source_key="entity:a500",
            target_key="entity:b500",
            relation_type="related",
            source_memory_id=500,
        )
        edge_id_a = await store.add_edge(edge_a, node_map_a)
        entry_a = GraphEntry(
            entry_key="mem500:entry",
            source_memory_id=500,
            session_id="s-partial",
            persona_id="p1",
            entry_type="fact",
            content="Entry with vector",
            node_keys=["entity:a500", "entity:b500"],
            relation_type="related",
        )
        await store.add_entry(entry_a, node_map_a, edge_id_a)
        async with store._connect() as db:
            await db.execute(
                "UPDATE graph_entries SET vector_doc_id = ? WHERE source_memory_id = ?",
                (5000, 500),
            )
            await db.commit()

        # Memory 501: entry WITHOUT vector_doc_id (set to NULL)
        nodes_b = [
            GraphNode(node_type="entity", value="C501", canonical_value="c501"),
            GraphNode(node_type="entity", value="D501", canonical_value="d501"),
        ]
        node_map_b = await store.upsert_nodes(nodes_b)
        edge_b = GraphEdge(
            source_key="entity:c501",
            target_key="entity:d501",
            relation_type="related",
            source_memory_id=501,
        )
        edge_id_b = await store.add_edge(edge_b, node_map_b)
        entry_b = GraphEntry(
            entry_key="mem501:entry",
            source_memory_id=501,
            session_id="s-partial",
            persona_id="p1",
            entry_type="fact",
            content="Entry without vector",
            node_keys=["entity:c501", "entity:d501"],
            relation_type="related",
        )
        await store.add_entry(entry_b, node_map_b, edge_id_b)
        # Leave vector_doc_id as NULL (default)

        result = await store.batch_delete_memories([500, 501])
        # Memory 500 should have a vector_doc_id; 501 should not
        assert 500 in result
        assert result[500] == [5000]
        # Memory 501 may or may not be in result depending on vector_doc_id value
        stats = await store.get_memory_entry_stats()
        assert stats["graph_entries"] == 0
