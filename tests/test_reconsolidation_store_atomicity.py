"""再巩固候选 Store 的并发幂等与事务原子性回归测试。"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from core.features.reconsolidation.application.reconsolidation import (
    ReconsolidationManager,
)
from core.features.reconsolidation.infrastructure.reconsolidation_store import (
    ReconsolidationStore,
)


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


async def _insert_pending_candidate(
    db: aiosqlite.Connection,
    candidate_id: str,
    created_at: str,
    *,
    old_metadata: str = '{"access_count": 8}',
) -> None:
    """写入一条与其它候选完全重复的 pending 行（模拟旧库迁移前状态）。"""

    await db.execute(
        """
        INSERT INTO reconsolidation_candidates (
            candidate_id, memory_id, source_revision, old_content, old_metadata,
            proposed_content, change_summary, evidence_type, status, reason_code,
            created_at, updated_at
        ) VALUES (?, 7, 'r-7', '原始记忆正文', ?,
                  '修正后的记忆正文内容', 'LLM 修订候选', 'llm_revision', 'pending',
                  'proposed', ?, ?)
        """,
        (candidate_id, old_metadata, created_at, created_at),
    )


async def _insert_apply_intent(
    db: aiosqlite.Connection,
    candidate_id: str,
    target_metadata: str,
    created_at: str,
) -> None:
    """写入一条未收口的 apply intent。"""

    await db.execute(
        """
        INSERT INTO reconsolidation_apply_ops (
            candidate_id, expected_revision, target_metadata, status,
            reason_code, created_at, updated_at
        ) VALUES (?, 'r-7', ?, 'pending', 'apply_started', ?, ?)
        """,
        (candidate_id, target_metadata, created_at, created_at),
    )


async def _orphan_counts(db: aiosqlite.Connection) -> tuple[int, int, int]:
    """统计三个引用候选的表中的孤儿行数。"""

    row = await (
        await db.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM reconsolidation_apply_ops ops
                 WHERE NOT EXISTS (SELECT 1 FROM reconsolidation_candidates c
                                   WHERE c.candidate_id = ops.candidate_id)),
                (SELECT COUNT(*) FROM reconsolidation_rollback_ops ops
                 WHERE NOT EXISTS (SELECT 1 FROM reconsolidation_candidates c
                                   WHERE c.candidate_id = ops.candidate_id)),
                (SELECT COUNT(*) FROM reconsolidation_actions actions
                 WHERE NOT EXISTS (SELECT 1 FROM reconsolidation_candidates c
                                   WHERE c.candidate_id = actions.candidate_id))
            """
        )
    ).fetchone()
    assert row is not None
    return (int(row[0]), int(row[1]), int(row[2]))


async def _has_pending_unique_index(db: aiosqlite.Connection) -> bool:
    """返回 pending 唯一索引是否存在。"""

    row = await (
        await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' "
            "AND name='idx_reconsolidation_candidates_pending_key'"
        )
    ).fetchone()
    return row is not None


@pytest.mark.asyncio
async def test_legacy_pending_dedup_keeps_inflight_intent_without_orphans(
    tmp_path: Path,
) -> None:
    """单一未收口 intent 的重复组：保留该行、清掉无 intent 重复行且不留孤儿。"""

    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    async with aiosqlite.connect(store.db_path) as db:
        # 唯一索引尚未建立的旧库才可能出现重复 pending 候选
        await db.execute("DROP INDEX idx_reconsolidation_candidates_pending_key")
        # intent 挂在最旧一行：旧的「保留最新」规则会把它删掉
        await _insert_pending_candidate(db, "candidate-with-intent", "1")
        await _insert_pending_candidate(db, "candidate-plain", "2")
        await _insert_pending_candidate(db, "candidate-newest", "3")
        await _insert_apply_intent(
            db, "candidate-with-intent", '{"access_count": 8}', "1"
        )
        await db.execute(
            """
            INSERT INTO reconsolidation_actions (
                action_id, candidate_id, action, reason_code, created_at
            ) VALUES ('act-plain', 'candidate-plain', 'stage', 'proposed', '2')
            """
        )
        await db.commit()

    await store.initialize()

    async with aiosqlite.connect(store.db_path) as db:
        pending = await (
            await db.execute(
                "SELECT candidate_id FROM reconsolidation_candidates "
                "WHERE status='pending'"
            )
        ).fetchall()
        orphans = await _orphan_counts(db)
        index_present = await _has_pending_unique_index(db)

    assert [row[0] for row in pending] == ["candidate-with-intent"]
    assert orphans == (0, 0, 0)
    assert index_present is True
    incomplete = await store.list_incomplete_applies()
    assert [item["candidate_id"] for item in incomplete] == ["candidate-with-intent"]
    restored = await store.get_candidate("candidate-with-intent")
    assert restored is not None
    assert restored["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", ["candidate-first", "candidate-second"])
async def test_legacy_duplicate_intents_are_deferred_to_recovery(
    tmp_path: Path, committed: str
) -> None:
    """同一重复组有多条未收口 intent 时：不得删除任何恢复依据，交由恢复收口。"""

    metadata_first = '{"access_count": 8, "last_reconsolidated_at": 1.0}'
    metadata_second = '{"access_count": 8, "last_reconsolidated_at": 2.0}'
    committed_metadata = (
        metadata_first if committed == "candidate-first" else metadata_second
    )
    store = ReconsolidationStore(tmp_path / "reconsolidation.db")
    await store.initialize()
    async with aiosqlite.connect(store.db_path) as db:
        await db.execute("DROP INDEX idx_reconsolidation_candidates_pending_key")
        await _insert_pending_candidate(
            db, "candidate-first", "1", old_metadata='{"access_count": 8}'
        )
        await _insert_pending_candidate(
            db, "candidate-second", "2", old_metadata='{"access_count": 8}'
        )
        await _insert_apply_intent(db, "candidate-first", metadata_first, "1")
        await _insert_apply_intent(db, "candidate-second", metadata_second, "2")
        await db.commit()

    # 迁移：两条未收口 intent 冲突 → 延迟去重并跳过唯一索引，保留全部证据
    await store.initialize()
    async with aiosqlite.connect(store.db_path) as db:
        pending = await (
            await db.execute(
                "SELECT candidate_id FROM reconsolidation_candidates "
                "WHERE status='pending' ORDER BY candidate_id"
            )
        ).fetchall()
        intent_count = await (
            await db.execute("SELECT COUNT(*) FROM reconsolidation_apply_ops")
        ).fetchone()
        orphans_before = await _orphan_counts(db)
        index_present = await _has_pending_unique_index(db)

    assert [row[0] for row in pending] == ["candidate-first", "candidate-second"]
    assert intent_count == (2,)
    assert orphans_before == (0, 0, 0)
    assert index_present is False

    # 真实启动恢复：已写 canonical 的候选必须收口为 approved，另一条才 blocked
    canonical: dict[str, Any] = {
        "id": 7,
        "text": "修正后的记忆正文内容",
        "updated_at": "r-8",
        "metadata": committed_metadata,
    }
    rewrites: list[int] = []

    async def _get_memory(memory_id: int) -> dict[str, Any]:
        return canonical if memory_id == 7 else {}

    async def _update_memory(memory_id: int, _payload: Any, **_kwargs: Any) -> bool:
        rewrites.append(memory_id)
        return False

    manager = ReconsolidationManager(
        store,
        get_memory_cb=_get_memory,
        update_memory_cb=_update_memory,
        enabled=True,
    )
    outcome = await manager.recover_incomplete_applies()

    assert outcome == {"recovered": 1, "blocked": 1}
    assert rewrites == []
    statuses: dict[str, str] = {}
    for candidate_id in ("candidate-first", "candidate-second"):
        candidate = await store.get_candidate(candidate_id)
        assert candidate is not None
        statuses[candidate_id] = str(candidate["status"])
    assert statuses == {committed: "approved", _other(committed): "failed"}
    assert await store.list_incomplete_applies() == []

    async with aiosqlite.connect(store.db_path) as db:
        orphans_after = await _orphan_counts(db)

    # 收口后 pending 重复消失，下一次初始化即可建唯一索引
    await store.initialize()
    async with aiosqlite.connect(store.db_path) as db:
        index_after = await _has_pending_unique_index(db)

    assert orphans_after == (0, 0, 0)
    assert index_after is True


def _other(candidate_id: str) -> str:
    """返回同一重复组中的另一条候选 ID。"""

    return (
        "candidate-second" if candidate_id == "candidate-first" else "candidate-first"
    )
