"""再巩固候选 Store 的并发幂等与事务原子性回归测试。"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from core.storage.reconsolidation_store import ReconsolidationStore


def _candidate_payload() -> dict[str, Any]:
    """构造可重复提交的最小候选 payload。"""

    return {
        "memory_id": 7,
        "source_revision": "r-7",
        "old_content": "原始记忆正文",
        "old_metadata": {"access_count": 8},
        "proposed_content": "修正后的记忆正文内容",
        "change_summary": "LLM 修订候选",
        "evidence_type": "llm_revision",
    }


@pytest.mark.asyncio
async def test_stage_candidate_reuses_existing_pending_row(tmp_path: Path) -> None:
    """相同来源与提案重复写入时应复用同一条 pending 候选。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()

    first = await store.stage_candidate(**_candidate_payload())
    second = await store.stage_candidate(**_candidate_payload())

    assert second["candidate_id"] == first["candidate_id"]
    assert len(await store.list_candidates()) == 1


@pytest.mark.asyncio
async def test_stage_candidate_serializes_concurrent_duplicates(tmp_path: Path) -> None:
    """并发重复提案只能保留一条 pending 候选。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()

    results = await asyncio.gather(
        *(store.stage_candidate(**_candidate_payload()) for _ in range(8)),
        return_exceptions=True,
    )

    assert not [result for result in results if isinstance(result, Exception)]
    assert len(await store.list_candidates()) == 1


@pytest.mark.asyncio
async def test_transition_rolls_back_status_when_action_audit_fails(
    tmp_path: Path,
) -> None:
    """动作审计写入失败时，候选状态迁移必须一并回滚。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    candidate = await store.stage_candidate(**_candidate_payload())
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute(
            """
            CREATE TRIGGER reject_reconsolidation_action
            BEFORE INSERT ON reconsolidation_actions
            WHEN NEW.action='reject'
            BEGIN
                SELECT RAISE(ABORT, 'action audit blocked');
            END
            """
        )
        await db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        await store.transition(
            candidate["candidate_id"],
            expected_status="pending",
            new_status="rejected",
            reason_code="manual_reject",
            action="reject",
        )

    persisted = await store.get_candidate(candidate["candidate_id"])
    assert persisted is not None
    assert persisted["status"] == "pending"
    assert [
        item["action"] for item in await store.list_actions(candidate["candidate_id"])
    ] == ["stage"]


@pytest.mark.asyncio
async def test_complete_rollback_is_atomic_when_action_audit_fails(
    tmp_path: Path,
) -> None:
    """回滚动作审计失败时，候选状态和恢复操作必须一起保留。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    candidate = await store.stage_candidate(**_candidate_payload())
    await store.begin_apply(
        candidate["candidate_id"],
        expected_revision="r-7",
        target_metadata={"access_count": 8},
    )
    await store.complete_apply(
        candidate["candidate_id"],
        applied=True,
        reason_code="applied",
        applied_revision="r-8",
        applied_metadata={"access_count": 8},
    )
    await store.begin_rollback(
        candidate["candidate_id"],
        expected_revision="r-8",
    )
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute(
            """
            CREATE TRIGGER reject_rollback_action
            BEFORE INSERT ON reconsolidation_actions
            WHEN NEW.action='rollback'
            BEGIN
                SELECT RAISE(ABORT, 'rollback audit blocked');
            END
            """
        )
        await db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        await store.complete_rollback(candidate["candidate_id"])

    persisted = await store.get_candidate(candidate["candidate_id"])
    assert persisted is not None
    assert persisted["status"] == "approved"
    operations = await store.list_incomplete_rollbacks()
    assert [item["candidate_id"] for item in operations] == [candidate["candidate_id"]]
    assert [
        item["action"] for item in await store.list_actions(candidate["candidate_id"])
    ] == ["stage", "apply"]
