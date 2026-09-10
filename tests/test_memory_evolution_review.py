"""Memory Evolution 高影响 relation 复核闭环测试。"""

from __future__ import annotations

import json

import pytest

from core.features.evolution.domain import (
    DerivedApplyPlan,
    DerivedState,
    RelationType,
    RelationView,
)
from core.features.evolution.infrastructure import (
    DerivedReviewConflictError,
    DerivedReviewNotAllowedError,
    DerivedReviewSourceError,
    MemoryEvolutionStore,
)


def _candidate_plan(
    relation_id: str = "conflict-1",
    *,
    relation_type: RelationType = RelationType.CONTRADICTS,
) -> DerivedApplyPlan:
    """构造带稳定 source revision 的高影响候选写入计划。"""

    return DerivedApplyPlan(
        relations=(
            RelationView(
                relation_id,
                1,
                2,
                relation_type,
                0.91,
                "private:user",
                "confidential",
                DerivedState.CANDIDATE,
                "revision-1",
                "revision-2",
            ),
        ),
        source_revisions={1: "revision-1", 2: "revision-2"},
        origin_job_id="job-reviewable",
    )


async def _seed_canonical_sources(store: MemoryEvolutionStore) -> None:
    """写入审批时需要二次核对的最小 canonical source 快照。"""

    connection = store.connection
    assert connection is not None
    await connection.execute(
        """CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY,
        metadata TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
        )"""
    )
    metadata = json.dumps(
        {"scope_key": "private:user", "privacy_level": "confidential"}
    )
    await connection.executemany(
        "INSERT INTO documents(id,metadata,created_at,updated_at) VALUES (?,?,?,?)",
        (
            (1, metadata, "revision-1", "revision-1"),
            (2, metadata, "revision-2", "revision-2"),
        ),
    )
    await connection.commit()


@pytest.mark.asyncio
async def test_review_schema_is_created_and_old_relations_gain_revision(
    tmp_path,
) -> None:
    """初始化必须创建审计表，并为 relation 提供候选 CAS revision。"""

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()

    tables = await store._fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    columns = await store._fetch_all("PRAGMA table_info(memory_relations)")

    assert "memory_derived_review_actions" in {row["name"] for row in tables}
    assert "revision" in {row["name"] for row in columns}
    await store.close()


@pytest.mark.asyncio
async def test_approve_revalidates_sources_and_records_auditable_action(
    tmp_path,
) -> None:
    """审批只能激活 source 仍有效的候选，并保存低敏动作历史。"""

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await _seed_canonical_sources(store)
    await store.apply_derived_plan(_candidate_plan())

    result = await store.review_relation_candidate(
        "conflict-1",
        action="approve",
        expected_revision=1,
    )
    active = await store.active_relations_for_seeds([1], scope_key="private:user")
    actions = await store.list_relation_review_actions("conflict-1")

    assert result["state"] == DerivedState.ACTIVE.value
    assert result["revision"] == 2
    assert [relation.relation_id for relation in active] == ["conflict-1"]
    assert [action["action"] for action in actions] == ["approve"]
    assert actions[0]["previous_state"] == DerivedState.CANDIDATE.value
    assert actions[0]["new_state"] == DerivedState.ACTIVE.value
    assert "source_memory_id" not in actions[0]
    assert "source_revision" not in actions[0]
    await store.close()


@pytest.mark.asyncio
async def test_reject_can_be_replayed_and_stale_revision_cannot_overwrite(
    tmp_path,
) -> None:
    """拒绝可审计并可重放为候选，所有动作都必须服从 revision CAS。"""

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await _seed_canonical_sources(store)
    await store.apply_derived_plan(_candidate_plan())

    rejected = await store.review_relation_candidate(
        "conflict-1",
        action="reject",
        expected_revision=1,
    )
    with pytest.raises(DerivedReviewConflictError):
        await store.review_relation_candidate(
            "conflict-1",
            action="replay",
            expected_revision=1,
        )
    replayed = await store.review_relation_candidate(
        "conflict-1",
        action="replay",
        expected_revision=rejected["revision"],
    )
    fixed_time = "2026-08-04T00:00:00+00:00"
    connection = store.connection
    assert connection is not None
    await connection.execute(
        "UPDATE memory_derived_review_actions "
        "SET action_id=CASE action WHEN 'reject' THEN ? WHEN 'replay' THEN ? "
        "ELSE action_id END, created_at=? WHERE relation_id=?",
        ("f" * 32, "0" * 32, fixed_time, "conflict-1"),
    )
    await connection.commit()
    actions = await store.list_relation_review_actions("conflict-1")
    await store.close()

    assert rejected["state"] == DerivedState.REJECTED.value
    assert replayed["state"] == DerivedState.CANDIDATE.value
    assert replayed["revision"] == 3
    assert actions[0]["created_at"] == actions[1]["created_at"]
    assert [action["action"] for action in actions] == ["reject", "replay"]
    assert [action["result_revision"] for action in actions] == [2, 3]


@pytest.mark.asyncio
async def test_background_upsert_cannot_reopen_rejected_candidate(tmp_path) -> None:
    """重复 proposal 不得绕过显式 replay 动作重新打开已拒绝候选。"""

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await store.apply_derived_plan(_candidate_plan())
    rejected = await store.review_relation_candidate(
        "conflict-1",
        action="reject",
        expected_revision=1,
    )
    await store.apply_derived_plan(_candidate_plan())
    stored = await store.get_relation_review_candidate("conflict-1")
    actions = await store.list_relation_review_actions("conflict-1")
    await store.close()

    assert stored == rejected
    assert [action["action"] for action in actions] == ["reject"]


@pytest.mark.asyncio
async def test_background_upsert_preserves_approved_active_relation(tmp_path) -> None:
    """重复后台 proposal 不得撤销已经人工批准的 active relation。"""

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await _seed_canonical_sources(store)
    await store.apply_derived_plan(_candidate_plan())
    approved = await store.review_relation_candidate(
        "conflict-1",
        action="approve",
        expected_revision=1,
    )

    await store.apply_derived_plan(_candidate_plan())
    stored = await store.get_relation_review_candidate("conflict-1")
    await store.close()

    assert approved["state"] == DerivedState.ACTIVE.value
    assert stored is not None
    assert stored["relation_id"] == approved["relation_id"]
    assert stored["state"] == DerivedState.ACTIVE.value
    assert stored["revision"] == approved["revision"] + 1


@pytest.mark.asyncio
async def test_approve_and_replay_fail_when_source_revision_changed(tmp_path) -> None:
    """source 已变化时不得把旧候选激活或重新送回待审状态。"""

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await _seed_canonical_sources(store)
    await store.apply_derived_plan(_candidate_plan())
    connection = store.connection
    assert connection is not None
    await connection.execute(
        "UPDATE documents SET updated_at=? WHERE id=?",
        ("revision-1-new", 1),
    )
    await connection.commit()

    with pytest.raises(DerivedReviewSourceError):
        await store.review_relation_candidate(
            "conflict-1",
            action="approve",
            expected_revision=1,
        )
    candidate = await store.get_relation_review_candidate("conflict-1")

    assert candidate is not None
    assert candidate["state"] == DerivedState.CANDIDATE.value
    assert candidate["revision"] == 1
    await store.close()


@pytest.mark.asyncio
async def test_low_impact_candidate_does_not_enter_high_impact_review_queue(
    tmp_path,
) -> None:
    """低置信 same_episode 候选不得伪装成需要人工裁决的冲突。"""

    store = MemoryEvolutionStore(str(tmp_path / "memory.db"))
    await store.initialize()
    await store.apply_derived_plan(
        _candidate_plan(
            relation_id="episode-candidate",
            relation_type=RelationType.SAME_EPISODE,
        )
    )

    assert await store.list_relation_review_candidates() == []
    with pytest.raises(DerivedReviewNotAllowedError):
        await store.review_relation_candidate(
            "episode-candidate",
            action="approve",
            expected_revision=1,
        )
    await store.close()
