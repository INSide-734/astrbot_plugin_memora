"""验证 topic catalog 的生命周期回填、就绪判断和安全降级。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.features.memory.infrastructure.topic_catalog_schema import (
    create_topic_catalog_schema,
)
from core.features.memory.infrastructure.topic_catalog_store import TopicCatalogStore
from core.platform.composition import DatabaseSetup, DerivedRebuildCoordinator


async def _open_catalog(tmp_path):
    """创建包含 canonical documents 和 catalog schema 的独立数据库。"""

    db = await aiosqlite.connect(tmp_path / "catalog-lifecycle.db")
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
    await db.commit()
    return db


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


@pytest.mark.asyncio
async def test_rebuild_from_canonical_publishes_generation(tmp_path) -> None:
    """回填应发布 ready generation，且不删除 canonical documents。"""

    db = await _open_catalog(tmp_path)
    try:
        await db.executemany(
            "INSERT INTO documents VALUES(?,?,?,?,?)",
            [
                (1, "正文一", _metadata("话题一"), "2026-01-01T00:00:00+00:00", "r1"),
                (2, "正文二", _metadata("话题二"), "2026-01-02T00:00:00+00:00", "r2"),
            ],
        )
        await db.commit()

        result = await TopicCatalogStore(db).rebuild_from_canonical(
            batch_size=1,
            now=10.0,
        )
        state = await (
            await db.execute(
                """
                SELECT status, active_generation, backfill_cursor, backfill_total
                FROM topic_catalog_state WHERE id = 1
                """
            )
        ).fetchone()
        canonical_count = await (
            await db.execute("SELECT COUNT(*) FROM documents")
        ).fetchone()
        mapping_count = await (
            await db.execute("SELECT COUNT(*) FROM memory_topic_sources")
        ).fetchone()

        assert result["success"] is True
        assert state == ("ready", 1, 2, 2)
        assert canonical_count == (2,)
        assert mapping_count == (2,)
        skipped = await TopicCatalogStore(db).rebuild_from_canonical(now=20.0)
        assert skipped["status"] == "skipped"
        assert skipped["generation"] == 1
        assert skipped["reason_code"] == "catalog_ready"

    finally:
        await db.close()


@pytest.mark.asyncio
async def test_empty_canonical_rebuild_publishes_empty_ready_generation(
    tmp_path,
) -> None:
    """空 canonical 也应发布可读的空 generation，而不是保持未就绪。"""

    db = await _open_catalog(tmp_path)
    try:
        result = await TopicCatalogStore(db).rebuild_from_canonical(now=10.0)
        state = await (
            await db.execute(
                "SELECT status, active_generation, staging_generation, "
                "backfill_cursor, backfill_total FROM topic_catalog_state WHERE id=1"
            )
        ).fetchone()

        assert result["success"] is True
        assert result["generation"] == 1
        assert state == ("ready", 1, None, 0, 0)
        assert await TopicCatalogStore(db).readable_generation() == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_cancelled_rebuild_keeps_resumable_cursor(tmp_path) -> None:
    """取消回填应保留 staging cursor，重启接管过期 lease 后完成。"""

    class CancellingStore(TopicCatalogStore):
        """在首批成功后于下一 source 处模拟取消。"""

        calls = 0

        async def replace_memory_mappings(self, memory_id, generation, **kwargs):
            """完成首条 mapping 后在下一条前传播取消。"""

            self.calls += 1
            if self.calls == 2:
                raise asyncio.CancelledError
            return await super().replace_memory_mappings(
                memory_id,
                generation,
                **kwargs,
            )

    db = await _open_catalog(tmp_path)
    try:
        await db.executemany(
            "INSERT INTO documents VALUES(?,?,?,?,?)",
            [
                (1, "正文一", _metadata("话题一"), "2026-01-01T00:00:00+00:00", "r1"),
                (2, "正文二", _metadata("话题二"), "2026-01-02T00:00:00+00:00", "r2"),
            ],
        )
        await db.commit()

        with pytest.raises(asyncio.CancelledError):
            await CancellingStore(db).rebuild_from_canonical(
                batch_size=1,
                lease_seconds=5.0,
                now=10.0,
            )
        interrupted = await (
            await db.execute(
                """
                SELECT status, staging_generation, backfill_cursor, backfill_total
                FROM topic_catalog_state WHERE id = 1
                """
            )
        ).fetchone()
        assert interrupted == ("backfilling", 1, 1, 2)

        result = await TopicCatalogStore(db).rebuild_from_canonical(
            batch_size=1,
            lease_seconds=5.0,
            now=20.0,
        )
        completed = await (
            await db.execute(
                "SELECT status, active_generation, backfill_cursor FROM topic_catalog_state"
            )
        ).fetchone()
        assert result["success"] is True
        assert completed == ("ready", 1, 2)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_dirty_canonical_change_rebuilds_new_generation_and_clears_reconcile(
    tmp_path,
) -> None:
    """canonical 变化后应被识别为 stale/dirty，并在重建后切换新 generation。"""

    db = await _open_catalog(tmp_path)
    store = TopicCatalogStore(db)
    try:
        await db.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?)",
            (1, "正文", _metadata("旧话题"), "2026-01-01T00:00:00+00:00", "r1"),
        )
        await db.commit()
        coordinator = DerivedRebuildCoordinator(
            MagicMock(), MagicMock(), catalog_store=store
        )

        first = await store.rebuild_from_canonical(batch_size=1, now=10.0)
        assert first["generation"] == 1
        assert await coordinator.catalog_needs_reconcile() is False

        await db.execute(
            "UPDATE documents SET metadata=?, updated_at=? WHERE id=1",
            (_metadata("新话题"), "r2"),
        )
        await db.commit()
        assert await coordinator.catalog_needs_reconcile() is True

        second = await store.rebuild_from_canonical(batch_size=1, now=20.0)
        state = await store.get_state()
        dirty = await store.list_dirty(states=("completed",))
        topics = await store.list_scope_topics(
            2,
            "scope-a",
            "shared",
            resolver_revision="resolver-1",
            chat_type="private",
        )

        assert second["success"] is True
        assert second["generation"] == 2
        assert state["status"] == "ready"
        assert state["active_generation"] == 2
        assert state["published_dirty_watermark"] == state["canonical_write_watermark"]
        assert [(item.memory_id, item.state) for item in dirty] == [(1, "completed")]
        assert [item["display_topic"] for item in topics] == ["新话题"]
        assert await coordinator.catalog_needs_reconcile() is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_database_setup_reconciles_catalog_when_indexes_are_consistent() -> None:
    """索引一致但 catalog 未就绪时仍应独立触发 catalog 回填。"""

    class Coordinator:
        """记录启动期 readiness 探针和重建参数。"""

        def __init__(self):
            self.rebuild_calls = []

        async def catalog_needs_reconcile(self):
            """声明 catalog 需要回填。"""

            return True

        async def rebuild_all(self, **kwargs):
            """记录不重复重建 FTS 的调用。"""

            self.rebuild_calls.append(kwargs)
            return {"success": True, "reason_code": "catalog_ready"}

    validator = MagicMock()
    validator.check_consistency = AsyncMock(
        return_value=SimpleNamespace(
            is_consistent=True,
            needs_rebuild=False,
            reason="indexes_consistent",
            documents_count=2,
            bm25_count=2,
            vector_count=2,
        )
    )
    coordinator = Coordinator()

    result = await DatabaseSetup.auto_rebuild_index_if_needed(
        validator,
        MagicMock(),
        coordinator,
    )

    assert result["success"] is True
    assert coordinator.rebuild_calls == [{"rebuild_indexes": False}]


@pytest.mark.asyncio
async def test_database_setup_uses_coordinator_when_indexes_need_rebuild() -> None:
    """索引不一致时启动维护仍应进入统一协调器。"""

    class Coordinator:
        """记录索引不一致时的统一重建调用。"""

        def __init__(self):
            self.rebuild_calls = []

        async def rebuild_all(self, **kwargs):
            """返回成功的统一派生重建结果。"""

            self.rebuild_calls.append(kwargs)
            return {"success": True, "reason_code": "derived_rebuild_completed"}

    validator = MagicMock()
    validator.check_consistency = AsyncMock(
        return_value=SimpleNamespace(
            is_consistent=False,
            needs_rebuild=True,
            reason="indexes_stale",
            documents_count=2,
            bm25_count=1,
            vector_count=1,
        )
    )
    coordinator = Coordinator()

    result = await DatabaseSetup.auto_rebuild_index_if_needed(
        validator,
        MagicMock(),
        coordinator,
    )

    assert result["success"] is True
    assert coordinator.rebuild_calls == [{}]


@pytest.mark.asyncio
async def test_coordinator_orders_catalog_before_graph() -> None:
    """统一协调器应在索引后、图重建前执行 catalog 阶段。"""

    order = []

    class Catalog:
        """提供最小 catalog 生命周期端口。"""

        async def rebuild_from_canonical(self):
            """记录 catalog 阶段。"""

            order.append("catalog")
            return {"success": True, "generation": 1}

        async def verify_published_generation(self, generation):
            """确认发布后的 generation。"""

            return generation == 1

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=1)

    async def rebuild_indexes(_engine):
        """记录索引阶段。"""

        order.append("indexes")
        return {"success": True}

    validator.rebuild_indexes.side_effect = rebuild_indexes
    engine = MagicMock()

    async def rebuild_graph():
        """记录图阶段。"""

        order.append("graph")
        return {"success": True}

    engine.rebuild_graph_index.side_effect = rebuild_graph
    engine.note_proposal_pipeline = None
    manager = MagicMock(mode="disabled")

    result = await DerivedRebuildCoordinator(
        validator,
        engine,
        manager,
        Catalog(),
    ).rebuild_all()

    assert result["success"] is True
    assert order == ["indexes", "catalog", "graph"]


@pytest.mark.asyncio
async def test_ready_catalog_with_pending_dirty_stays_readable() -> None:
    """已有安全 generation 时未收敛 dirty 不得把目录降级。"""

    catalog = MagicMock()
    catalog.get_state = AsyncMock(
        return_value={
            "status": "ready",
            "active_generation": 3,
            "staging_generation": None,
            "canonical_write_watermark": 9,
            "published_dirty_watermark": 8,
        }
    )
    catalog.list_dirty = AsyncMock(return_value=(object(),))
    catalog.verify_published_generation = AsyncMock(return_value=True)
    catalog.mark_degraded = AsyncMock(return_value=True)

    result = await DerivedRebuildCoordinator(
        MagicMock(), MagicMock(), catalog_store=catalog
    ).catalog_readiness_decision()

    assert result == {
        "catalog_decision": "ready",
        "safe_baseline": True,
        "reason_code": "catalog_ready_pending_reconcile",
    }
    catalog.mark_degraded.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_publish_verification_failure_degrades_catalog() -> None:
    """发布后聚合复核失败时必须返回失败并标记目录降级。"""

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=0)
    validator.rebuild_indexes = AsyncMock(return_value={"success": True})
    engine = MagicMock()
    engine.rebuild_graph_index = AsyncMock(return_value={"success": True})
    engine.note_proposal_pipeline = None
    manager = MagicMock(mode="disabled")
    catalog = MagicMock()
    catalog.rebuild_from_canonical = AsyncMock(
        return_value={"success": True, "generation": 1}
    )
    catalog.verify_published_generation = AsyncMock(return_value=False)
    catalog.mark_degraded = AsyncMock(return_value=True)

    result = await DerivedRebuildCoordinator(
        validator,
        engine,
        manager,
        catalog,
    ).rebuild_all()

    assert result["success"] is False
    assert result["stages"]["catalog"]["status"] == "failed"
    catalog.mark_degraded.assert_awaited_once_with("catalog_post_publish_verify_failed")


@pytest.mark.asyncio
async def test_dirty_reconcile_does_not_mutate_foreign_active_lease(tmp_path) -> None:
    """foreign dirty lease 存活时，重建 owner 不得提前收敛 dirty。"""

    db = await _open_catalog(tmp_path)
    try:
        sequence = await TopicCatalogStore(db).register_dirty(1, "add")
        store = TopicCatalogStore(db)
        claimed = await store.claim_dirty("foreign", now=10.0, lease_seconds=100.0)
        assert claimed and claimed[0].sequence == sequence
        assert await store.begin_generation(
            1, "rebuild", 1000.0, backfill_total=0, now=10.0
        )
        assert not await store.mark_dirty_reconciled(
            "rebuild", up_to_sequence=sequence, covered_memory_ids={1}, now=20.0
        )
        state = await store.get_state()
        assert not await store.publish_generation(
            1,
            "rebuild",
            start_watermark=state["staging_start_watermark"],
            published_dirty_watermark=state["staging_start_watermark"],
            canonical_snapshot_revision="snapshot",
            expected_source_count=0,
            expected_mapping_count=0,
            now=20.0,
        )

        row = await (
            await db.execute("SELECT state, lease_owner_token FROM topic_catalog_dirty")
        ).fetchone()
        assert row == ("running", "foreign")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_rebuild_fences_canonical_write_before_publish(tmp_path) -> None:
    """回填期间发生 canonical 写入时不得发布过时 generation。"""

    class WritingStore(TopicCatalogStore):
        """在首条 mapping 完成后注入一次确定性的 canonical 写入。"""

        def __init__(self, db):
            super().__init__(db)
            self.inserted = False

        async def mark_dirty_reconciled(self, owner_token, **kwargs):
            """在 publish 前追加 canonical 行，触发最终 watermark fence。"""

            if not self.inserted:
                self.inserted = True
                db = self._db
                assert db is not None
                await db.execute(
                    "INSERT INTO documents VALUES(?,?,?,?,?)",
                    (
                        2,
                        "并发正文",
                        _metadata("并发话题"),
                        "2026-01-02T00:00:00+00:00",
                        "r2",
                    ),
                )
                await db.commit()
            return await super().mark_dirty_reconciled(owner_token, **kwargs)

    db = await _open_catalog(tmp_path)
    try:
        await db.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?)",
            (1, "正文", _metadata("原话题"), "2026-01-01T00:00:00+00:00", "r1"),
        )
        await db.commit()

        store = WritingStore(db)
        result = await store.rebuild_from_canonical(batch_size=1, now=10.0)
        state = await store.get_state()
        canonical_count = await (
            await db.execute("SELECT COUNT(*) FROM documents")
        ).fetchone()
        pending = await store.list_dirty(states=("pending",))

        assert result == {"success": False, "reason_code": "catalog_publish_fenced"}
        assert state["status"] == "degraded"
        assert state["active_generation"] is None
        assert state["staging_generation"] is None
        assert canonical_count == (2,)
        assert [(item.memory_id, item.sequence) for item in pending] == [(2, 2)]
        assert await store.readable_generation() is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_catalog_publish_without_generation_degrades() -> None:
    """缺少已发布 generation 时协调器必须拒绝报告 catalog ready。"""

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=0)
    validator.rebuild_indexes = AsyncMock(return_value={"success": True})
    engine = MagicMock()
    engine.rebuild_graph_index = AsyncMock(return_value={"success": True})
    engine.note_proposal_pipeline = None
    manager = MagicMock(mode="disabled")
    catalog = MagicMock()
    catalog.rebuild_from_canonical = AsyncMock(return_value={"success": True})
    catalog.mark_degraded = AsyncMock(return_value=True)

    result = await DerivedRebuildCoordinator(
        validator,
        engine,
        manager,
        catalog,
    ).rebuild_all()

    assert result["success"] is False
    catalog.mark_degraded.assert_awaited_once_with("catalog_generation_missing")


@pytest.mark.asyncio
async def test_begin_generation_preserves_or_updates_resume_total(tmp_path) -> None:
    """generation 续作区分保留旧总数和显式空 canonical 总数。"""

    db = await _open_catalog(tmp_path)
    try:
        store = TopicCatalogStore(db)
        assert await store.begin_generation(
            1, "owner-one", 100.0, backfill_total=2, now=1.0
        )
        assert await store.begin_generation(
            1, "owner-two", 200.0, backfill_total=None, now=101.0
        )
        assert (await store.get_state())["backfill_total"] == 2
        assert await store.begin_generation(
            1, "owner-three", 300.0, backfill_total=0, now=201.0
        )
        assert (await store.get_state())["backfill_total"] == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_failed_second_generation_restores_previous_active_generation(
    tmp_path,
) -> None:
    """第二个 generation 的发布后复核失败时必须恢复旧 active。"""

    class FailingSecondGenerationStore(TopicCatalogStore):
        """只让第二个 generation 的发布后聚合复核失败。"""

        async def verify_published_generation(self, generation: int) -> bool:
            """保留首个 generation 的真实校验，拒绝第二个 generation。"""

            if generation == 2:
                return False
            return await super().verify_published_generation(generation)

    db = await _open_catalog(tmp_path)
    try:
        await db.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?)",
            (1, "正文", _metadata("旧话题"), "2026-01-01T00:00:00+00:00", "r1"),
        )
        await db.commit()
        initial_store = TopicCatalogStore(db)
        assert (await initial_store.rebuild_from_canonical(batch_size=1, now=10.0))[
            "generation"
        ] == 1
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
        catalog = FailingSecondGenerationStore(db)
        coordinator = DerivedRebuildCoordinator(
            validator,
            engine,
            MagicMock(mode="disabled"),
            catalog,
        )

        result = await coordinator.rebuild_all(rebuild_indexes=False)
        state = await catalog.get_state()
        document_count = await (
            await db.execute("SELECT COUNT(*) FROM documents")
        ).fetchone()

        assert result["success"] is False
        assert result["stages"]["catalog"]["status"] == "failed"
        assert state["status"] == "ready"
        assert state["active_generation"] == 1
        assert state["staging_generation"] is None
        assert state["published_dirty_watermark"] < state["canonical_write_watermark"]
        assert await catalog.generation_counts(1) == (1, 1)
        assert await catalog.generation_counts(2) == (0, 0)
        assert document_count == (1,)
        assert await coordinator.catalog_needs_reconcile() is True
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_mark_degraded_cleans_only_captured_staging_generation(tmp_path) -> None:
    """降级只能清理 staging 行，不能删除 active 或 canonical 行。"""

    db = await _open_catalog(tmp_path)
    try:
        await db.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?)",
            (1, "正文", _metadata("话题"), "2026-01-01T00:00:00+00:00", "r1"),
        )
        await db.commit()
        store = TopicCatalogStore(db)
        assert (await store.rebuild_from_canonical(now=10.0))["generation"] == 1
        assert await store.begin_generation(
            2,
            "staging-owner",
            100.0,
            backfill_total=1,
            now=20.0,
        )
        assert await store.replace_memory_mappings(
            1,
            2,
            owner_token="staging-owner",
            now=20.0,
        )

        assert await store.mark_degraded("catalog_manual_degraded", now=21.0)
        state = await store.get_state()
        canonical_count = await (
            await db.execute("SELECT COUNT(*) FROM documents")
        ).fetchone()

        assert state["status"] == "degraded"
        assert state["active_generation"] == 1
        assert state["staging_generation"] is None
        assert await store.generation_counts(1) == (1, 1)
        assert await store.generation_counts(2) == (0, 0)
        assert canonical_count == (1,)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_dirty_reconciliation_requires_staging_coverage(tmp_path) -> None:
    """无 staging 覆盖证明的 dirty 不得被完成或用于发布。"""

    db = await _open_catalog(tmp_path)
    try:
        store = TopicCatalogStore(db)
        sequence = await store.register_dirty(1, "delete")
        assert await store.begin_generation(
            1,
            "rebuild-owner",
            100.0,
            backfill_total=0,
            now=1.0,
        )

        assert not await store.mark_dirty_reconciled(
            "rebuild-owner", up_to_sequence=sequence, now=2.0
        )
        state = await store.get_state()
        assert not await store.publish_generation(
            1,
            "rebuild-owner",
            start_watermark=state["staging_start_watermark"],
            published_dirty_watermark=state["staging_start_watermark"],
            canonical_snapshot_revision="snapshot",
            expected_source_count=0,
            expected_mapping_count=0,
            now=2.0,
        )
        dirty = await store.list_dirty(states=("pending",))
        assert [(item.memory_id, item.sequence) for item in dirty] == [(1, sequence)]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_advance_cursor_is_monotonic_id_watermark(tmp_path) -> None:
    """cursor 是 keyset id 水位：只能单调前进，不与 backfill_total 比较。"""

    db = await _open_catalog(tmp_path)
    try:
        store = TopicCatalogStore(db)
        assert await store.begin_generation(
            1,
            "rebuild-owner",
            100.0,
            backfill_total=0,
            now=1.0,
        )
        # 单调：先推进到 5，再回退到 3 必须失败
        assert await store.advance_backfill_cursor(
            1,
            "rebuild-owner",
            5,
            now=2.0,
        )
        assert not await store.advance_backfill_cursor(
            1,
            "rebuild-owner",
            3,
            now=3.0,
        )
        assert (await store.get_state())["backfill_cursor"] == 5
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_initializer_blocks_scheduler_without_safe_catalog_decision(
    tmp_path,
) -> None:
    """目录未形成明确安全决定时不得启动 SummaryScheduler。"""

    from core.platform.composition.plugin_initializer import PluginInitializer
    from core.shared.errors import InitializationError

    config_manager = MagicMock()
    config_manager.get.side_effect = lambda key, default=None: {
        "topic_segmentation.legacy_backfill.enabled": False,
        "topic_segmentation.legacy_backfill.batch_size": 50,
        "topic_segmentation.legacy_backfill.max_backfill_per_run": 500,
    }.get(key, default)
    initializer = PluginInitializer(MagicMock(), config_manager, str(tmp_path))
    initializer._faiss_checker.load_vec_db_class = MagicMock(return_value=MagicMock())
    scheduler = SimpleNamespace(start=AsyncMock(), close=AsyncMock())
    db = MagicMock()
    db.close = AsyncMock()
    engine = MagicMock()
    engine.close = AsyncMock()
    conversation_store = MagicMock()
    conversation_store.close = AsyncMock()
    identity_runtime = MagicMock()
    identity_runtime.close = AsyncMock()
    initializer._component_factory.build_all = AsyncMock(
        return_value={
            "db": db,
            "graph_db": None,
            "memory_engine": engine,
            "memory_processor": MagicMock(),
            "memory_quarantine_store": MagicMock(),
            "memory_quality_gate": MagicMock(),
            "gate_runtime": MagicMock(),
            "conversation_manager": MagicMock(store=conversation_store),
            "identity_runtime": identity_runtime,
            "index_validator": MagicMock(),
            "decay_scheduler": None,
            "injection_decision_store": None,
            "injection_decision_recorder": None,
            "memory_evolution_store": None,
            "memory_evolution_manager": None,
            "realtime_hub": None,
            "summary_scheduler": scheduler,
            "summary_llm_limiter": MagicMock(),
            "catalog_maintenance_result": {
                "catalog_decision": "blocked",
                "safe_baseline": False,
                "reason_code": "catalog_not_ready",
            },
        }
    )
    initializer._initialize_cognitive_components = AsyncMock()
    initializer._create_prompt_protection_service = MagicMock(return_value=None)

    with pytest.raises(InitializationError, match="topic_catalog_startup_unresolved"):
        await initializer._run_full_init()

    scheduler.start.assert_not_awaited()
    scheduler.close.assert_awaited_once()
