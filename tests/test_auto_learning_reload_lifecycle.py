"""自主学习 reload operation 的持久化、重启对账与失败状态回归。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.managers.auto_learning import AutoLearningManager
from core.plugin_reload_lifecycle import run_scheduled_plugin_reload

_CANDIDATE_ID = "candidate_reload_state_01"
_PUBLICATION_ID = "publication_reload_st01"
_OPERATION_ID = "operation_reload_state01"
_LEARNING_PATHS = (
    "graph_memory.document_route_weight",
    "graph_memory.graph_route_weight",
)


class _FeedbackManager:
    """提供 reload 状态测试所需的最小反馈策略。"""

    policy = SimpleNamespace(
        baseline_document_weight=0.65,
        baseline_graph_weight=0.35,
    )


def _manager(data_dir: str) -> AutoLearningManager:
    """构造使用真实安全状态文件的 manager。"""

    return AutoLearningManager(  # type: ignore[arg-type]
        _FeedbackManager(),
        data_dir=data_dir,
        enabled=True,
    )


def _install_active_publication(manager: AutoLearningManager) -> None:
    """安装一个已提交且等待 reload 的 active publication。"""

    manager._candidates[_CANDIDATE_ID] = {
        "candidate_id": _CANDIDATE_ID,
        "status": "published",
    }
    manager._publications[_PUBLICATION_ID] = {
        "publication_id": _PUBLICATION_ID,
        "publication_revision": _PUBLICATION_ID,
        "parent_publication_revision": None,
        "candidate_id": _CANDIDATE_ID,
        "before_document_weight": 0.65,
        "before_graph_weight": 0.35,
        "after_document_weight": 0.70,
        "after_graph_weight": 0.30,
        "status": "active",
    }
    manager._active_publication_revision = _PUBLICATION_ID


@pytest.mark.asyncio
async def test_queued_reload_is_persisted_and_reconciled_after_restart(
    tmp_path: object,
) -> None:
    """新生命周期加载目标权重后把 queued operation 收口为 succeeded。"""

    data_dir = str(tmp_path)
    manager = _manager(data_dir)
    _install_active_publication(manager)
    recorded = await manager.record_reload_operation(
        action="publish",
        candidate_id=_CANDIDATE_ID,
        operation_id=_OPERATION_ID,
        applied_revision="config-revision-2",
        changed_paths=_LEARNING_PATHS,
        state="queued",
    )
    assert recorded["state"] == "queued"

    restarted = _manager(data_dir)
    await restarted.load_state()
    result = await restarted.reconcile_reload_operation(
        effective_document_weight=0.70,
        effective_graph_weight=0.30,
    )

    assert result is not None
    assert result["state"] == "succeeded"
    status = await restarted.get_status_snapshot()
    assert status["reload"]["state"] == "succeeded"


@pytest.mark.asyncio
async def test_restart_with_wrong_effective_weights_marks_reload_failed(
    tmp_path: object,
) -> None:
    """新生命周期未加载目标权重时明确标记 failed，不伪造成功。"""

    data_dir = str(tmp_path)
    manager = _manager(data_dir)
    _install_active_publication(manager)
    await manager.record_reload_operation(
        action="publish",
        candidate_id=_CANDIDATE_ID,
        operation_id=_OPERATION_ID,
        applied_revision="config-revision-2",
        changed_paths=_LEARNING_PATHS,
        state="queued",
    )
    await manager.update_reload_operation(
        _OPERATION_ID,
        state="running",
        reason_code="reload_started",
    )

    restarted = _manager(data_dir)
    await restarted.load_state()
    result = await restarted.reconcile_reload_operation(
        effective_document_weight=0.65,
        effective_graph_weight=0.35,
    )

    assert result is not None
    assert result["state"] == "failed"
    assert result["reason_code"] == "runtime_config_mismatch"


@pytest.mark.asyncio
async def test_corrupt_state_keeps_reload_pending_without_blocking_startup(
    tmp_path: object,
) -> None:
    """主状态损坏时 reload 对账不得写回或阻断生命周期启动。"""

    data_dir = str(tmp_path)
    manager = _manager(data_dir)
    _install_active_publication(manager)
    await manager.record_reload_operation(
        action="publish",
        candidate_id=_CANDIDATE_ID,
        operation_id=_OPERATION_ID,
        applied_revision="config-revision-2",
        changed_paths=_LEARNING_PATHS,
        state="queued",
    )
    await manager.update_reload_operation(
        _OPERATION_ID,
        state="running",
        reason_code="reload_started",
    )

    state_path = manager._state_store.path
    state_path.write_text("{broken-primary", encoding="utf-8")
    restarted = _manager(data_dir)
    await restarted.load_state()

    result = await restarted.reconcile_reload_operation(
        effective_document_weight=0.70,
        effective_graph_weight=0.30,
    )

    assert result is not None
    assert result["state"] == "queued"
    status = await restarted.get_status_snapshot()
    assert status["recovery"]["state_recovery_required"] is True


@pytest.mark.asyncio
async def test_reload_failure_callback_is_persisted(tmp_path: object) -> None:
    """宿主重载失败回调把 queued/running operation 收口为 failed。"""

    manager = _manager(str(tmp_path))
    _install_active_publication(manager)
    await manager.record_reload_operation(
        action="publish",
        candidate_id=_CANDIDATE_ID,
        operation_id=_OPERATION_ID,
        applied_revision="config-revision-2",
        changed_paths=_LEARNING_PATHS,
        state="queued",
    )

    result = await manager.update_reload_operation(
        _OPERATION_ID,
        state="failed",
        reason_code="host_reload_failed",
    )

    assert result is not None
    assert result["state"] == "failed"
    assert result["reason_code"] == "host_reload_failed"


@pytest.mark.asyncio
async def test_invalid_reload_transition_does_not_overwrite_terminal_state(
    tmp_path: object,
) -> None:
    """succeeded 终态不能被迟到的旧宿主失败回调覆盖。"""

    manager = _manager(str(tmp_path))
    _install_active_publication(manager)
    await manager.record_reload_operation(
        action="publish",
        candidate_id=_CANDIDATE_ID,
        operation_id=_OPERATION_ID,
        applied_revision="config-revision-2",
        changed_paths=_LEARNING_PATHS,
        state="queued",
    )
    await manager.update_reload_operation(
        _OPERATION_ID,
        state="succeeded",
        reason_code="runtime_config_reconciled",
    )

    result = await manager.update_reload_operation(
        _OPERATION_ID,
        state="failed",
        reason_code="host_reload_failed",
    )

    assert result is not None
    assert result["state"] == "succeeded"


@pytest.mark.asyncio
async def test_stale_reload_callback_cannot_regress_new_instance_terminal_state(
    tmp_path: object,
) -> None:
    """旧插件实例的回调不能覆盖新实例已经持久化的成功终态。"""

    data_dir = str(tmp_path)
    old_manager = _manager(data_dir)
    _install_active_publication(old_manager)
    await old_manager.record_reload_operation(
        action="publish",
        candidate_id=_CANDIDATE_ID,
        operation_id=_OPERATION_ID,
        applied_revision="config-revision-2",
        changed_paths=_LEARNING_PATHS,
        state="queued",
    )
    old_revision = old_manager._state_revision

    new_manager = _manager(data_dir)
    await new_manager.load_state()
    result = await new_manager.reconcile_reload_operation(
        effective_document_weight=0.70,
        effective_graph_weight=0.30,
    )
    assert result is not None
    assert result["state"] == "succeeded"

    reload_plugin = AsyncMock(return_value=False)
    old_plugin = SimpleNamespace(
        _terminating=False,
        initializer=SimpleNamespace(
            memory_engine=SimpleNamespace(auto_learning=old_manager)
        ),
    )
    with patch("core.plugin_reload_lifecycle.asyncio.sleep", new=AsyncMock()):
        await run_scheduled_plugin_reload(
            old_plugin,
            reload_plugin,
            reason="auto_learning",
            learning_operation_id=_OPERATION_ID,
            expected_state_revision=old_revision,
        )

    reload_plugin.assert_not_awaited()
    persisted = await old_manager._state_store.load()
    assert persisted.payload is not None
    assert persisted.payload["reload_operation"]["state"] == "succeeded"


@pytest.mark.asyncio
async def test_reload_callback_revision_conflict_is_fail_closed(
    tmp_path: object,
) -> None:
    """过期状态写入者应返回稳定 revision 冲突，不得覆盖新状态。"""

    from core.managers.auto_learning_state import (
        AutoLearningStatePersistenceError,
        AutoLearningStateStore,
    )

    store = AutoLearningStateStore(str(tmp_path / "auto_learning.json"))
    first_payload = {"marker": "first"}
    second_payload = {"marker": "second"}
    first_revision = await store.save(first_payload)

    with pytest.raises(AutoLearningStatePersistenceError) as exc_info:
        await store.save(
            second_payload,
            expected_state_revision=first_revision + "stale",
        )

    assert exc_info.value.reason_code == "learning_state_revision_conflict"
    assert (await store.load()).payload == first_payload
