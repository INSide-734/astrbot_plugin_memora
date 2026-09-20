"""验证 catalog 新 generation 发布后的旧派生行回收。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.features.memory.infrastructure.topic_catalog_schema import (
    create_topic_catalog_schema,
)
from core.features.memory.infrastructure.topic_catalog_store import TopicCatalogStore
from core.platform.composition import DerivedRebuildCoordinator


def _metadata(topic: str) -> str:
    """返回可参与 catalog 的最小 canonical metadata。"""

    return json.dumps(
        {
            "topics": [topic],
            "scope_key": "scope-a",
            "chat_type": "private",
            "privacy_level": "shared",
            "resolver_revision": "resolver-1",
            "source_provenance_complete": True,
            "status": "active",
        }
    )


async def _open_database(tmp_path) -> aiosqlite.Connection:
    """创建仅用于 generation 回收测试的 canonical 数据库。"""

    db = await aiosqlite.connect(tmp_path / "topic-rebuild.db")
    await db.execute(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            metadata TEXT NOT NULL,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    await create_topic_catalog_schema(db)
    await db.execute(
        "INSERT INTO documents VALUES(?,?,?,?,?)",
        (1, "正文", _metadata("旧话题"), "2026-01-01T00:00:00+00:00", "r1"),
    )
    await db.commit()
    return db


@pytest.mark.asyncio
async def test_successful_rebuild_retires_previous_generation(tmp_path) -> None:
    """新 generation 复核成功后只回收旧派生行并保留 canonical。"""

    db = await _open_database(tmp_path)
    try:
        store = TopicCatalogStore(db)
        assert (await store.rebuild_from_canonical(now=10.0))["generation"] == 1
        await db.execute(
            "UPDATE documents SET metadata=?, updated_at=? WHERE id=1",
            (_metadata("新话题"), "r2"),
        )
        await db.commit()

        validator = MagicMock()
        validator._get_document_count = AsyncMock(return_value=1)
        engine = MagicMock()
        engine.rebuild_graph_index = AsyncMock(return_value={"success": True})
        engine.note_proposal_pipeline = None
        coordinator = DerivedRebuildCoordinator(
            validator,
            engine,
            MagicMock(mode="disabled"),
            store,
        )

        result = await coordinator.rebuild_all(rebuild_indexes=False)

        assert result["success"] is True
        assert await store.generation_counts(1) == (0, 0)
        assert await store.generation_counts(2) == (1, 1)
        assert await (
            await db.execute("SELECT COUNT(*) FROM documents")
        ).fetchone() == (1,)
    finally:
        await db.close()


class _FakeClock:
    """可控时钟：``advance`` 只推进逻辑时间，不占用真实时间。"""

    def __init__(self, start: float = 1.0) -> None:
        self.now = start

    def time(self) -> float:
        """返回当前逻辑时间。"""

        return self.now

    async def advance(self, seconds: float) -> None:
        """推进逻辑时间，并保留一次协程让出模拟批次切换。"""

        await asyncio.sleep(0)
        self.now += seconds


class _PacedMappingStore(TopicCatalogStore):
    """按调用次序注入可控耗时，区分主回填轮与覆盖轮。"""

    def __init__(
        self,
        db: aiosqlite.Connection,
        clock: _FakeClock,
        backfill_seconds: float,
        cover_seconds: float,
    ) -> None:
        super().__init__(db)
        self._clock = clock
        self._backfill_seconds = backfill_seconds
        self._cover_seconds = cover_seconds
        self._replaced: set[tuple[int, int]] = set()

    async def replace_memory_mappings(
        self, memory_id: int, generation: int, **kwargs
    ) -> bool:
        """同一 generation 的同一 memory ID 第二次替换即为覆盖阶段重读。"""

        key = (generation, memory_id)
        seconds = (
            self._backfill_seconds if key not in self._replaced else self._cover_seconds
        )
        self._replaced.add(key)
        await self._clock.advance(seconds)
        return await super().replace_memory_mappings(memory_id, generation, **kwargs)


async def _insert_documents(db: aiosqlite.Connection, doc_ids: range) -> None:
    """追加 canonical 文档（每条 INSERT 由触发器登记一条 dirty）。"""

    for doc_id in doc_ids:
        await db.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?)",
            (
                doc_id,
                "正文",
                _metadata(f"话题{doc_id}"),
                "2026-01-01T00:00:00+00:00",
                f"r{doc_id}",
            ),
        )
    await db.commit()


@pytest.mark.asyncio
async def test_dirty_cover_renews_lease_before_publish(tmp_path, monkeypatch) -> None:
    """覆盖阶段累计耗时越过租约时不得消费 dirty 后再被 publish fence 放弃。"""

    import core.features.memory.infrastructure.topic_catalog_rebuild as rebuild_module

    clock = _FakeClock()
    monkeypatch.setattr(rebuild_module, "time", clock)

    db = await _open_database(tmp_path)
    try:
        await _insert_documents(db, range(2, 17))

        lease_seconds = 10.0
        # 16 条 dirty 逐批覆盖（每批 1s，累计 16s）> 单次租约 10s
        store = _PacedMappingStore(db, clock, backfill_seconds=1.0, cover_seconds=1.0)
        result = await store.rebuild_from_canonical(
            batch_size=1, lease_seconds=lease_seconds
        )
        state = await store.get_state()
        pending = await store.list_dirty(states=("pending", "failed", "running"))
        canonical_count = await (
            await db.execute("SELECT COUNT(*) FROM documents")
        ).fetchone()

        assert result["success"] is True
        assert state["status"] == "ready"
        assert pending == ()
        assert canonical_count == (16,)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_dirty_cover_abandon_keeps_dirty_when_lease_lost(
    tmp_path, monkeypatch
) -> None:
    """覆盖单批耗时越过租约且无法续租时，放弃 generation 但不得消费 dirty。"""

    import core.features.memory.infrastructure.topic_catalog_rebuild as rebuild_module

    clock = _FakeClock()
    monkeypatch.setattr(rebuild_module, "time", clock)

    db = await _open_database(tmp_path)
    try:
        await _insert_documents(db, range(2, 4))

        # 主回填每批 1s 可续租；覆盖轮单批 20s > 租约 10s，续租必然失败
        store = _PacedMappingStore(db, clock, backfill_seconds=1.0, cover_seconds=20.0)
        result = await store.rebuild_from_canonical(batch_size=1, lease_seconds=10.0)
        state = await store.get_state()
        unreconciled = await store.list_dirty(states=("pending", "failed"))
        canonical_count = await (
            await db.execute("SELECT COUNT(*) FROM documents")
        ).fetchone()

        assert result == {"success": False, "reason_code": "catalog_dirty_unresolved"}
        assert state["staging_generation"] is None
        assert len(unreconciled) == 3
        assert canonical_count == (3,)
    finally:
        await db.close()
