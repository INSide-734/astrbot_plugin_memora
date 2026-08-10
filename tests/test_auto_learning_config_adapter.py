"""自主学习配置写适配器的契约测试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.base.config_manager import (
    ConfigApplyResult,
    ConfigConflictError,
    ConfigPersistenceError,
    ConfigValidationError,
)
from core.features.learning.infrastructure.learning_config_adapter import (
    LearningConfigAdapter,
)


@dataclass
class _ConfigManager:
    """以可控快照模拟 ConfigManager 的最小协议。"""

    snapshots: list[tuple[dict[str, Any], str]]
    apply_result: ConfigApplyResult | Exception | None = None

    def __post_init__(self) -> None:
        """为异步调用创建可观察 mock。"""

        self.get_config_snapshot_async = AsyncMock(side_effect=self.snapshots)
        self.apply_config_changes = AsyncMock(side_effect=self._apply)

    async def _apply(self, *_args: Any, **_kwargs: Any) -> ConfigApplyResult:
        """返回预设结果或重新抛出预设异常。"""

        if isinstance(self.apply_result, Exception):
            raise self.apply_result
        assert self.apply_result is not None
        return self.apply_result


def _snapshot(document: float, graph: float) -> dict[str, Any]:
    """构造仅包含目标权重的配置快照。"""

    return {
        "graph_memory": {
            "document_route_weight": document,
            "graph_route_weight": graph,
        }
    }


@pytest.mark.asyncio
async def test_get_weight_snapshot_returns_authoritative_revision_and_hashes() -> None:
    """prepared intent 必须能在 writer 前取得受验证的权威权重快照。"""

    before = _snapshot(0.65, 0.35)
    manager = _ConfigManager([(before, "rev-before")])

    snapshot = await LearningConfigAdapter(manager).get_weight_snapshot()

    assert snapshot.revision == "rev-before"
    assert snapshot.as_weights() == {
        "document_route_weight": 0.65,
        "graph_route_weight": 0.35,
    }
    assert len(snapshot.config_hash) == 64
    assert len(snapshot.weight_hash) == 64


@pytest.mark.asyncio
async def test_apply_uses_only_canonical_paths_and_verifies_post_snapshot() -> None:
    """成功提交必须使用两个固定路径并返回经重读验证的真实修订。"""

    before = _snapshot(0.65, 0.35)
    after = _snapshot(0.61, 0.39)
    manager = _ConfigManager(
        [(before, "rev-before"), (after, "rev-after")],
        ConfigApplyResult(
            "rev-after",
            (
                "graph_memory.document_route_weight",
                "graph_memory.graph_route_weight",
            ),
        ),
    )

    result = await LearningConfigAdapter(manager).apply_weights(
        {"document_route_weight": 0.61, "graph_route_weight": 0.39},
        expected_revision="rev-before",
    )

    manager.apply_config_changes.assert_awaited_once_with(
        {
            "graph_memory.document_route_weight": 0.61,
            "graph_memory.graph_route_weight": 0.39,
        },
        expected_revision="rev-before",
        persist=True,
    )
    assert result.requested_revision == "rev-before"
    assert result.applied_revision == "rev-after"
    assert result.changed_paths == (
        "graph_memory.document_route_weight",
        "graph_memory.graph_route_weight",
    )
    assert result.applied is True
    assert result.no_op is False
    assert result.reason_code == "config_applied"
    assert result.before_hash != result.after_hash


@pytest.mark.asyncio
async def test_apply_reports_noop_without_calling_config_manager_writer() -> None:
    """目标权重已生效时必须稳定返回 no-op 而不创建配置写入。"""

    before = _snapshot(0.65, 0.35)
    manager = _ConfigManager([(before, "rev-before")])

    result = await LearningConfigAdapter(manager).apply_weights(
        {"document_route_weight": 0.65, "graph_route_weight": 0.35},
        expected_revision="rev-before",
    )

    manager.apply_config_changes.assert_not_awaited()
    assert result.applied is False
    assert result.no_op is True
    assert result.reason_code == "config_noop"
    assert result.applied_revision == "rev-before"


@pytest.mark.asyncio
async def test_apply_rejects_invalid_weights_without_writer() -> None:
    """布尔、越界和非互补权重不得到达 ConfigManager。"""

    manager = _ConfigManager([(_snapshot(0.65, 0.35), "rev-before")])

    result = await LearningConfigAdapter(manager).apply_weights(
        {"document_route_weight": True, "graph_route_weight": 0.35},
        expected_revision="rev-before",
    )

    manager.apply_config_changes.assert_not_awaited()
    assert result.applied is False
    assert result.reason_code == "config_validation_failed"


@pytest.mark.asyncio
async def test_apply_maps_conflict_and_validation_failures() -> None:
    """最终 CAS 冲突与配置校验失败必须保留稳定原因码。"""

    before = _snapshot(0.65, 0.35)
    conflict = _ConfigManager(
        [(before, "rev-before")],
        ConfigConflictError("rev-before", "rev-current"),
    )
    validation = _ConfigManager(
        [(before, "rev-before")],
        ConfigValidationError({"graph_memory.document_route_weight": "invalid"}),
    )

    conflict_result = await LearningConfigAdapter(conflict).apply_weights(
        {"document_route_weight": 0.61, "graph_route_weight": 0.39},
        expected_revision="rev-before",
    )
    validation_result = await LearningConfigAdapter(validation).apply_weights(
        {"document_route_weight": 0.61, "graph_route_weight": 0.39},
        expected_revision="rev-before",
    )

    assert conflict_result.reason_code == "config_revision_conflict"
    assert validation_result.reason_code == "config_validation_failed"


@pytest.mark.asyncio
async def test_persistence_failure_with_unchanged_post_snapshot_is_not_applied() -> (
    None
):
    """可证明未提交的持久化异常必须返回非恢复性失败。"""

    before = _snapshot(0.65, 0.35)
    manager = _ConfigManager(
        [(before, "rev-before"), (before, "rev-before")],
        ConfigPersistenceError("write failed"),
    )

    result = await LearningConfigAdapter(manager).apply_weights(
        {"document_route_weight": 0.61, "graph_route_weight": 0.39},
        expected_revision="rev-before",
    )

    assert result.applied is False
    assert result.reason_code == "config_persistence_failed"


@pytest.mark.asyncio
async def test_unknown_post_commit_state_requires_recovery() -> None:
    """无法与提交前或目标状态对应的异常后快照必须 fail-closed。"""

    before = _snapshot(0.65, 0.35)
    diverged = _snapshot(0.52, 0.48)
    manager = _ConfigManager(
        [(before, "rev-before"), (diverged, "rev-other")],
        ConfigPersistenceError("write failed"),
    )

    result = await LearningConfigAdapter(manager).apply_weights(
        {"document_route_weight": 0.61, "graph_route_weight": 0.39},
        expected_revision="rev-before",
    )

    assert result.applied is False
    assert result.reason_code == "learning_publish_recovery_required"


@pytest.mark.asyncio
async def test_post_read_mismatch_requires_recovery() -> None:
    """writer 成功但修订、路径或权威权重不一致时必须进入恢复状态。"""

    before = _snapshot(0.65, 0.35)
    manager = _ConfigManager(
        [(before, "rev-before"), (_snapshot(0.52, 0.48), "rev-other")],
        ConfigApplyResult(
            "rev-after",
            (
                "graph_memory.document_route_weight",
                "graph_memory.graph_route_weight",
            ),
        ),
    )

    result = await LearningConfigAdapter(manager).apply_weights(
        {"document_route_weight": 0.61, "graph_route_weight": 0.39},
        expected_revision="rev-before",
    )

    assert result.applied is False
    assert result.reason_code == "learning_publish_recovery_required"


@pytest.mark.asyncio
async def test_cancelled_error_propagates() -> None:
    """取消不得被 adapter 转换为普通失败。"""

    before = _snapshot(0.65, 0.35)
    manager = _ConfigManager([(before, "rev-before")])
    manager.apply_config_changes.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await LearningConfigAdapter(manager).apply_weights(
            {"document_route_weight": 0.61, "graph_route_weight": 0.39},
            expected_revision="rev-before",
        )
