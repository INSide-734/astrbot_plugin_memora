"""自主学习 tombstone 保留、引用保护与 reset 原子性回归。"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.managers.auto_learning import AutoLearningManager
from core.managers.auto_learning_state import AutoLearningStatePersistenceError


class _FeedbackManager:
    """提供 AutoLearningManager 构造所需的最小反馈策略。"""

    policy = SimpleNamespace(
        baseline_document_weight=0.65,
        baseline_graph_weight=0.35,
    )


def _manager() -> AutoLearningManager:
    """构造不访问磁盘且允许 reset 的 manager。"""

    return AutoLearningManager(_FeedbackManager(), enabled=True)  # type: ignore[arg-type]


def _tombstone(
    suffix: str,
    *,
    completed_at: str,
    publication_revision: str | None = None,
    status: str = "rolled_back",
) -> tuple[str, dict[str, object]]:
    """构造字段完整的 tombstone，并允许覆盖异常边界。"""

    tombstone_id = f"t{suffix:->21}"[-22:]
    operation_id = f"o{suffix:->21}"[-22:]
    candidate_id = f"c{suffix:->21}"[-22:]
    publication_id = publication_revision or f"p{suffix:->21}"[-22:]
    return tombstone_id, {
        "tombstone_id": tombstone_id,
        "operation_id": operation_id,
        "candidate_id": candidate_id,
        "publication_revision": publication_id,
        "status": status,
        "completed_at": completed_at,
    }


def _install_relationships(
    manager: AutoLearningManager,
    tombstone_id: str,
    tombstone: dict[str, object],
) -> None:
    """安装 tombstone 对应 publication、candidate 与幂等终态。"""

    publication_revision = str(tombstone["publication_revision"])
    candidate_id = str(tombstone["candidate_id"])
    operation_id = str(tombstone["operation_id"])
    manager._tombstones[tombstone_id] = tombstone
    manager._publications[publication_revision] = {
        "publication_revision": publication_revision,
        "candidate_id": candidate_id,
        "parent_publication_revision": None,
        "status": "rolled_back",
    }
    manager._candidates[candidate_id] = {
        "candidate_id": candidate_id,
        "evidence_revision": f"e{candidate_id[1:]}",
        "status": "rolled_back",
    }
    manager._terminal_operations[f"rollback:{tombstone_id}"] = {
        "restored": True,
        "operation_id": operation_id,
    }


@pytest.mark.asyncio
async def test_reset_prunes_expired_safe_tombstone_and_matching_terminal() -> None:
    """超过 30 天且关系完整的 rolled_back tombstone 可被安全裁剪。"""

    manager = _manager()
    old_time = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    tombstone_id, tombstone = _tombstone("old", completed_at=old_time)
    _install_relationships(manager, tombstone_id, tombstone)

    result = await manager.reset()

    assert result["reset"] is True
    assert result["tombstones_removed"] == 1
    assert manager._tombstones == {}
    assert manager._terminal_operations == {}
    assert manager._publications
    assert manager._candidates


@pytest.mark.asyncio
async def test_reset_keeps_invalid_incomplete_and_referenced_tombstones() -> None:
    """非法时间、关系缺失及恢复引用一律 fail-closed 保留。"""

    manager = _manager()
    old_time = (datetime.now(UTC) - timedelta(days=31)).isoformat()

    invalid_id, invalid = _tombstone("invalid", completed_at="not-a-time")
    _install_relationships(manager, invalid_id, invalid)

    incomplete_id, incomplete = _tombstone("missing", completed_at=old_time)
    manager._tombstones[incomplete_id] = incomplete

    referenced_id, referenced = _tombstone("recovery", completed_at=old_time)
    _install_relationships(manager, referenced_id, referenced)
    manager._recovery_records["recovery-record"] = {
        "operation_id": referenced["operation_id"],
        "candidate_id": referenced["candidate_id"],
        "publication_revision": referenced["publication_revision"],
    }

    result = await manager.reset()

    assert result["tombstones_removed"] == 0
    assert set(manager._tombstones) == {invalid_id, incomplete_id, referenced_id}
    assert len(manager._terminal_operations) == 2


@pytest.mark.asyncio
async def test_reset_keeps_active_publication_and_parent_chain_tombstones() -> None:
    """active publication 及其 parent 引用的过期 tombstone 必须保留。"""

    manager = _manager()
    old_time = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    parent_id, parent = _tombstone("parent", completed_at=old_time)
    child_id, child = _tombstone("child", completed_at=old_time)
    _install_relationships(manager, parent_id, parent)
    _install_relationships(manager, child_id, child)
    parent_revision = str(parent["publication_revision"])
    child_revision = str(child["publication_revision"])
    manager._publications[child_revision]["parent_publication_revision"] = (
        parent_revision
    )
    manager._active_publication_revision = child_revision

    result = await manager.reset()

    assert result["tombstones_removed"] == 0
    assert set(manager._tombstones) == {parent_id, child_id}
    assert len(manager._terminal_operations) == 2


@pytest.mark.asyncio
async def test_reset_capacity_prunes_oldest_by_utc_then_id() -> None:
    """超过容量时按 persisted UTC 时间和 tombstone ID 确定性删除最旧项。"""

    manager = _manager()
    manager._tombstone_max_entries = 2
    timestamp = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    identifiers: list[str] = []
    for suffix in ("c", "a", "b"):
        tombstone_id, tombstone = _tombstone(suffix, completed_at=timestamp)
        identifiers.append(tombstone_id)
        _install_relationships(manager, tombstone_id, tombstone)

    result = await manager.reset()

    assert result["tombstones_removed"] == 1
    assert sorted(manager._tombstones) == sorted(identifiers)[1:]


@pytest.mark.asyncio
async def test_reset_save_failure_restores_all_pruned_state() -> None:
    """reset 保存失败时恢复 candidate、evidence、tombstone 与 terminal 四组快照。"""

    manager = _manager()
    old_time = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    tombstone_id, tombstone = _tombstone("restore", completed_at=old_time)
    _install_relationships(manager, tombstone_id, tombstone)
    evidence_revision = str(
        manager._candidates[str(tombstone["candidate_id"])]["evidence_revision"]
    )
    manager._evidence_artifacts[evidence_revision] = {"marker": "keep"}
    before = {
        "candidates": copy.deepcopy(manager._candidates),
        "evidence": copy.deepcopy(manager._evidence_artifacts),
        "tombstones": copy.deepcopy(manager._tombstones),
        "terminal": copy.deepcopy(manager._terminal_operations),
    }
    manager._save_state = AsyncMock(  # type: ignore[method-assign]
        side_effect=AutoLearningStatePersistenceError("save_failed")
    )

    with pytest.raises(AutoLearningStatePersistenceError):
        await manager.reset()

    assert manager._candidates == before["candidates"]
    assert manager._evidence_artifacts == before["evidence"]
    assert manager._tombstones == before["tombstones"]
    assert manager._terminal_operations == before["terminal"]
