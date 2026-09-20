"""显式 apply 计划的执行：revision CAS、checkpoint 恢复、取消与派生降级。

共享 fixture（临时 SQLite、canonical 行、真实 CAS 引擎边界）位于
``tests/representation_migration_support.py``。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest
from sqlalchemy import text

from core.features.memory.application.canonical_representation_migration import (
    ACTION_OWNER_REINFORCE,
    REASON_ALREADY_APPLIED,
    REASON_CHECKPOINT_UNAVAILABLE,
    REASON_CONFIRMATION_MISMATCH,
    REASON_CONFIRMATION_REQUIRED,
    REASON_DERIVED_REBUILD_FAILED,
    REASON_DERIVED_REBUILD_UNAVAILABLE,
    REASON_ENGINE_UNAVAILABLE,
    REASON_EVIDENCE_MAPPING_MISMATCH,
    REASON_REVISION_CONFLICT,
    TARGET_REPRESENTATION_VERSION,
    CanonicalRepresentationMigrationService,
    MigrationPlanItem,
    PlanValidationError,
    RepresentationMigrationError,
    assert_report_is_privacy_safe,
    build_migration_plan,
    build_migration_service,
)
from tests.representation_migration_support import (
    _create_schema,
    _current_row,
    _DocumentStorage,
    _engine_with_real_cas,
    _legacy_content,
    _legacy_row,
    _read_checkpoint,
    _read_row,
    _seed,
)

# ---------------------------------------------------------------------------
# apply：CAS、checkpoint、取消与派生降级
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# apply：CAS、checkpoint、取消与派生降级
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_uses_revision_cas_and_resumes_from_checkpoint(tmp_path) -> None:
    """apply 原地改写表示、保持整数 ID，并能从 checkpoint 幂等恢复。"""

    db_path = str(tmp_path / "canonical.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            _legacy_row(1, updated_at="rev-1"),
            _current_row(2, updated_at="rev-2"),
        ],
    )
    storage = _DocumentStorage(db_path)
    engine = _engine_with_real_cas(storage)
    derived_rebuild = AsyncMock(return_value={"success": True})
    connection = await aiosqlite.connect(db_path)
    try:
        service = build_migration_service(
            engine, db_connection=connection, derived_rebuild=derived_rebuild
        )
        outcome = await service.dry_run()
        plan = build_migration_plan(outcome.plan_items, operator_confirmation="token-1")
        assert [item.memory_id for item in plan.items] == [1]

        report = await service.apply(plan, confirmation="token-1")

        assert report["status"] == "completed"
        assert report["applied_count"] == 1
        assert report["conflict_count"] == 0
        assert report["resumed"] is False
        assert report["derived"] == {
            "status": "available",
            "stage": None,
            "reason_code": None,
        }
        assert report["checkpoint"] == {"status": "recorded", "reason_code": None}
        derived_rebuild.assert_awaited_once()

        row = await _read_row(db_path, 1)
        assert row is not None
        assert row["id"] == 1
        assert row["text"] == "喜欢咖啡；在上海工作"
        assert row["updated_at"] != "rev-1"
        metadata = json.loads(row["metadata"])
        assert metadata["canonical_summary"] == "喜欢咖啡；在上海工作"
        assert metadata["persona_summary"] == _legacy_content()
        assert metadata["summary_schema_version"] == TARGET_REPRESENTATION_VERSION
        untouched = await _read_row(db_path, 2)
        assert untouched is not None and untouched["updated_at"] == "rev-2"

        stored = await _read_checkpoint(db_path)
        assert stored is not None
        assert stored["last_processed_id"] == 1
        assert stored["applied_count"] == 1
        assert stored["status"] == "completed"

        resumed = await service.apply(plan, confirmation="token-1")
        assert resumed["resumed"] is True
        assert resumed["applied_count"] == 0
        assert resumed["skipped_count"] == 0
        assert resumed["derived"]["status"] == "not_applicable"

        replay = await service.apply(plan, confirmation="token-1", resume=False)
        assert replay["applied_count"] == 0
        assert replay["skipped_count"] == 1
        assert replay["reason_counts"] == {REASON_ALREADY_APPLIED: 1}
        assert replay["status"] == "completed"
        # --no-resume 只忽略已有 checkpoint，仍然记录本次进度。
        assert replay["checkpoint"] == {"status": "recorded", "reason_code": None}
    finally:
        await connection.close()
        await storage.close()


@pytest.mark.asyncio
async def test_apply_reads_back_when_derived_refresh_fails_after_commit(
    tmp_path,
) -> None:
    """canonical 已提交但派生刷新失败时不伪造 applied，只按回读结果记账。"""

    db_path = str(tmp_path / "canonical.db")
    await _create_schema(db_path)
    await _seed(db_path, [_legacy_row(1, updated_at="rev-1")])
    storage = _DocumentStorage(db_path)
    engine = _engine_with_real_cas(storage, bm25_retriever=SimpleNamespace())
    connection = await aiosqlite.connect(db_path)
    try:
        service = build_migration_service(engine, db_connection=connection)
        plan = build_migration_plan(
            [MigrationPlanItem(1, "representation_rewrite", "rev-1")],
            operator_confirmation="token-1",
        )

        report = await service.apply(plan, confirmation="token-1")

        assert report["applied_count"] == 0
        assert report["skipped_count"] == 1
        assert report["reason_counts"] == {REASON_ALREADY_APPLIED: 1}
        assert report["conflict_count"] == 0
        row = await _read_row(db_path, 1)
        assert row is not None
        assert row["text"] == "喜欢咖啡；在上海工作"
    finally:
        await connection.close()
        await storage.close()


@pytest.mark.asyncio
async def test_apply_counts_cas_conflict_and_never_overwrites(tmp_path) -> None:
    """计划 revision 过期时只计数冲突，不覆盖并发写入的正文。"""

    db_path = str(tmp_path / "canonical.db")
    await _create_schema(db_path)
    await _seed(db_path, [_legacy_row(1, updated_at="rev-1")])
    connection = await aiosqlite.connect(db_path)
    update_memory = AsyncMock(return_value=True)
    try:
        service = CanonicalRepresentationMigrationService(
            db_connection=connection, update_memory=update_memory
        )
        outcome = await service.dry_run()
        plan = build_migration_plan(outcome.plan_items, operator_confirmation="token-1")
        storage = _DocumentStorage(db_path)
        async with storage.engine.begin() as session:
            await session.execute(
                text(
                    """UPDATE documents SET text = :text, metadata = :metadata,
                       updated_at = :revision WHERE id = 1"""
                ),
                {
                    "text": "并发写入的正文",
                    "metadata": json.dumps({"key_facts": ["并发改写事实"]}),
                    "revision": "rev-1-concurrent",
                },
            )
        await storage.close()

        report = await service.apply(plan, confirmation="token-1")

        assert report["applied_count"] == 0
        assert report["conflict_count"] == 1
        assert report["status"] == "degraded"
        assert report["reason_counts"] == {REASON_REVISION_CONFLICT: 1}
        update_memory.assert_not_awaited()
        row = await _read_row(db_path, 1)
        assert row is not None
        assert row["text"] == "并发写入的正文"
        assert row["updated_at"] == "rev-1-concurrent"
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_apply_cancellation_propagates_and_keeps_batch_checkpoint(
    tmp_path,
) -> None:
    """取消继续传播，且已完成的批次边界仍然可恢复。"""

    db_path = str(tmp_path / "canonical.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [_legacy_row(1, updated_at="rev-1"), _legacy_row(2, updated_at="rev-2")],
    )
    connection = await aiosqlite.connect(db_path)
    update_memory = AsyncMock(side_effect=[True, asyncio.CancelledError()])
    try:
        service = CanonicalRepresentationMigrationService(
            db_connection=connection, update_memory=update_memory, batch_size=1
        )
        plan = build_migration_plan(
            [
                MigrationPlanItem(1, "representation_rewrite", "rev-1"),
                MigrationPlanItem(2, "representation_rewrite", "rev-2"),
            ],
            operator_confirmation="token-1",
        )
        with pytest.raises(asyncio.CancelledError):
            await service.apply(plan, confirmation="token-1")

        stored = await _read_checkpoint(db_path)
        assert stored is not None
        assert stored["last_processed_id"] == 1
        assert stored["applied_count"] == 1
        assert stored["status"] == "in_progress"
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_apply_reports_derived_degradation_without_fabricating_success(
    tmp_path,
) -> None:
    """派生缺失或失败只报告 degraded/needs_repair，不伪造成功也不回滚 canonical。"""

    db_path = str(tmp_path / "canonical.db")
    await _create_schema(db_path)
    await _seed(db_path, [_legacy_row(1, updated_at="rev-1")])
    connection = await aiosqlite.connect(db_path)
    try:
        missing_service = CanonicalRepresentationMigrationService(
            db_connection=connection, update_memory=AsyncMock(return_value=True)
        )
        plan = build_migration_plan(
            [MigrationPlanItem(1, "representation_rewrite", "rev-1")],
            operator_confirmation="token-1",
        )
        missing = await missing_service.apply(plan, confirmation="token-1")
        assert missing["applied_count"] == 1
        assert missing["status"] == "degraded"
        assert missing["derived"] == {
            "status": "unavailable",
            "stage": None,
            "reason_code": REASON_DERIVED_REBUILD_UNAVAILABLE,
        }

        failing_service = CanonicalRepresentationMigrationService(
            db_connection=connection,
            update_memory=AsyncMock(return_value=True),
            derived_rebuild=AsyncMock(
                return_value={
                    "success": False,
                    "reason_code": "graph_rebuild_failed",
                    "stages": {
                        "indexes": {"status": "completed", "success": True},
                        "graph": {"status": "failed", "success": False},
                    },
                }
            ),
        )
        failed = await failing_service.apply(plan, confirmation="token-1", resume=False)
        assert failed["status"] == "degraded"
        assert failed["derived"] == {
            "status": "degraded",
            "stage": "graph",
            "reason_code": REASON_DERIVED_REBUILD_FAILED,
        }
        assert_report_is_privacy_safe(failed)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_apply_reports_checkpoint_unavailable_without_migration_status(
    tmp_path,
) -> None:
    """缺少 migration_status 表时如实报告 checkpoint 不可用。"""

    db_path = str(tmp_path / "canonical.db")
    await _create_schema(db_path, migration_status=False)
    await _seed(db_path, [_legacy_row(1, updated_at="rev-1")])
    connection = await aiosqlite.connect(db_path)
    try:
        service = CanonicalRepresentationMigrationService(
            db_connection=connection, update_memory=AsyncMock(return_value=True)
        )
        plan = build_migration_plan(
            [MigrationPlanItem(1, "representation_rewrite", "rev-1")],
            operator_confirmation="token-1",
        )

        report = await service.apply(plan, confirmation="token-1")

        assert report["applied_count"] == 1
        assert report["status"] == "degraded"
        assert report["checkpoint"] == {
            "status": "unavailable",
            "reason_code": REASON_CHECKPOINT_UNAVAILABLE,
        }
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_apply_requires_confirmation_and_engine_port(tmp_path) -> None:
    """apply 缺少确认令牌或写回端口时 fail-closed。"""

    db_path = str(tmp_path / "canonical.db")
    await _create_schema(db_path)
    await _seed(db_path, [_legacy_row(1, updated_at="rev-1")])
    connection = await aiosqlite.connect(db_path)
    try:
        service = CanonicalRepresentationMigrationService(db_connection=connection)
        plan = build_migration_plan(
            [MigrationPlanItem(1, "representation_rewrite", "rev-1")],
            operator_confirmation="token-1",
        )

        with pytest.raises(PlanValidationError) as missing:
            await service.apply(plan, confirmation="")
        assert missing.value.reason == REASON_CONFIRMATION_REQUIRED

        with pytest.raises(PlanValidationError) as mismatch:
            await service.apply(plan, confirmation="token-2")
        assert mismatch.value.reason == REASON_CONFIRMATION_MISMATCH

        with pytest.raises(RepresentationMigrationError) as unavailable:
            await service.apply(plan, confirmation="token-1")
        assert unavailable.value.reason == REASON_ENGINE_UNAVAILABLE
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_owner_reinforce_updates_only_merge_accounting(tmp_path) -> None:
    """dedup 强化只按既有 narrow owner 语义更新记账，绝不删除 duplicate 行。"""

    db_path = str(tmp_path / "canonical.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            _current_row(10, updated_at="rev-10"),
            _current_row(11, updated_at="rev-11"),
        ],
    )
    storage = _DocumentStorage(db_path)
    async with storage.engine.begin() as session:
        await session.execute(
            text("UPDATE documents SET metadata = :metadata WHERE id = 10"),
            {
                "metadata": json.dumps(
                    {
                        **_current_row(10, updated_at="rev-10")[2],
                        "merge_count": 2,
                        "merged_idempotency_keys": ["k1"],
                    },
                    ensure_ascii=False,
                )
            },
        )
    await storage.close()

    update_memory = AsyncMock(return_value=True)
    connection = await aiosqlite.connect(db_path)
    try:
        service = CanonicalRepresentationMigrationService(
            db_connection=connection,
            update_memory=update_memory,
            clock=lambda: 1234.0,
        )
        plan = build_migration_plan(
            [
                MigrationPlanItem(
                    memory_id=11,
                    action=ACTION_OWNER_REINFORCE,
                    expected_revision="rev-11",
                    owner_memory_id=10,
                    owner_expected_revision="rev-10",
                )
            ],
            operator_confirmation="token-1",
        )

        report = await service.apply(plan, confirmation="token-1")

        assert report["applied_count"] == 1
        assert report["conflict_count"] == 0
        call = update_memory.await_args
        assert call is not None
        owner_id, updates, expected_revision = call.args
        assert owner_id == 10
        assert expected_revision == "rev-10"
        assert updates == {
            "metadata": {
                "merge_count": 3,
                "last_merged_at": 1234.0,
                "merged_idempotency_keys": ["k1", "representation-migration:11"],
            }
        }
        duplicate = await _read_row(db_path, 11)
        assert duplicate is not None and duplicate["updated_at"] == "rev-11"

        stale = build_migration_plan(
            [
                MigrationPlanItem(
                    memory_id=11,
                    action=ACTION_OWNER_REINFORCE,
                    expected_revision="rev-11",
                    owner_memory_id=10,
                    owner_expected_revision="rev-stale",
                )
            ],
            operator_confirmation="token-1",
        )
        conflicted = await service.apply(stale, confirmation="token-1", resume=False)
        assert conflicted["conflict_count"] == 1
        assert conflicted["reason_counts"] == {REASON_REVISION_CONFLICT: 1}
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_apply_skips_unavailable_rows_without_writing(tmp_path) -> None:
    """计划中的不可迁移行只按原因码计数，不发起任何写回。"""

    db_path = str(tmp_path / "canonical.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            (
                1,
                "坏行",
                {"key_facts": ["a", "b"], "fact_source_evidence": [{"role": "user"}]},
                "c1",
                "rev-1",
            )
        ],
    )
    connection = await aiosqlite.connect(db_path)
    update_memory = AsyncMock(return_value=True)
    try:
        service = CanonicalRepresentationMigrationService(
            db_connection=connection, update_memory=update_memory
        )
        plan = build_migration_plan(
            [MigrationPlanItem(1, "representation_rewrite", "rev-1")],
            operator_confirmation="token-1",
        )

        report = await service.apply(plan, confirmation="token-1")

        assert report["applied_count"] == 0
        assert report["unavailable_count"] == 1
        assert report["status"] == "degraded"
        assert report["reason_counts"] == {REASON_EVIDENCE_MAPPING_MISMATCH: 1}
        update_memory.assert_not_awaited()
    finally:
        await connection.close()
