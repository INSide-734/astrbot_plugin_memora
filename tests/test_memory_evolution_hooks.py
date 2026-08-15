"""MemoryEngine canonical 提交后派生维护钩子的回归测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.features.memory.application.memory_engine import MemoryEngine


@pytest.mark.asyncio
async def test_revision_invalidation_delegates_retry_to_store() -> None:
    """失效钩子不得在 Store 已协调重试时再次获取全局写锁。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    source = MagicMock(revision_token="revision-17")
    store = MagicMock()
    store.load_sources = AsyncMock(return_value=[source])
    store.invalidate_for_source_revision = AsyncMock(return_value=1)
    engine.memory_evolution_store = store

    await engine._invalidate_evolution_after_revision(17)

    store.invalidate_for_source_revision.assert_awaited_once_with(17, "revision-17")


@pytest.mark.asyncio
async def test_evolution_schedule_delegates_retry_to_store() -> None:
    """调度钩子不得在 Store 已协调重试时再次获取全局写锁。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    source = MagicMock()
    manager = MagicMock()
    manager.store.load_sources = AsyncMock(return_value=[source])
    manager.schedule_consider = AsyncMock(return_value=None)
    engine.memory_evolution_manager = manager

    await engine._schedule_evolution_after_write(17)

    manager.schedule_consider.assert_awaited_once_with(source)


@pytest.mark.asyncio
async def test_mark_write_memory_skips_evolution_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mark_write 记忆写入后不得调度演化任务，并上报跳过观测。"""

    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        "core.features.memory.application.memory_engine_evolution_hooks.report_debug_event",
        lambda event_name, **fields: events.append({"event": event_name, **fields}),
    )

    async with aiosqlite.connect(":memory:") as conn:
        await conn.execute(
            "CREATE TABLE documents (id INTEGER PRIMARY KEY, text TEXT, metadata TEXT)"
        )
        await conn.execute(
            "INSERT INTO documents (id, text, metadata) VALUES (?, ?, ?)",
            (17, "low-confidence", '{"gate_disposition": "mark_write"}'),
        )
        await conn.commit()

        engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
        engine.db_connection = conn
        source = MagicMock()
        manager = MagicMock(mode="active")
        manager.store.load_sources = AsyncMock(return_value=[source])
        manager.schedule_consider = AsyncMock()
        engine.memory_evolution_manager = manager

        await engine._schedule_evolution_after_write(17)

    manager.schedule_consider.assert_not_awaited()
    assert events and events[-1]["reason_code"] == "evolution_gate_mark_write"
