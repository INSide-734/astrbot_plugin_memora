"""MemoryEngine canonical 提交后派生维护钩子的回归测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.features.memory.application.memory_engine import MemoryEngine


@pytest.mark.asyncio
async def test_revision_invalidation_retries_transient_sqlite_lock() -> None:
    """派生 revision 失效遇到瞬时锁冲突时应重试，而非直接降级。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    source = MagicMock(revision_token="revision-17")
    store = MagicMock()
    store.load_sources = AsyncMock(return_value=[source])
    store.invalidate_for_source_revision = AsyncMock(
        side_effect=[aiosqlite.OperationalError("database is locked"), 1]
    )
    engine.memory_evolution_store = store

    await engine._invalidate_evolution_after_revision(17)

    assert store.invalidate_for_source_revision.await_count == 2
    store.invalidate_for_source_revision.assert_awaited_with(17, "revision-17")


@pytest.mark.asyncio
async def test_evolution_schedule_retries_transient_sqlite_lock() -> None:
    """演化任务入队遇到瞬时锁冲突时应重试，避免遗漏派生调度。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    source = MagicMock()
    manager = MagicMock()
    manager.store.load_sources = AsyncMock(return_value=[source])
    manager.schedule_consider = AsyncMock(
        side_effect=[aiosqlite.OperationalError("database is locked"), None]
    )
    engine.memory_evolution_manager = manager

    await engine._schedule_evolution_after_write(17)

    assert manager.schedule_consider.await_count == 2
    manager.schedule_consider.assert_awaited_with(source)
