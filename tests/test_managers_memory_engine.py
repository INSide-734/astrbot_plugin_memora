"""MemoryEngine 测试 — 构造函数、统计、维护和初始化。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from core.managers.memory_engine import MemoryEngine


class TestMemoryEngineConstructor:
    """Tests for MemoryEngine.__init__."""

    def test_init_stores_basic_deps(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(
            db_path=":memory:",
            faiss_db=mock_faiss,
            config={"graph_memory_enabled": False},
        )
        assert engine.db_path == ":memory:"
        assert engine.faiss_db is mock_faiss
        assert engine.config is not None
        assert engine.graph_enabled is False

    def test_init_with_graph_enabled(self) -> None:
        mock_faiss = MagicMock()
        mock_graph = MagicMock()
        engine = MemoryEngine(
            db_path=":memory:",
            faiss_db=mock_faiss,
            graph_vector_db=mock_graph,
            config={"graph_memory_enabled": True, "atom_enabled": True},
        )
        assert engine.graph_enabled is True
        assert engine.atom_enabled is True
        assert engine.graph_vector_db is mock_graph

    def test_init_graph_enabled_with_atom_config_variant(self) -> None:
        """atom_enabled via graph_memory_atom_enabled fallback key."""
        mock_faiss = MagicMock()
        engine = MemoryEngine(
            db_path=":memory:",
            faiss_db=mock_faiss,
            config={"graph_memory_enabled": True, "graph_memory_atom_enabled": False},
        )
        assert engine.atom_enabled is False

    def test_init_default_config(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        assert engine.config == {}
        assert engine.graph_enabled is False
        assert engine.atom_enabled is True  # default

    def test_init_write_repair_config(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(
            db_path=":memory:",
            faiss_db=mock_faiss,
            config={"write_reliability.repair_enabled": False, "write_reliability.max_retries": 5},
        )
        assert engine._write_op_repair_enabled is False
        assert engine._write_journal._max_retries == 5

    def test_init_creates_sub_components(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        assert engine._retrieval is not None
        assert engine._write_journal is not None
        assert engine._schema is not None
        assert engine._maintenance is not None
        assert engine._pending_tasks is not None

    def test_init_placeholder_components_none(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        assert engine.text_processor is None
        assert engine.hybrid_retriever is None
        assert engine.graph_store is None
        assert engine.atom_store is None
        assert engine.dual_route_retriever is None
        assert engine.db_connection is None


class TestMemoryEngineCreateTrackedTask:
    """Tests for _create_tracked_task."""

    @pytest.mark.asyncio
    async def test_create_tracked_task_runs_and_cleans_up(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        side_effect_result = []

        async def sample_coro():
            side_effect_result.append("ran")

        engine._create_tracked_task(sample_coro())

        # Let the task run
        await asyncio.sleep(0.05)
        assert side_effect_result == ["ran"]

        # Task should be cleaned from pending set
        assert len(engine._pending_tasks) == 0

    @pytest.mark.asyncio
    async def test_create_tracked_task_multiple(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        results = []

        async def make_coro(i):
            results.append(i)

        for i in range(3):
            engine._create_tracked_task(make_coro(i))

        await asyncio.sleep(0.05)
        assert sorted(results) == [0, 1, 2]
        assert len(engine._pending_tasks) == 0


class TestMemoryEngineDelegation:
    """Tests for delegated methods that just pass through to sub-components."""

    def test_update_importance_delegates(self) -> None:
        mock_faiss = MagicMock()
        with patch.object(MemoryEngine, "update_memory", new_callable=AsyncMock) as mock_upd:
            engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
            asyncio.run(engine.update_importance(10, 0.8))
            mock_upd.assert_called_once_with(10, {"importance": 0.8})

    def test_consolidate_memories_delegates(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine._retrieval.consolidate = AsyncMock(return_value={"merged": 3})
        result = asyncio.run(engine.consolidate_memories())
        assert result == {"merged": 3}

    def test_register_trigger_delegates(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine._retrieval.register_trigger = AsyncMock()
        asyncio.run(engine.register_trigger("keyword", 42))
        engine._retrieval.register_trigger.assert_called_once_with("keyword", 42)

    def test_update_access_time_delegates(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine._maintenance.update_access_time = AsyncMock(return_value=True)
        result = asyncio.run(engine.update_access_time(10))
        assert result is True

    def test_update_access_times_batch_delegates(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine._maintenance.update_access_times_batch = AsyncMock(return_value=3)
        result = asyncio.run(engine.update_access_times_batch([1, 2, 3]))
        assert result == 3

    def test_get_session_memories_delegates(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        expected = [{"id": 1, "text": "hello"}]
        engine._maintenance.get_session_memories = AsyncMock(return_value=expected)
        result = asyncio.run(engine.get_session_memories("s1"))
        assert result == expected

    def test_apply_daily_decay_delegates(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine._maintenance.apply_daily_decay = AsyncMock(return_value=5)
        result = asyncio.run(engine.apply_daily_decay(0.01, 7))
        assert result == 5

    def test_cleanup_old_memories_delegates(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        engine._maintenance.cleanup_old_memories = AsyncMock(return_value=3)
        result = asyncio.run(engine.cleanup_old_memories(days_threshold=30))
        assert result == 3

    def test_get_statistics_delegates(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        expected = {"total_memories": 100}
        engine._maintenance.get_statistics = AsyncMock(return_value=expected)
        result = asyncio.run(engine.get_statistics())
        assert result == expected

    def test_maintain_storage_delegates(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        expected = {"vacuumed": True}
        engine._maintenance.maintain_storage = AsyncMock(return_value=expected)
        result = asyncio.run(engine.maintain_storage(vacuum=True))
        assert result == expected

    def test_rebuild_graph_index_delegates(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        expected = {"rebuilt": 5}
        engine._maintenance.rebuild_graph_index = AsyncMock(return_value=expected)
        result = asyncio.run(engine.rebuild_graph_index())
        assert result == expected


class TestMemoryEngineClose:
    """Tests for close() lifecycle method."""

    @pytest.mark.asyncio
    async def test_close_without_init(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)
        # close should be safe even without full init
        await engine.close()

    @pytest.mark.asyncio
    async def test_close_cancels_pending_tasks(self) -> None:
        mock_faiss = MagicMock()
        engine = MemoryEngine(db_path=":memory:", faiss_db=mock_faiss)

        # Add a long-running pending task
        async def slow_task():
            await asyncio.sleep(10)

        engine._create_tracked_task(slow_task())
        assert len(engine._pending_tasks) > 0

        await engine.close()
        assert len(engine._pending_tasks) == 0
