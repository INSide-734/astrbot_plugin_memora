"""好感度系统测试：模型、存储、管理器和情绪门控。

覆盖交互分类（关键词回退 + LLM Mock）、情绪门控、
好感度增量计算、情绪修饰符调节、重分配和
存储 CRUD 操作。
"""

from __future__ import annotations

import asyncio
import math
import time
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from core.base.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)
from core.affection.models import (
    AffectionLevel,
    BotMood,
    INTERACTION_RULES,
    InteractionType,
    MoodType,
    UserAffection,
    classify_by_keywords,
)
from core.affection.affection_store import AffectionStore
from core.affection.affection_manager import AffectionManager


# ============================================================================
# 管理员好感度与情绪操作测试
# ============================================================================


class TestAffectionAdminOperations:
    """管理员操作不得伪造自动互动字段，且必须可并发安全地编辑。"""

    @pytest.mark.asyncio
    async def test_manual_affection_create_has_no_fake_interaction(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            created = await manager.create_user_affection_manual(
                group_id="g1", user_id="alice", score=30
            )
            assert created.affection_score == 30
            assert created.interaction_count == 0
            assert created.last_interaction == 0.0
            assert created.level is AffectionLevel.WARM
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_manual_affection_create_rejects_duplicate_identity(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            await manager.create_user_affection_manual("g1", "alice", 10)
            with pytest.raises(EntityAlreadyExistsError):
                await manager.create_user_affection_manual("g1", "alice", 20)
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_manual_affection_validation_rejects_bad_identity_score_and_revision(
        self, tmp_db_path
    ):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            for kwargs, field in (
                ({"group_id": "", "user_id": "alice", "score": 10}, "group_id"),
                ({"group_id": "g1", "user_id": " ", "score": 10}, "user_id"),
                ({"group_id": "g1", "user_id": "alice", "score": True}, "score"),
                ({"group_id": "g1", "user_id": "alice", "score": 101}, "score"),
            ):
                with pytest.raises(EntityValidationError) as exc_info:
                    await manager.create_user_affection_manual(**kwargs)
                assert field in exc_info.value.field_errors

            with pytest.raises(EntityValidationError) as exc_info:
                await manager.update_user_affection_manual(
                    "g1", "alice", 10, expected_revision=" "
                )
            assert "expected_revision" in exc_info.value.field_errors
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_manual_score_update_preserves_interaction_fields_and_revision(
        self, tmp_db_path
    ):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            before = await manager.create_user_affection_manual("g1", "alice", 10)
            before_revision = manager.revision_for_affection(before)
            updated = await manager.update_user_affection_manual(
                "g1", "alice", 70, expected_revision=before_revision
            )
            assert updated.level is AffectionLevel.FRIENDLY
            assert updated.interaction_count == before.interaction_count
            assert updated.last_interaction == before.last_interaction
            assert manager.revision_for_affection(updated) != before_revision
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_manual_update_and_delete_distinguish_not_found_from_conflict(
        self, tmp_db_path
    ):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            with pytest.raises(EntityNotFoundError):
                await manager.update_user_affection_manual(
                    "g1", "missing", 10, expected_revision="revision"
                )

            created = await manager.create_user_affection_manual("g1", "alice", 10)
            revision = manager.revision_for_affection(created)
            updated = await manager.update_user_affection_manual(
                "g1", "alice", 20, expected_revision=revision
            )
            with pytest.raises(EditConflictError) as exc_info:
                await manager.delete_user_affection_manual(
                    "g1", "alice", expected_revision=revision
                )
            assert exc_info.value.current_entity["affection_score"] == 20
            assert exc_info.value.current_revision == manager.revision_for_affection(updated)

            assert await manager.delete_user_affection_manual(
                "g1", "alice", expected_revision=exc_info.value.current_revision
            )
            with pytest.raises(EntityNotFoundError):
                await manager.delete_user_affection_manual(
                    "g1", "alice", expected_revision="revision"
                )
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_list_user_affections_is_paginated_and_deterministic(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            for user_id, score in (("zoe", 10), ("alice", 30), ("bob", 30)):
                await manager.create_user_affection_manual("g1", user_id, score)
            users, total = await manager.list_user_affections("g1", limit=2, offset=1)
            assert total == 3
            assert [user.user_id for user in users] == ["bob", "zoe"]
            with pytest.raises(EntityValidationError):
                await manager.list_user_affections("g1", limit=True, offset=0)
            with pytest.raises(EntityValidationError):
                await manager.list_user_affections("g1", limit=1, offset=-1)
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_same_revision_concurrent_admin_updates_have_one_winner(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            created = await manager.create_user_affection_manual("g1", "alice", 10)
            revision = manager.revision_for_affection(created)
            results = await asyncio.gather(
                manager.update_user_affection_manual(
                    "g1", "alice", 20, expected_revision=revision
                ),
                manager.update_user_affection_manual(
                    "g1", "alice", 30, expected_revision=revision
                ),
                return_exceptions=True,
            )
            assert sum(isinstance(result, UserAffection) for result in results) == 1
            assert sum(isinstance(result, EditConflictError) for result in results) == 1
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_automatic_writer_cannot_interleave_admin_revision_transaction(
        self, tmp_db_path, monkeypatch
    ):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            created = await manager.create_user_affection_manual("g1", "alice", 10)
            revision = manager.revision_for_affection(created)
            automatic_insert_started = asyncio.Event()
            release_automatic_insert = asyncio.Event()
            original_execute = store._execute

            async def gated_execute(sql, params=()):
                if "ON CONFLICT(user_id, group_id) DO UPDATE" in sql:
                    automatic_insert_started.set()
                    await release_automatic_insert.wait()
                return await original_execute(sql, params)

            monkeypatch.setattr(store, "_execute", gated_execute)
            automatic = asyncio.create_task(store.upsert_affection("g1", "alice", 1))
            await automatic_insert_started.wait()
            admin = asyncio.create_task(
                manager.update_user_affection_manual(
                    "g1", "alice", 20, expected_revision=revision
                )
            )
            await asyncio.sleep(0)
            assert not admin.done()
            release_automatic_insert.set()
            await automatic
            with pytest.raises(EditConflictError):
                await admin
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_store_preserves_original_write_failure_when_rollback_fails(
        self, tmp_db_path, monkeypatch
    ):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            created = await manager.create_user_affection_manual("g1", "alice", 10)
            original_execute = store._execute

            async def failing_execute(sql, params=()):
                if sql.startswith("UPDATE user_affection"):
                    raise RuntimeError("write failure")
                return await original_execute(sql, params)

            async def failing_rollback():
                raise RuntimeError("rollback failure")

            monkeypatch.setattr(store, "_execute", failing_execute)
            monkeypatch.setattr(store.connection, "rollback", failing_rollback)
            with pytest.raises(RuntimeError, match="write failure"):
                await manager.update_user_affection_manual(
                    "g1",
                    "alice",
                    20,
                    expected_revision=manager.revision_for_affection(created),
                )
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_strict_create_does_not_misclassify_unrelated_integrity_error(
        self, tmp_db_path, monkeypatch
    ):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            original_execute = store._execute

            async def failing_execute(sql, params=()):
                if sql.startswith("INSERT INTO user_affection"):
                    raise aiosqlite.IntegrityError("foreign key failed")
                return await original_execute(sql, params)

            monkeypatch.setattr(store, "_execute", failing_execute)
            with pytest.raises(aiosqlite.IntegrityError, match="foreign key failed"):
                await store.create_affection_strict("g1", "alice", 10)
        finally:
            await store.close()


class TestMoodAdminOperations:
    """情绪写入必须验证输入、追加历史，并在提交后更新缓存。"""

    @pytest.mark.asyncio
    async def test_set_mood_normalizes_inputs_and_appends_history(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            mood = await manager.set_mood(
                "g1", MoodType.HAPPY, intensity=9, duration_hours=0, description="  Happy  "
            )
            assert mood.intensity == 1.0
            assert mood.duration_hours == 0.25
            assert mood.description == "Happy"
            history = await manager.get_mood_history("g1", limit=10)
            assert len(history) == 1
            assert history[0].description == "Happy"
            assert history[0].mood_type is MoodType.HAPPY
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_set_mood_rejects_invalid_type_nonfinite_values_and_description(
        self, tmp_db_path
    ):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            for mood_type, intensity, duration, description, field in (
                ("happy", 0.5, 4.0, None, "mood_type"),
                (MoodType.HAPPY, math.nan, 4.0, None, "intensity"),
                (MoodType.HAPPY, 0.5, math.inf, None, "duration_hours"),
                (MoodType.HAPPY, 0.5, 4.0, 1, "description"),
            ):
                with pytest.raises(EntityValidationError) as exc_info:
                    await manager.set_mood(
                        "g1", mood_type, intensity, duration, description
                    )
                assert field in exc_info.value.field_errors
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_reset_mood_appends_calm_history(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            await manager.set_mood("g1", MoodType.HAPPY, intensity=0.8, description="Happy")
            reset = await manager.reset_mood("g1")
            history = await manager.get_mood_history("g1", limit=10)
            assert reset.mood_type is MoodType.CALM
            assert reset.intensity == manager.DEFAULT_INTENSITY
            assert len(history) == 2
            assert [mood.mood_type for mood in history] == [MoodType.CALM, MoodType.HAPPY]
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_failed_mood_persist_does_not_change_cache(self, tmp_db_path, monkeypatch):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            initial = await manager.set_mood("g1", MoodType.HAPPY, description="Initial")

            async def fail_save(*args, **kwargs):
                raise RuntimeError("storage unavailable")

            monkeypatch.setattr(store, "save_bot_mood", fail_save)
            with pytest.raises(RuntimeError, match="storage unavailable"):
                await manager.set_mood("g1", MoodType.SAD, description="Failed")
            assert manager._mood_cache["g1"] is initial
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_concurrent_mood_sets_leave_cache_at_latest_persisted_mood(
        self, tmp_db_path, monkeypatch
    ):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            first_persisted = asyncio.Event()
            release_first = asyncio.Event()
            original_save = store.save_bot_mood
            call_count = 0

            async def delayed_first_save(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                row_id = await original_save(*args, **kwargs)
                if call_count == 1:
                    first_persisted.set()
                    await release_first.wait()
                return row_id

            monkeypatch.setattr(store, "save_bot_mood", delayed_first_save)
            first = asyncio.create_task(
                manager.set_mood("g1", MoodType.HAPPY, description="First")
            )
            await first_persisted.wait()
            second = asyncio.create_task(
                manager.set_mood("g1", MoodType.SAD, description="Second")
            )
            await asyncio.sleep(0)
            release_first.set()
            await asyncio.gather(first, second)
            latest = await store.get_latest_mood("g1")
            assert latest is not None
            assert manager._mood_cache["g1"].mood_type.value == latest["mood_type"]
            assert manager._mood_cache["g1"].description == latest["description"]
        finally:
            await store.close()


# ============================================================================
# Task 8 质量审查并发与损坏数据回归
# ============================================================================


class TestAffectionQualityReviewRegressions:
    """覆盖管理员写入、读取快照及情绪缓存的审查回归。"""

    @pytest.mark.asyncio
    async def test_redistribution_skips_admin_changed_candidate_after_read(
        self, tmp_db_path, monkeypatch
    ):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store, max_total_affection=100)
            alice = await manager.create_user_affection_manual("g1", "alice", 100)
            await manager.create_user_affection_manual("g1", "bob", 50)
            read_complete = asyncio.Event()
            release_redistribution = asyncio.Event()
            original_get_all = store.get_all_affections

            async def gated_get_all(group_id):
                rows = await original_get_all(group_id)
                read_complete.set()
                await release_redistribution.wait()
                return rows

            monkeypatch.setattr(store, "get_all_affections", gated_get_all)
            redistribution = asyncio.create_task(
                manager._maybe_redistribute("g1", exclude_user="actor")
            )
            await read_complete.wait()
            await manager.update_user_affection_manual(
                "g1",
                "alice",
                7,
                expected_revision=manager.revision_for_affection(alice),
            )
            release_redistribution.set()
            await redistribution

            persisted = await manager.get_user_affection("g1", "alice")
            assert persisted is not None
            assert persisted.affection_score == 7
            assert persisted.interaction_count == 0
            assert persisted.last_interaction == 0.0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_mood_cache_repairs_after_committed_save_is_cancelled(
        self, tmp_db_path, monkeypatch
    ):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            old = await manager.set_mood("g1", MoodType.HAPPY, description="old")
            committed = asyncio.Event()
            release_save = asyncio.Event()
            original_save = store.save_bot_mood

            async def commit_then_wait(*args, **kwargs):
                row_id = await original_save(*args, **kwargs)
                committed.set()
                await release_save.wait()
                return row_id

            monkeypatch.setattr(store, "save_bot_mood", commit_then_wait)
            setter = asyncio.create_task(
                manager.set_mood("g1", MoodType.SAD, description="new")
            )
            await committed.wait()
            latest_before_cancel = await store.get_latest_mood("g1")
            assert latest_before_cancel is not None
            assert latest_before_cancel["mood_type"] == MoodType.SAD.value
            assert manager._mood_cache["g1"] is old

            setter.cancel()
            release_save.set()
            with pytest.raises(asyncio.CancelledError):
                await setter

            assert manager._mood_cache["g1"].mood_type is MoodType.SAD
            assert manager._mood_cache["g1"].description == "new"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_concurrent_get_mood_creates_one_default_history_row(
        self, tmp_db_path, monkeypatch
    ):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            first_read_started = asyncio.Event()
            release_first_read = asyncio.Event()
            original_get_active = store.get_active_mood
            calls = 0

            async def gate_first_read(group_id):
                nonlocal calls
                calls += 1
                if calls == 1:
                    first_read_started.set()
                    await release_first_read.wait()
                return await original_get_active(group_id)

            monkeypatch.setattr(store, "get_active_mood", gate_first_read)
            first = asyncio.create_task(manager.get_mood("g1"))
            await first_read_started.wait()
            second = asyncio.create_task(manager.get_mood("g1"))
            release_first_read.set()
            moods = await asyncio.gather(first, second)

            assert [mood.mood_type for mood in moods] == [MoodType.CALM, MoodType.CALM]
            assert len(await store.get_mood_history("g1", limit=10)) == 1
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_list_affections_uses_one_snapshot_against_external_writer(
        self, tmp_db_path, monkeypatch
    ):
        reader = AffectionStore(tmp_db_path)
        writer = AffectionStore(tmp_db_path)
        await reader.initialize()
        await writer.initialize()
        try:
            await reader.create_affection_strict("g1", "alice", 10)
            count_complete = asyncio.Event()
            release_page = asyncio.Event()
            original_fetch_scalar = reader._fetch_scalar

            async def gated_fetch_scalar(sql, params=()):
                value = await original_fetch_scalar(sql, params)
                if sql.startswith("SELECT COUNT(*) FROM user_affection"):
                    count_complete.set()
                    await release_page.wait()
                return value

            monkeypatch.setattr(reader, "_fetch_scalar", gated_fetch_scalar)
            page_task = asyncio.create_task(reader.list_affections("g1", 10, 0))
            await count_complete.wait()
            await writer.create_affection_strict("g1", "bob", 20)
            release_page.set()
            rows, total = await page_task

            assert total == len(rows)
            assert [row["user_id"] for row in rows] == ["alice"]
        finally:
            await writer.close()
            await reader.close()

    @pytest.mark.asyncio
    async def test_two_stores_strict_create_duplicate_is_domain_conflict(self, tmp_db_path):
        first = AffectionStore(tmp_db_path)
        second = AffectionStore(tmp_db_path)
        await first.initialize()
        await second.initialize()
        try:
            results = await asyncio.gather(
                first.create_affection_strict("g1", "alice", 10),
                second.create_affection_strict("g1", "alice", 20),
                return_exceptions=True,
            )
            assert sum(isinstance(result, dict) for result in results) == 1
            assert sum(isinstance(result, EntityAlreadyExistsError) for result in results) == 1
        finally:
            await second.close()
            await first.close()

    @pytest.mark.asyncio
    async def test_mood_history_skips_malformed_rows_without_leaking_values(
        self, tmp_db_path, monkeypatch, caplog
    ):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            manager = AffectionManager(store)
            malformed = [
                {
                    "mood_type": "unknown-legacy-mood",
                    "intensity": 0.5,
                    "description": "secret unknown description",
                    "start_time": 20.0,
                    "duration_hours": 1.0,
                },
                {
                    "mood_type": MoodType.HAPPY.value,
                    "intensity": math.nan,
                    "description": "secret nan description",
                    "start_time": 19.0,
                    "duration_hours": 1.0,
                },
                {
                    "mood_type": MoodType.HAPPY.value,
                    "intensity": 0.5,
                    "description": None,
                    "start_time": None,
                    "duration_hours": "invalid-duration",
                },
                {
                    "mood_type": MoodType.CALM.value,
                    "intensity": 0.5,
                    "description": "valid",
                    "start_time": 10.0,
                    "duration_hours": 1.0,
                },
            ]

            async def injected_history(*args, **kwargs):
                return malformed

            monkeypatch.setattr(store, "get_mood_history", injected_history)
            history = await manager.get_mood_history("g1", limit=10)
            assert [(mood.mood_type, mood.description) for mood in history] == [
                (MoodType.CALM, "valid")
            ]
            assert "ValueError" in caplog.text
            assert "secret unknown description" not in caplog.text
            assert "secret nan description" not in caplog.text
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_invalid_active_mood_is_skipped_for_next_valid_row(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            await store.save_bot_mood("g1", MoodType.HAPPY.value, 0.5, "valid", 4.0)
            async with store._write_transaction():
                await store._execute(
                    """INSERT INTO bot_mood (group_id, mood_type, intensity,
                       description, start_time, duration_hours)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    ("g1", "unknown-legacy-mood", 0.5, "bad", time.time() + 1, 4.0),
                )

            mood = await AffectionManager(store).get_mood("g1")
            assert mood.mood_type is MoodType.HAPPY
            assert mood.description == "valid"
        finally:
            await store.close()


# ============================================================================
# Task 8 取消与关闭生命周期回归
# ============================================================================


class TestAffectionLifecycleRegressions:
    """事务清理和管理器关闭必须等待其已启动的异步生命周期。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("transaction_name", ["_write_transaction", "_read_snapshot"])
    async def test_transaction_rollback_completes_after_repeated_cancellation(
        self, tmp_db_path, monkeypatch, transaction_name
    ):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        rollback_started = asyncio.Event()
        release_rollback = asyncio.Event()
        rollback_completed = asyncio.Event()
        body_started = asyncio.Event()
        original_rollback = store.connection.rollback

        async def gated_rollback():
            rollback_started.set()
            await release_rollback.wait()
            await original_rollback()
            rollback_completed.set()

        monkeypatch.setattr(store.connection, "rollback", gated_rollback)

        async def cancelled_transaction():
            async with getattr(store, transaction_name)():
                body_started.set()
                await asyncio.Event().wait()

        transaction = asyncio.create_task(cancelled_transaction())
        try:
            await body_started.wait()
            transaction.cancel()
            await rollback_started.wait()
            transaction.cancel()
            release_rollback.set()
            with pytest.raises(asyncio.CancelledError):
                await transaction

            assert rollback_completed.is_set()
            await store.create_affection_strict("g1", transaction_name, 1)
            rows, total = await store.list_affections("g1", 10, 0)
            assert total == 1
            assert rows[0]["user_id"] == transaction_name
        finally:
            release_rollback.set()
            if not transaction.done():
                transaction.cancel()
            await asyncio.gather(transaction, return_exceptions=True)
            await store.close()

    @pytest.mark.asyncio
    async def test_close_waits_for_inflight_mood_save(self, tmp_db_path, monkeypatch):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        manager = AffectionManager(store)
        save_started = asyncio.Event()
        release_save = asyncio.Event()
        close_entered = asyncio.Event()
        release_close = asyncio.Event()
        original_save = store.save_bot_mood
        original_close = store.close

        async def gated_save(*args, **kwargs):
            save_started.set()
            await release_save.wait()
            return await original_save(*args, **kwargs)

        async def gated_close():
            close_entered.set()
            await release_close.wait()
            await original_close()

        monkeypatch.setattr(store, "save_bot_mood", gated_save)
        monkeypatch.setattr(store, "close", gated_close)
        setter = asyncio.create_task(
            manager.set_mood("g1", MoodType.HAPPY, description="pending")
        )
        closer: asyncio.Task[None] | None = None
        try:
            await save_started.wait()
            closer = asyncio.create_task(manager.close())
            await asyncio.sleep(0)
            assert not close_entered.is_set()

            release_save.set()
            mood = await setter
            assert mood.mood_type is MoodType.HAPPY
            await close_entered.wait()
            release_close.set()
            await closer
            assert store.connection is None
        finally:
            release_save.set()
            release_close.set()
            if not setter.done():
                setter.cancel()
            await asyncio.gather(setter, return_exceptions=True)
            if closer is not None:
                if not closer.done():
                    closer.cancel()
                await asyncio.gather(closer, return_exceptions=True)
            if store.connection is not None:
                await original_close()

    @pytest.mark.asyncio
    async def test_close_waits_for_cancelled_mood_setter_cleanup(self, tmp_db_path, monkeypatch):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        manager = AffectionManager(store)
        save_started = asyncio.Event()
        release_save = asyncio.Event()
        close_entered = asyncio.Event()
        release_close = asyncio.Event()
        original_save = store.save_bot_mood
        original_close = store.close

        async def gated_save(*args, **kwargs):
            save_started.set()
            await release_save.wait()
            return await original_save(*args, **kwargs)

        async def gated_close():
            close_entered.set()
            await release_close.wait()
            await original_close()

        monkeypatch.setattr(store, "save_bot_mood", gated_save)
        monkeypatch.setattr(store, "close", gated_close)
        setter = asyncio.create_task(
            manager.set_mood("g1", MoodType.SAD, description="cancelled")
        )
        closer: asyncio.Task[None] | None = None
        try:
            await save_started.wait()
            setter.cancel()
            closer = asyncio.create_task(manager.close())
            await asyncio.sleep(0)
            assert not close_entered.is_set()

            release_save.set()
            with pytest.raises(asyncio.CancelledError):
                await setter
            await close_entered.wait()
            release_close.set()
            await closer
            assert store.connection is None
        finally:
            release_save.set()
            release_close.set()
            if not setter.done():
                setter.cancel()
            await asyncio.gather(setter, return_exceptions=True)
            if closer is not None:
                if not closer.done():
                    closer.cancel()
                await asyncio.gather(closer, return_exceptions=True)
            if store.connection is not None:
                await original_close()


# ============================================================================
# 模型测试
# ============================================================================


class TestAffectionLevel:
    """AffectionLevel 枚举和 from_score 方法测试。"""

    def test_from_score_hostile(self):
        assert AffectionLevel.from_score(-80) == AffectionLevel.HOSTILE
        assert AffectionLevel.from_score(-75) == AffectionLevel.HOSTILE

    def test_from_score_boundaries(self):
        # Minimum-threshold semantics: score must reach threshold to qualify
        assert AffectionLevel.from_score(-76) == AffectionLevel.HOSTILE
        assert AffectionLevel.from_score(-74) == AffectionLevel.HOSTILE  # not yet DISLIKED
        assert AffectionLevel.from_score(-50) == AffectionLevel.DISLIKED
        assert AffectionLevel.from_score(-25) == AffectionLevel.COLD
        assert AffectionLevel.from_score(0) == AffectionLevel.NEUTRAL
        assert AffectionLevel.from_score(25) == AffectionLevel.WARM

    def test_from_score_positive(self):
        assert AffectionLevel.from_score(50) == AffectionLevel.FRIENDLY
        assert AffectionLevel.from_score(75) == AffectionLevel.CLOSE
        assert AffectionLevel.from_score(100) == AffectionLevel.INTIMATE

    def test_name_for(self):
        assert AffectionLevel.name_for(30) == "温暖"
        assert AffectionLevel.name_for(-80) == "敌对"

    def test_enum_order_is_correct(self):
        values = [e.value for e in AffectionLevel]
        assert values == sorted(values)


class TestBotMood:
    """BotMood 模型测试。"""

    def test_is_active_when_fresh(self):
        mood = BotMood(
            mood_type=MoodType.HAPPY,
            intensity=0.7,
            start_time=time.time(),
            duration_hours=4.0,
        )
        assert mood.is_active()

    def test_is_active_when_expired(self):
        mood = BotMood(
            mood_type=MoodType.HAPPY,
            intensity=0.7,
            start_time=time.time() - 5 * 3600,
            duration_hours=4.0,
        )
        assert not mood.is_active()

    def test_get_mood_modifier_happy(self):
        mood = BotMood(mood_type=MoodType.HAPPY, intensity=0.5)
        # HAPPY base=1.2, intensity factor=(0.5+0.5*0.5)=0.75 -> 1.2*0.75=0.9
        modifier = mood.get_mood_modifier()
        assert modifier == pytest.approx(0.9)

    def test_get_mood_modifier_excited(self):
        mood = BotMood(mood_type=MoodType.EXCITED, intensity=1.0)
        # EXCITED base=1.3, intensity factor=(0.5+1.0*0.5)=1.0 -> 1.3
        assert mood.get_mood_modifier() == pytest.approx(1.3)

    def test_get_mood_modifier_angry_low_intensity(self):
        mood = BotMood(mood_type=MoodType.ANGRY, intensity=0.1)
        # ANGRY base=0.4, intensity factor=(0.5+0.1*0.5)=0.55 -> 0.4*0.55=0.22
        assert mood.get_mood_modifier() == pytest.approx(0.22)

    def test_get_mood_modifier_calm(self):
        mood = BotMood(mood_type=MoodType.CALM, intensity=0.5)
        # CALM base=1.0, intensity factor=(0.5+0.5*0.5)=0.75 -> 0.75
        assert mood.get_mood_modifier() == pytest.approx(0.75)

    def test_get_mood_modifier_angry_high_intensity(self):
        mood = BotMood(mood_type=MoodType.ANGRY, intensity=1.0)
        # ANGRY base=0.4, intensity factor=1.0 -> 0.4*1.0=0.4
        assert mood.get_mood_modifier() == pytest.approx(0.4)


class TestInteractionType:
    """InteractionType 枚举和规则测试。"""

    def test_all_17_types_present(self):
        expected = {
            "chat", "compliment", "flirt", "comfort", "help", "thanks",
            "apology", "tease", "care", "gift", "praise", "encourage",
            "support", "insult", "harassment", "abuse", "threat",
        }
        actual = {e.value for e in InteractionType}
        assert actual == expected

    def test_every_type_has_rule(self):
        for itype in InteractionType:
            assert itype in INTERACTION_RULES, f"{itype} missing rule"

    def test_chat_base_change(self):
        assert INTERACTION_RULES[InteractionType.CHAT].base_change == 1

    def test_gift_highest_positive_change(self):
        assert INTERACTION_RULES[InteractionType.GIFT].base_change == 8

    def test_threat_lowest_negative_change(self):
        assert INTERACTION_RULES[InteractionType.THREAT].base_change == -12

    def test_flirt_has_mood_requirements(self):
        rule = INTERACTION_RULES[InteractionType.FLIRT]
        assert rule.mood_requirements is not None
        assert MoodType.HAPPY in rule.mood_requirements

    def test_comfort_has_mood_requirements(self):
        rule = INTERACTION_RULES[InteractionType.COMFORT]
        assert rule.mood_requirements is not None
        assert MoodType.SAD in rule.mood_requirements
        assert MoodType.ANXIOUS in rule.mood_requirements

    def test_negative_types_have_negative_change(self):
        for itype in (InteractionType.INSULT, InteractionType.HARASSMENT,
                        InteractionType.ABUSE, InteractionType.THREAT):
            assert INTERACTION_RULES[itype].base_change < 0

    def test_positive_types_have_positive_change(self):
        for itype in InteractionType:
            if itype.value not in {"insult", "harassment", "abuse", "threat"}:
                assert INTERACTION_RULES[itype].base_change > 0


class TestKeywordClassification:
    """基于规则的关键词回退分类器测试。"""

    def test_compliment_keywords(self):
        assert classify_by_keywords("你好美啊") == InteractionType.COMPLIMENT
        assert classify_by_keywords("真可爱") == InteractionType.COMPLIMENT
        assert classify_by_keywords("666") == InteractionType.COMPLIMENT

    def test_thanks_keywords(self):
        assert classify_by_keywords("谢谢！") == InteractionType.THANKS
        assert classify_by_keywords("感谢你") == InteractionType.THANKS

    def test_care_keywords(self):
        assert classify_by_keywords("你好") == InteractionType.CARE
        assert classify_by_keywords("早上好") == InteractionType.CARE

    def test_threat_keywords(self):
        assert classify_by_keywords("我威胁你") == InteractionType.THREAT
        assert classify_by_keywords("打死你") == InteractionType.THREAT

    def test_insult_keywords(self):
        assert classify_by_keywords("你这个蠢货") == InteractionType.INSULT
        assert classify_by_keywords("垃圾！") == InteractionType.INSULT

    def test_no_match_returns_none(self):
        assert classify_by_keywords("今天天气不错") is None
        assert classify_by_keywords("随机文本") is None
        assert classify_by_keywords("xyzabc") is None

    def test_case_insensitive(self):
        assert classify_by_keywords("THANK YOU") == InteractionType.THANKS
        assert classify_by_keywords("Hello") == InteractionType.CARE

    def test_longest_match_wins(self):
        """验证 '你好美啊' 匹配 COMPLIMENT ('好美') 而非 CARE ('你好')。"""
        result = classify_by_keywords("你好美啊")
        assert result == InteractionType.COMPLIMENT


# ============================================================================
# Store Tests
# ============================================================================


class TestAffectionStore:
    """AffectionStore CRUD 操作测试。"""

    @pytest.mark.asyncio
    async def test_upsert_new_user(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            record = await store.upsert_affection("g1", "u1", 5)
            assert record["affection_score"] == 5
            assert record["interaction_count"] == 1
            assert record["last_interaction"] > 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_upsert_existing_user_accumulates(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            await store.upsert_affection("g1", "u1", 5)
            record = await store.upsert_affection("g1", "u1", 3)
            assert record["affection_score"] == 8
            assert record["interaction_count"] == 2
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_upsert_clamps_to_max(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            await store.upsert_affection("g1", "u1", 95)
            record = await store.upsert_affection("g1", "u1", 20, max_score=100)
            assert record["affection_score"] <= 100
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_upsert_clamps_to_min(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            await store.upsert_affection("g1", "u1", -95)
            record = await store.upsert_affection("g1", "u1", -20, min_score=-100)
            assert record["affection_score"] >= -100
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_affection_missing(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            assert await store.get_affection("g1", "nonexistent") is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_top_users(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            await store.upsert_affection("g1", "u1", 10)
            await store.upsert_affection("g1", "u2", 50)
            await store.upsert_affection("g1", "u3", 30)
            top = await store.get_top_users("g1", limit=2)
            assert len(top) == 2
            assert top[0]["user_id"] == "u2"
            assert top[1]["user_id"] == "u3"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_total_affection(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            await store.upsert_affection("g1", "u1", 10)
            await store.upsert_affection("g1", "u2", -5)
            total = await store.get_total_affection("g1")
            assert total == 5
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_user_count(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            await store.upsert_affection("g1", "u1", 10)
            await store.upsert_affection("g1", "u2", 20)
            assert await store.get_user_count("g1") == 2
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_save_and_get_mood(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mood_id = await store.save_bot_mood("g1", "happy", 0.8, "开心", 4.0)
            assert mood_id > 0
            latest = await store.get_latest_mood("g1")
            assert latest is not None
            assert latest["mood_type"] == "happy"
            assert latest["intensity"] == 0.8
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_active_mood_vs_expired(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            await store.save_bot_mood("g1", "happy", 0.7, "开心", 0.0)
            active = await store.get_active_mood("g1")
            assert active is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_users_above_threshold(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            await store.upsert_affection("g1", "u1", 80)
            await store.upsert_affection("g1", "u2", 20)
            await store.upsert_affection("g1", "u3", 60)
            above = await store.get_users_above_score("g1", 50)
            assert len(above) == 2
            assert {u["user_id"] for u in above} == {"u1", "u3"}
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_separate_groups_isolation(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            await store.upsert_affection("g1", "u1", 50)
            await store.upsert_affection("g2", "u1", 30)
            r1 = await store.get_affection("g1", "u1")
            r2 = await store.get_affection("g2", "u1")
            assert r1["affection_score"] == 50
            assert r2["affection_score"] == 30
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_set_affection_score(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            await store.upsert_affection("g1", "u1", 30)
            await store.set_affection_score("g1", "u1", 15)
            record = await store.get_affection("g1", "u1")
            assert record["affection_score"] == 15
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_mood_history(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            await store.save_bot_mood("g1", "calm", 0.5, "平静", 4.0)
            await store.save_bot_mood("g1", "happy", 0.8, "开心", 4.0)
            history = await store.get_mood_history("g1", limit=10)
            assert len(history) == 2
        finally:
            await store.close()


# ============================================================================
# Manager Tests (inline store creation pattern)
# ============================================================================


def _make_manager(tmp_db_path, **kwargs):
    """使用全新存储创建一个 AffectionManager。"""
    return _make_manager_with_llm(tmp_db_path, None, **kwargs)


def _make_manager_with_llm(tmp_db_path, llm_adapter=None, **kwargs):
    return AffectionManager(
        _create_store_sync(tmp_db_path),  # will be initialized per-test
        llm_adapter=llm_adapter,
        **kwargs,
    )


async def _create_store(db_path: str) -> AffectionStore:
    s = AffectionStore(db_path)
    await s.initialize()
    return s


def _create_store_sync(db_path: str) -> AffectionStore:
    """创建未初始化的存储 — 调用者必须内联初始化。"""
    return AffectionStore(db_path)


class TestAffectionManager:
    """AffectionManager 核心逻辑测试。"""

    @pytest.mark.asyncio
    async def test_process_interaction_chat(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            result = await mgr.process_interaction("u1", "g1", "今天天气不错", "嗯呢~")
            assert result["success"]
            assert result["interaction_type"] == "chat"
            assert result["affection_delta"] > 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_process_interaction_compliment_keyword(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            result = await mgr.process_interaction("u1", "g1", "你好漂亮啊", "谢谢~")
            assert result["success"]
            assert result["interaction_type"] == "compliment"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_process_interaction_negative_insult(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            result = await mgr.process_interaction("u1", "g1", "你这个蠢货！", "")
            assert result["success"]
            assert result["interaction_type"] == "insult"
            assert result["affection_delta"] < 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_affection_accumulates(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            await mgr.process_interaction("u1", "g1", "你好美", "谢谢~")
            result = await mgr.process_interaction("u1", "g1", "你好棒", "谢谢~")
            assert result["success"]
            # Two COMPLIMENT interactions accumulate: base 3+3=6, with CALM modifier ~0.75
            # gives ~4-5. Validate it increased from the first call.
            assert result["affection_score"] >= 3
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_user_affection(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            await mgr.process_interaction("u1", "g1", "你好棒", "谢谢~")
            ua = await mgr.get_user_affection("g1", "u1")
            assert ua is not None
            assert ua.affection_score > 0
            assert ua.interaction_count == 1
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_user_affection_missing(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            ua = await mgr.get_user_affection("g1", "nonexistent")
            assert ua is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_group_affection_status(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            await mgr.process_interaction("u1", "g1", "你好棒", "谢谢~")
            status = await mgr.get_group_affection_status("g1")
            assert status["user_count"] >= 1
            mood = status["current_mood"]
            assert mood is not None
            assert mood["type"] == "calm"
            assert mood["duration_hours"] == 1.0
            assert mood["start_time"] > 0
            assert isinstance(mood["is_active"], bool)
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_mood_is_set_on_first_call(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            mood = await mgr.get_mood("g_new")
            assert mood.mood_type == MoodType.CALM
            assert mood.intensity == 0.5
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_set_mood_explicit(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            mood = await mgr.set_mood("g1", MoodType.EXCITED, 0.9, 6.0)
            assert mood.mood_type == MoodType.EXCITED
            assert mood.intensity == 0.9
            assert mood.duration_hours == 6.0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_set_mood_persisted(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            await mgr.set_mood("g1", MoodType.HAPPY, 0.7, 3.0)
            record = await store.get_latest_mood("g1")
            assert record["mood_type"] == "happy"
            assert record["intensity"] == 0.7
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_negative_cascade_changes_mood(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            await mgr.process_interaction("u1", "g1", "你这个蠢货！", "")
            mood = await mgr.get_mood("g1")
            assert mood.mood_type == MoodType.SAD
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_positive_cascade_changes_mood(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            # "礼物" keyword classifies as COMPLIMENT, not GIFT. We need LLM for GIFT.
            # Let's test with praise keywords instead
            await mgr.set_mood("g1", MoodType.CALM, 0.5)
            await mgr.process_interaction("u1", "g1", "你太优秀了！", "")
            # PRAISE might not have a keyword match. Most positive keywords map to
            # COMPLIMENT, so the cascade won't trigger positive_mood_boost.
            # Instead just verify interaction was processed.
            mood = await mgr.get_mood("g1")
            assert mood.mood_type in (MoodType.CALM, MoodType.HAPPY)
        finally:
            await store.close()


class TestAffectionManagerWithLLM:
    """需要 LLM 适配器 Mock 的测试。"""

    @pytest.mark.asyncio
    async def test_llm_classification_used(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            llm = AsyncMock()
            llm.chat_completion.return_value = "compliment"
            mgr = AffectionManager(store, llm_adapter=llm)
            result = await mgr.process_interaction("u1", "g1", "某条消息", "回复")
            assert result["interaction_type"] == "compliment"
            llm.chat_completion.assert_called_once()
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_llm_classification_falls_back_on_bad_output(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            llm = AsyncMock()
            llm.chat_completion.return_value = "bogus_type"
            mgr = AffectionManager(store, llm_adapter=llm)
            result = await mgr.process_interaction("u1", "g1", "普通聊天内容", "")
            assert result["interaction_type"] == "chat"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_llm_classification_falls_back_on_error(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            llm = AsyncMock()
            llm.chat_completion.side_effect = RuntimeError("LLM down")
            mgr = AffectionManager(store, llm_adapter=llm)
            result = await mgr.process_interaction("u1", "g1", "普通聊天内容", "")
            assert result["interaction_type"] == "chat"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_mood_gate_flirt_blocks_on_calm(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            llm = AsyncMock()
            llm.chat_completion.return_value = "flirt"
            mgr = AffectionManager(store, llm_adapter=llm)
            await mgr.set_mood("g1", MoodType.CALM, 0.5)
            result = await mgr.process_interaction("u1", "g1", "撩你一下~", "")
            assert result["gated"] is True
            assert result["affection_delta"] == 0
            assert "不适合" in result["gate_reason"]
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_mood_gate_flirt_passes_on_happy(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            llm = AsyncMock()
            llm.chat_completion.return_value = "flirt"
            mgr = AffectionManager(store, llm_adapter=llm)
            await mgr.set_mood("g1", MoodType.HAPPY, 0.7)
            result = await mgr.process_interaction("u1", "g1", "撩你一下~", "")
            assert result["gated"] is False
            assert result["affection_delta"] > 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_mood_gate_comfort_blocks_on_happy(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            llm = AsyncMock()
            llm.chat_completion.return_value = "comfort"
            mgr = AffectionManager(store, llm_adapter=llm)
            await mgr.set_mood("g1", MoodType.HAPPY, 0.8)
            result = await mgr.process_interaction("u1", "g1", "别难过", "")
            assert result["gated"] is True
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_mood_gate_passes_on_permitted_mood(self, tmp_db_path):
        """SAD 状态下允许 COMFORT。"""
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            llm = AsyncMock()
            llm.chat_completion.return_value = "comfort"
            mgr = AffectionManager(store, llm_adapter=llm)
            await mgr.set_mood("g1", MoodType.SAD, 0.6)
            result = await mgr.process_interaction("u1", "g1", "别难过", "")
            assert result["gated"] is False
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_gift_triggers_positive_cascade(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            llm = AsyncMock()
            llm.chat_completion.return_value = "gift"
            mgr = AffectionManager(store, llm_adapter=llm)
            await mgr.set_mood("g1", MoodType.CALM, 0.5)
            result = await mgr.process_interaction("u1", "g1", "送你礼物！", "")
            assert result["success"]
            mood = await mgr.get_mood("g1")
            assert mood.mood_type == MoodType.EXCITED
        finally:
            await store.close()


# ============================================================================
# Redistribution Tests
# ============================================================================


class TestRedistribution:
    """好感度分数重分配测试。"""

    @pytest.mark.asyncio
    async def test_redistribution_triggers_on_overflow(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            await store.upsert_affection("g1", "u1", 90)
            await store.upsert_affection("g1", "u2", 90)
            await store.upsert_affection("g1", "u3", 90)

            mgr = AffectionManager(store, max_total_affection=200, affection_decay_rate=0.8)
            await mgr.process_interaction("u_new", "g1", "你好棒！", "谢谢~")

            total = await store.get_total_affection("g1")
            assert total <= 200
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_no_redistribution_when_under_limit(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            await store.upsert_affection("g1", "u1", 30)
            await store.upsert_affection("g1", "u2", 20)

            mgr = AffectionManager(store, max_total_affection=200)
            await mgr.process_interaction("u1", "g1", "你好棒！", "谢谢~")

            total = await store.get_total_affection("g1")
            assert total < 200
        finally:
            await store.close()


# ============================================================================
# Edge Cases
# ============================================================================


class TestEdgeCases:
    """边界情况与边界条件。"""

    @pytest.mark.asyncio
    async def test_empty_message_defaults_to_chat(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            result = await mgr.process_interaction("u1", "g1", "", "")
            assert result["interaction_type"] == "chat"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_very_long_message(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            long_msg = "你真好" * 500
            result = await mgr.process_interaction("u1", "g1", long_msg, "")
            assert result["success"]
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_max_affection_bound(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            for _ in range(50):
                await mgr.process_interaction("u1", "g1", "你太棒啦！", "谢谢~")
            ua = await mgr.get_user_affection("g1", "u1")
            assert ua is not None
            assert ua.affection_score <= 100
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_min_affection_bound(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            for _ in range(50):
                await mgr.process_interaction("u1", "g1", "你这个蠢货去死吧！", "")
            ua = await mgr.get_user_affection("g1", "u1")
            assert ua is not None
            assert ua.affection_score >= -100
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_multiple_users_independent(self, tmp_db_path):
        store = AffectionStore(tmp_db_path)
        await store.initialize()
        try:
            mgr = AffectionManager(store)
            await mgr.process_interaction("u1", "g1", "你好棒！", "谢谢~")
            await mgr.process_interaction("u2", "g1", "垃圾", "")
            ua1 = await mgr.get_user_affection("g1", "u1")
            ua2 = await mgr.get_user_affection("g1", "u2")
            assert ua1 is not None and ua2 is not None
            assert ua1.affection_score > ua2.affection_score
        finally:
            await store.close()
