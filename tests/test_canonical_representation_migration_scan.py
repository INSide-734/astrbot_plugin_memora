"""canonical 表示迁移的只读 dry-run：keyset 扫描、revision 复核与脱敏报告。

共享 fixture（临时 SQLite、canonical 行、引擎边界）位于
``tests/representation_migration_support.py``。
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from core.features.memory.application.canonical_representation_migration import (
    REASON_SCAN_UNAVAILABLE,
    CanonicalRepresentationMigrationService,
    RepresentationMigrationError,
    assert_report_is_privacy_safe,
)
from tests.representation_migration_support import (
    _create_schema,
    _current_row,
    _InterleavingConnection,
    _legacy_metadata,
    _legacy_row,
    _seed,
)

# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_is_read_only_and_reports_only_aggregates(tmp_path) -> None:
    """dry-run 不写库，报告只有白名单计数，计划只含可迁移行。"""

    db_path = str(tmp_path / "canonical.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            _legacy_row(1, updated_at="rev-1"),
            _current_row(2, updated_at="rev-2"),
            (
                3,
                "坏行",
                {"key_facts": ["a", "b"], "fact_source_evidence": [{"role": "user"}]},
                "c3",
                "rev-3",
            ),
            (
                4,
                "归档行",
                {**_legacy_metadata(), "memory_status": "archived"},
                "c4",
                "rev-4",
            ),
        ],
    )
    before = Path(db_path).read_bytes()
    connection = await aiosqlite.connect(db_path)
    try:
        service = CanonicalRepresentationMigrationService(db_connection=connection)
        outcome = await service.dry_run()
    finally:
        await connection.close()

    report = outcome.report
    assert report["mode"] == "dry_run"
    assert report["status"] == "completed"
    assert report["scanned_count"] == 4
    assert report["eligible_count"] == 2
    assert report["changed_count"] == 1
    assert report["unchanged_count"] == 1
    assert report["unavailable_count"] == 2
    assert report["conflict_count"] == 0
    assert report["exhausted"] is True
    assert report["action_counts"] == {
        "content": 1,
        "metadata": 0,
        "none": 1,
        "unavailable": 2,
    }
    assert report["reason_counts"] == {
        "evidence_mapping_mismatch": 1,
        "not_recallable": 1,
    }
    assert [item.memory_id for item in outcome.plan_items] == [1]
    assert outcome.plan_items[0].expected_revision == "rev-1"
    assert outcome.plan_items[0].action == "representation_rewrite"

    assert_report_is_privacy_safe(report)
    serialized = json.dumps(report, ensure_ascii=False)
    assert "用户喜欢咖啡" not in serialized
    assert "memory_id" not in serialized
    assert "rev-1" not in serialized
    assert Path(db_path).read_bytes() == before


@pytest.mark.asyncio
async def test_dry_run_respects_limit_and_reports_not_exhausted(tmp_path) -> None:
    """限制行数时报告明确未扫描到底。"""

    db_path = str(tmp_path / "canonical.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            _legacy_row(1, updated_at="rev-1"),
            _legacy_row(2, updated_at="rev-2"),
        ],
    )
    connection = await aiosqlite.connect(db_path)
    try:
        service = CanonicalRepresentationMigrationService(
            db_connection=connection, batch_size=1
        )
        outcome = await service.dry_run(limit=1)
    finally:
        await connection.close()

    assert outcome.report["scanned_count"] == 1
    assert outcome.report["exhausted"] is False
    assert [item.memory_id for item in outcome.plan_items] == [1]


@pytest.mark.asyncio
async def test_dry_run_limit_caps_scan_below_batch_size(tmp_path) -> None:
    """limit 小于 batch_size 时也不会多读：最多扫描 limit 行且不越界成计划。"""

    db_path = str(tmp_path / "canonical.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            _legacy_row(1, updated_at="rev-1"),
            _legacy_row(2, updated_at="rev-2"),
            _legacy_row(3, updated_at="rev-3"),
        ],
    )
    connection = await aiosqlite.connect(db_path)
    try:
        service = CanonicalRepresentationMigrationService(
            db_connection=connection, batch_size=50
        )
        outcome = await service.dry_run(limit=2)
    finally:
        await connection.close()

    assert outcome.report["scanned_count"] == 2
    assert outcome.report["exhausted"] is False
    assert [item.memory_id for item in outcome.plan_items] == [1, 2]


@pytest.mark.asyncio
async def test_dry_run_counts_revision_change_between_read_and_recheck(
    tmp_path,
) -> None:
    """读阶段 revision 复核发现变化时记为 conflict，不进入计划。"""

    db_path = str(tmp_path / "canonical.db")
    await _create_schema(db_path)
    await _seed(
        db_path,
        [
            _legacy_row(1, updated_at="rev-1"),
            _legacy_row(2, updated_at="rev-2"),
        ],
    )
    connection = await aiosqlite.connect(db_path)
    wrapped = _InterleavingConnection(
        connection, db_path=db_path, target_id=1, revision="rev-1-concurrent"
    )
    try:
        service = CanonicalRepresentationMigrationService(db_connection=wrapped)
        outcome = await service.dry_run()
    finally:
        await connection.close()

    assert outcome.report["scanned_count"] == 2
    assert outcome.report["changed_count"] == 1
    assert outcome.report["conflict_count"] == 1
    assert outcome.report["reason_counts"] == {"revision_conflict": 1}
    assert [item.memory_id for item in outcome.plan_items] == [2]


@pytest.mark.asyncio
async def test_dry_run_fails_closed_without_documents_table(tmp_path) -> None:
    """缺少 canonical 表时返回稳定原因码，不猜测空库。"""

    db_path = str(tmp_path / "empty.db")
    connection = await aiosqlite.connect(db_path)
    await connection.commit()
    try:
        service = CanonicalRepresentationMigrationService(db_connection=connection)
        with pytest.raises(RepresentationMigrationError) as error:
            await service.dry_run()
    finally:
        await connection.close()

    assert error.value.reason == REASON_SCAN_UNAVAILABLE
