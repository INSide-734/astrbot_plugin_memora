"""GraphMemoryManager 图产物原子替换与向量同步测试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from core.features.memory.application.graph_memory_manager import GraphMemoryManager
from core.features.memory.graph.infrastructure.graph_store import GraphReplaceResult
from core.features.recall.processors.graph_extractor import GraphExtractor


@dataclass
class _GraphEntryStub:
    """提供 Manager 向量同步所需的最小图条目字段。"""

    content: str
    metadata: dict


@dataclass
class _ExtractedResultStub:
    """提供图抽取结果的最小测试替身。"""

    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    entries: list = field(default_factory=list)


class TestGraphMemoryManagerConstructor:
    """验证 GraphMemoryManager 构造行为。"""

    def test_init_stores_dependencies(self) -> None:
        """构造器保存三个依赖并创建变更锁。"""
        graph_store = MagicMock()
        vector_retriever = MagicMock()
        extractor = GraphExtractor(config={})

        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        assert manager.graph_store is graph_store
        assert manager.graph_vector_retriever is vector_retriever
        assert manager.graph_extractor is extractor
        assert isinstance(manager._mutation_lock, asyncio.Lock)


@pytest.mark.asyncio
class TestGraphMemoryManagerIndexMemory:
    """验证 index_memory 的原子替换和向量补偿。"""

    async def test_index_memory_uses_atomic_replace_for_empty_graph(self) -> None:
        """空抽取结果仍原子删除旧图并清理该 source 的向量。"""
        graph_store = MagicMock()
        graph_store.replace_memory_graph = AsyncMock(
            return_value=GraphReplaceResult(entry_ids=[])
        )
        graph_store.update_entry_vector_doc_ids = AsyncMock()
        vector_retriever = MagicMock()
        vector_retriever.delete_entries_for_memory = AsyncMock(return_value=0)
        vector_retriever.add_entry = AsyncMock()
        extractor = MagicMock()
        extractor.extract.return_value = _ExtractedResultStub()
        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        await manager.index_memory(42, "测试内容", {"key": "value"})

        graph_store.replace_memory_graph.assert_awaited_once_with(42, [], [], [])
        vector_retriever.delete_entries_for_memory.assert_awaited_once_with(42)
        vector_retriever.add_entry.assert_not_awaited()
        graph_store.update_entry_vector_doc_ids.assert_awaited_once_with({})
        graph_store.delete_memory.assert_not_called()
        graph_store.upsert_nodes.assert_not_called()
        graph_store.add_edges.assert_not_called()
        graph_store.add_entries.assert_not_called()

    async def test_index_memory_with_entries(self) -> None:
        """原子替换后按顺序创建向量并回写条目标识。"""
        graph_store = MagicMock()
        graph_store.replace_memory_graph = AsyncMock(
            return_value=GraphReplaceResult(entry_ids=[301, 302])
        )
        graph_store.update_entry_vector_doc_ids = AsyncMock()
        vector_retriever = MagicMock()
        vector_retriever.delete_entries_for_memory = AsyncMock(return_value=2)
        vector_retriever.add_entry = AsyncMock(return_value=1001)
        extractor = MagicMock()
        entry1 = _GraphEntryStub(content="条目一", metadata={"topic": "t1"})
        entry2 = _GraphEntryStub(content="条目二", metadata={"topic": "t2"})
        extracted = _ExtractedResultStub(
            nodes=[{"key": "node1", "type": "entity"}],
            edges=[{"key": "edge1", "source": "a", "target": "b"}],
            entries=[entry1, entry2],
        )
        extractor.extract.return_value = extracted
        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        await manager.index_memory(42, "测试", {"key": "value"})

        graph_store.replace_memory_graph.assert_awaited_once_with(
            42,
            extracted.nodes,
            extracted.edges,
            extracted.entries,
        )
        vector_retriever.delete_entries_for_memory.assert_awaited_once_with(42)
        vector_retriever.add_entry.assert_has_awaits(
            [
                call("条目一", {"topic": "t1"}),
                call("条目二", {"topic": "t2"}),
            ]
        )
        graph_store.update_entry_vector_doc_ids.assert_awaited_once_with(
            {301: 1001, 302: 1001}
        )

    async def test_index_memory_id_count_mismatch_raises(self) -> None:
        """Store 返回的条目数量不匹配时显式失败。"""
        graph_store = MagicMock()
        graph_store.replace_memory_graph = AsyncMock(
            return_value=GraphReplaceResult(entry_ids=[301])
        )
        graph_store.update_entry_vector_doc_ids = AsyncMock()
        vector_retriever = MagicMock()
        vector_retriever.delete_entries_for_memory = AsyncMock(return_value=0)
        extractor = MagicMock()
        extractor.extract.return_value = _ExtractedResultStub(
            entries=[
                _GraphEntryStub(content="甲", metadata={}),
                _GraphEntryStub(content="乙", metadata={}),
            ]
        )
        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        with pytest.raises(RuntimeError, match="图条目标识数量不匹配"):
            await manager.index_memory(42, "测试", {})

        vector_retriever.delete_entries_for_memory.assert_awaited_once_with(42)
        graph_store.update_entry_vector_doc_ids.assert_not_awaited()

    async def test_index_memory_with_atoms_passed(self) -> None:
        """原子列表原样传给图抽取器。"""
        graph_store = MagicMock()
        graph_store.replace_memory_graph = AsyncMock(
            return_value=GraphReplaceResult(entry_ids=[])
        )
        graph_store.update_entry_vector_doc_ids = AsyncMock()
        vector_retriever = MagicMock()
        vector_retriever.delete_entries_for_memory = AsyncMock(return_value=0)
        extractor = MagicMock()
        extractor.extract.return_value = _ExtractedResultStub()
        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)
        atoms = [MagicMock()]

        await manager.index_memory(42, "测试", {"key": "value"}, atoms=atoms)

        extractor.extract.assert_called_once_with(
            42,
            "测试",
            {"key": "value"},
            atoms,
        )

    async def test_index_retry_purges_partial_vectors_before_readding(self) -> None:
        """首次半写失败后，重试会先清理残留再建立完整向量。"""
        graph_store = MagicMock()
        graph_store.replace_memory_graph = AsyncMock(
            side_effect=[
                GraphReplaceResult(entry_ids=[301, 302]),
                GraphReplaceResult(entry_ids=[401, 402]),
            ]
        )
        graph_store.update_entry_vector_doc_ids = AsyncMock()
        vector_retriever = MagicMock()
        vector_retriever.delete_entries_for_memory = AsyncMock(return_value=0)
        vector_retriever.add_entry = AsyncMock(
            side_effect=[1001, RuntimeError("模拟向量失败"), 2001, 2002]
        )
        extractor = MagicMock()
        extractor.extract.return_value = _ExtractedResultStub(
            entries=[
                _GraphEntryStub(content="甲", metadata={}),
                _GraphEntryStub(content="乙", metadata={}),
            ]
        )
        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        with pytest.raises(RuntimeError, match="模拟向量失败"):
            await manager.index_memory(42, "测试", {})
        await manager.index_memory(42, "测试", {})

        assert vector_retriever.delete_entries_for_memory.await_count == 2
        graph_store.update_entry_vector_doc_ids.assert_has_awaits(
            [call({301: 1001}), call({401: 2001, 402: 2002})]
        )


@pytest.mark.asyncio
class TestGraphMemoryManagerDeleteMemory:
    """验证单条图记忆删除。"""

    async def test_delete_memory_removes_sqlite_and_source_vectors(self) -> None:
        """删除会清理 SQLite 图行和该 source 的全部向量。"""
        graph_store = MagicMock()
        graph_store.delete_memory = AsyncMock(return_value=[1001, 1002])
        vector_retriever = MagicMock()
        vector_retriever.delete_entries_for_memory = AsyncMock(return_value=2)
        extractor = GraphExtractor(config={})
        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        await manager.delete_memory(42)

        graph_store.delete_memory.assert_awaited_once_with(42)
        vector_retriever.delete_entries_for_memory.assert_awaited_once_with(42)

    async def test_delete_memory_purges_vectors_without_sqlite_mapping(self) -> None:
        """SQLite 没有旧向量映射时仍按 source 清理历史残留。"""
        graph_store = MagicMock()
        graph_store.delete_memory = AsyncMock(return_value=[])
        vector_retriever = MagicMock()
        vector_retriever.delete_entries_for_memory = AsyncMock(return_value=1)
        extractor = GraphExtractor(config={})
        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        await manager.delete_memory(42)

        graph_store.delete_memory.assert_awaited_once_with(42)
        vector_retriever.delete_entries_for_memory.assert_awaited_once_with(42)

    async def test_delete_waits_until_index_vector_sync_finishes(self) -> None:
        """并发删除必须等待索引向量同步释放 mutation lock。"""
        graph_store = MagicMock()
        graph_store.replace_memory_graph = AsyncMock(
            return_value=GraphReplaceResult(entry_ids=[301])
        )
        graph_store.update_entry_vector_doc_ids = AsyncMock()
        graph_store.delete_memory = AsyncMock(return_value=[])
        vector_retriever = MagicMock()
        vector_retriever.delete_entries_for_memory = AsyncMock(return_value=0)
        add_started = asyncio.Event()
        release_add = asyncio.Event()

        async def paused_add_entry(_content: str, _metadata: dict) -> int:
            """暂停向量创建，暴露 Manager 锁的确定性测试窗口。"""
            add_started.set()
            await release_add.wait()
            return 1001

        vector_retriever.add_entry = AsyncMock(side_effect=paused_add_entry)
        extractor = MagicMock()
        extractor.extract.return_value = _ExtractedResultStub(
            entries=[_GraphEntryStub(content="甲", metadata={})]
        )
        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        index_task = asyncio.create_task(manager.index_memory(42, "测试", {}))
        await add_started.wait()
        delete_task = asyncio.create_task(manager.delete_memory(42))
        await asyncio.sleep(0)
        graph_store.delete_memory.assert_not_awaited()

        release_add.set()
        await index_task
        await delete_task
        graph_store.delete_memory.assert_awaited_once_with(42)


@pytest.mark.asyncio
class TestGraphMemoryManagerBatchDelete:
    """验证批量图记忆删除。"""

    async def test_batch_delete_empty_list(self) -> None:
        """空列表不会访问 Store 或向量后端。"""
        graph_store = MagicMock()
        vector_retriever = MagicMock()
        extractor = GraphExtractor(config={})
        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        await manager.batch_delete_memories([])

        graph_store.batch_delete_memories.assert_not_called()
        vector_retriever.delete_entries_for_memory.assert_not_called()

    async def test_batch_delete_normalizes_ids_and_purges_each_source(self) -> None:
        """批删会去重排序，并逐个清理 source-scoped 图向量。"""
        graph_store = MagicMock()
        graph_store.batch_delete_memories = AsyncMock(return_value={})
        vector_retriever = MagicMock()
        vector_retriever.delete_entries_for_memory = AsyncMock(return_value=0)
        extractor = GraphExtractor(config={})
        manager = GraphMemoryManager(graph_store, vector_retriever, extractor)

        await manager.batch_delete_memories([2, 1, 2])

        graph_store.batch_delete_memories.assert_awaited_once_with([1, 2])
        vector_retriever.delete_entries_for_memory.assert_has_awaits([call(1), call(2)])
