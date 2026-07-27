"""GraphMemoryManager 测试 — 图工件 CRUD 和同步。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest

from core.managers.graph_memory_manager import GraphMemoryManager
from core.processors.graph_extractor import GraphExtractor


class TestGraphMemoryManagerConstructor:
    """Tests for GraphMemoryManager.__init__."""

    def test_init_stores_dependencies(self) -> None:
        graph_store = MagicMock()
        vector_retriever = MagicMock()
        extractor = GraphExtractor(config={})

        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)
        assert manager.graph_store is graph_store
        assert manager.graph_vector_retriever is vector_retriever
        assert manager.graph_extractor is extractor


@pytest.mark.asyncio
class TestGraphMemoryManagerIndexMemory:
    """Tests for index_memory method."""

    async def test_index_memory_deletes_existing_first(self) -> None:
        graph_store = MagicMock()
        graph_store.delete_memory = AsyncMock(return_value=[])
        graph_store.upsert_nodes = AsyncMock(return_value={})
        graph_store.add_edges = AsyncMock(return_value={})
        graph_store.add_entries = AsyncMock(return_value=[])
        graph_store.update_entry_vector_doc_ids = AsyncMock()

        vector_retriever = MagicMock()
        vector_retriever.add_entry = AsyncMock()

        extractor = MagicMock()
        from dataclasses import dataclass, field

        @dataclass
        class ExtractedResult:
            nodes: list = field(default_factory=list)
            edges: list = field(default_factory=list)
            entries: list = field(default_factory=list)

        extractor.extract.return_value = ExtractedResult()

        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        await manager.index_memory(42, "test content", {"key": "val"})

        # delete_memory should be called for the old graph data
        graph_store.delete_memory.assert_called_once_with(42)

    async def test_index_memory_with_entries(self) -> None:
        graph_store = MagicMock()
        graph_store.delete_memory = AsyncMock(return_value=[])
        graph_store.upsert_nodes = AsyncMock(return_value={"node1": 101})
        graph_store.add_edges = AsyncMock(return_value={"edge1": 201})
        graph_store.add_entries = AsyncMock(return_value=[301, 302])
        graph_store.update_entry_vector_doc_ids = AsyncMock()

        vector_retriever = MagicMock()
        vector_retriever.add_entry = AsyncMock(return_value=1001)

        extractor = MagicMock()
        from dataclasses import dataclass

        @dataclass
        class GraphEntry:
            content: str
            metadata: dict

        @dataclass
        class ExtractedResult:
            nodes: list
            edges: list
            entries: list

        entry1 = GraphEntry(content="entry one", metadata={"topic": "t1"})
        entry2 = GraphEntry(content="entry two", metadata={"topic": "t2"})

        extractor.extract.return_value = ExtractedResult(
            nodes=[{"key": "node1", "type": "entity"}],
            edges=[{"key": "edge1", "source": "a", "target": "b"}],
            entries=[entry1, entry2],
        )

        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)
        await manager.index_memory(42, "test", {"key": "val"})

        # Should upsert nodes, edges, entries
        graph_store.upsert_nodes.assert_called_once()
        graph_store.add_edges.assert_called_once()
        graph_store.add_entries.assert_called_once()

        # Should add 2 vector entries (one per graph entry)
        assert vector_retriever.add_entry.call_count == 2
        vector_retriever.add_entry.assert_has_calls(
            [
                call("entry one", {"topic": "t1"}),
                call("entry two", {"topic": "t2"}),
            ]
        )

        # Should update entry vector doc ids
        graph_store.update_entry_vector_doc_ids.assert_called_once_with(
            {301: 1001, 302: 1001}
        )

    async def test_index_memory_no_entries_returns_early(self) -> None:
        graph_store = MagicMock()
        graph_store.delete_memory = AsyncMock(return_value=[])

        vector_retriever = MagicMock()
        extractor = MagicMock()
        from dataclasses import dataclass, field

        @dataclass
        class ExtractedResult:
            nodes: list = field(default_factory=list)
            edges: list = field(default_factory=list)
            entries: list = field(default_factory=list)

        extractor.extract.return_value = ExtractedResult()  # empty entries

        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)
        await manager.index_memory(42, "test", {})

        # Should not proceed beyond delete + extract
        graph_store.delete_memory.assert_called_once_with(42)
        graph_store.upsert_nodes.assert_not_called()
        graph_store.add_edges.assert_not_called()
        graph_store.add_entries.assert_not_called()

    async def test_index_memory_id_count_mismatch_raises(self) -> None:
        graph_store = MagicMock()
        graph_store.delete_memory = AsyncMock(return_value=[])
        graph_store.upsert_nodes = AsyncMock(return_value={})
        graph_store.add_edges = AsyncMock(return_value={})
        graph_store.add_entries = AsyncMock(
            return_value=[301]
        )  # only 1 id, but 2 entries
        graph_store.update_entry_vector_doc_ids = AsyncMock()

        vector_retriever = MagicMock()
        extractor = MagicMock()
        from dataclasses import dataclass

        @dataclass
        class GraphEntry:
            content: str
            metadata: dict

        @dataclass
        class ExtractedResult:
            nodes: list
            edges: list
            entries: list

        extractor.extract.return_value = ExtractedResult(
            nodes=[],
            edges=[],
            entries=[
                GraphEntry(content="a", metadata={}),
                GraphEntry(content="b", metadata={}),
            ],
        )

        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        with pytest.raises(RuntimeError, match="graph entry id count mismatch"):
            await manager.index_memory(42, "test", {})

    async def test_index_memory_with_atoms_passed(self) -> None:
        """atoms should be forwarded to extractor."""
        graph_store = MagicMock()
        graph_store.delete_memory = AsyncMock(return_value=[])
        graph_store.upsert_nodes = AsyncMock(return_value={})
        graph_store.add_edges = AsyncMock(return_value={})
        graph_store.add_entries = AsyncMock(return_value=[])

        vector_retriever = MagicMock()
        extractor = MagicMock()
        from dataclasses import dataclass, field

        @dataclass
        class ExtractedResult:
            nodes: list = field(default_factory=list)
            edges: list = field(default_factory=list)
            entries: list = field(default_factory=list)

        extractor.extract.return_value = ExtractedResult()

        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        atoms = [MagicMock()]
        await manager.index_memory(42, "test", {"key": "val"}, atoms=atoms)

        extractor.extract.assert_called_once_with(42, "test", {"key": "val"}, atoms)


@pytest.mark.asyncio
class TestGraphMemoryManagerDeleteMemory:
    """Tests for delete_memory method."""

    async def test_delete_memory_removes_entries_and_vectors(self) -> None:
        graph_store = MagicMock()
        graph_store.delete_memory = AsyncMock(return_value=[1001, 1002])

        vector_retriever = MagicMock()
        vector_retriever.delete_entry = AsyncMock()

        extractor = GraphExtractor(config={})
        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        await manager.delete_memory(42)

        graph_store.delete_memory.assert_called_once_with(42)
        assert vector_retriever.delete_entry.call_count == 2
        vector_retriever.delete_entry.assert_has_calls([call(1001), call(1002)])

    async def test_delete_memory_no_vectors(self) -> None:
        graph_store = MagicMock()
        graph_store.delete_memory = AsyncMock(return_value=[])

        vector_retriever = MagicMock()
        extractor = GraphExtractor(config={})
        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        await manager.delete_memory(42)

        graph_store.delete_memory.assert_called_once_with(42)
        vector_retriever.delete_entry.assert_not_called()


@pytest.mark.asyncio
class TestGraphMemoryManagerBatchDelete:
    """Tests for batch_delete_memories method."""

    async def test_batch_delete_empty_list(self) -> None:
        graph_store = MagicMock()
        vector_retriever = MagicMock()
        extractor = GraphExtractor(config={})
        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        await manager.batch_delete_memories([])

        graph_store.batch_delete_memories.assert_not_called()

    async def test_batch_delete_with_values(self) -> None:
        graph_store = MagicMock()
        graph_store.batch_delete_memories = AsyncMock(
            return_value={
                1: [1001, 1002],
                2: [1003],
            }
        )

        vector_retriever = MagicMock()
        vector_retriever.delete_entry = AsyncMock()

        extractor = GraphExtractor(config={})
        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        await manager.batch_delete_memories([1, 2])

        graph_store.batch_delete_memories.assert_called_once_with([1, 2])
        assert vector_retriever.delete_entry.call_count == 3
        vector_retriever.delete_entry.assert_has_calls(
            [call(1001), call(1002), call(1003)], any_order=True
        )
