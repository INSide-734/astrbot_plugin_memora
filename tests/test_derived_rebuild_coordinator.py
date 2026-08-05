"""验证统一派生重建协调器的顺序、降级和 canonical 保护。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.initializer.db_setup import DatabaseSetup
from core.initializer.derived_rebuild_coordinator import DerivedRebuildCoordinator


def _build_components(*, evolution_mode: str = "active"):
    """创建不连接真实数据库的协调器测试替身。"""

    validator = MagicMock()
    validator._get_document_count = AsyncMock(return_value=3)
    validator.rebuild_indexes = AsyncMock(
        return_value={
            "success": True,
            "processed": 3,
            "errors": 0,
            "total": 3,
        }
    )
    engine = MagicMock()
    engine.rebuild_graph_index = AsyncMock(return_value={"rebuilt": 3, "skipped": 0})
    engine.note_proposal_pipeline = None
    manager = MagicMock()
    manager.mode = evolution_mode
    manager.rebuild_from_canonical = AsyncMock(
        return_value={
            "success": True,
            "canonical_sources": 3,
            "scheduled_jobs": 3,
            "reason_code": "derived_rebuild_scheduled",
        }
    )
    return (
        DerivedRebuildCoordinator(validator, engine, manager),
        validator,
        engine,
        manager,
    )


@pytest.mark.asyncio
async def test_rebuild_all_runs_in_fixed_order() -> None:
    """索引成功后才进入 graph，再进入 relation/projection 重建。"""

    coordinator, validator, engine, manager = _build_components()
    order: list[str] = []

    async def rebuild_indexes(*_args):
        """记录索引阶段。"""

        return await _record_async(order, "indexes", {"success": True})

    async def rebuild_graph():
        """记录图阶段。"""

        return await _record_async(order, "graph", {"rebuilt": 3})

    async def rebuild_evolution():
        """记录 Evolution 阶段。"""

        return await _record_async(
            order,
            "evolution",
            {"success": True, "scheduled_jobs": 3},
        )

    validator.rebuild_indexes.side_effect = rebuild_indexes
    engine.rebuild_graph_index.side_effect = rebuild_graph
    manager.rebuild_from_canonical.side_effect = rebuild_evolution

    result = await coordinator.rebuild_all()

    assert result["success"] is True
    assert order == ["indexes", "graph", "evolution"]
    assert result["canonical"]["documents"] == 3


async def _record_async(order: list[str], name: str, result: dict) -> dict:
    """记录阶段调用顺序并返回阶段结果。"""

    order.append(name)
    return result


@pytest.mark.asyncio
async def test_index_failure_keeps_canonical_and_continues_degraded_rebuild() -> None:
    """FTS/向量阶段失败时仍可报告后续阶段，但整体必须降级。"""

    coordinator, validator, engine, manager = _build_components()
    validator.rebuild_indexes.return_value = {
        "success": False,
        "processed": 1,
        "errors": 2,
        "total": 3,
    }

    result = await coordinator.rebuild_all()

    assert result["success"] is False
    assert result["degraded"] is True
    assert result["reason_code"] == "index_rebuild_failed"
    assert result["canonical"]["documents"] == 3
    assert result["stages"]["graph"]["status"] == "completed"
    assert result["stages"]["evolution"]["status"] == "completed"
    validator._get_document_count.assert_awaited_once_with()
    manager.rebuild_from_canonical.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_graph_failure_does_not_claim_all_derived_success() -> None:
    """图重建异常时 relation/projection 阶段仍执行，但结果保持失败。"""

    coordinator, _validator, engine, manager = _build_components()
    engine.rebuild_graph_index.side_effect = RuntimeError("graph provider failed")

    result = await coordinator.rebuild_all()

    assert result["success"] is False
    assert result["reason_code"] == "graph_rebuild_failed"
    assert result["stages"]["graph"]["reason_code"] == "graph_rebuild_failed"
    manager.rebuild_from_canonical.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_evolution_failure_returns_stable_degraded_result() -> None:
    """派生 relation/projection 重建失败时不得伪装成完整成功。"""

    coordinator, _validator, _engine, manager = _build_components()
    manager.rebuild_from_canonical.return_value = {
        "success": False,
        "reason_code": "derived_rebuild_failed",
    }

    result = await coordinator.rebuild_all()

    assert result["success"] is False
    assert result["reason_code"] == "derived_rebuild_failed"
    assert result["stages"]["evolution"]["status"] == "failed"


@pytest.mark.asyncio
async def test_missing_canonical_stops_before_mutating_derived_indexes() -> None:
    """无法读取 canonical 时不应调用任何派生重建入口。"""

    coordinator, validator, engine, manager = _build_components()
    validator._get_document_count.side_effect = RuntimeError("database unavailable")

    result = await coordinator.rebuild_all()

    assert result["success"] is False
    assert result["reason_code"] == "canonical_unavailable"
    validator.rebuild_indexes.assert_not_awaited()
    engine.rebuild_graph_index.assert_not_awaited()
    manager.rebuild_from_canonical.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_rebuild_propagates() -> None:
    """取消信号必须向调用方传播，不能被阶段降级逻辑吞掉。"""

    coordinator, validator, _engine, _manager = _build_components()
    validator.rebuild_indexes.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await coordinator.rebuild_all()


@pytest.mark.asyncio
async def test_disabled_evolution_is_reported_as_skipped() -> None:
    """关闭 Evolution 时不创建派生任务，但索引与图重建仍可成功。"""

    coordinator, _validator, _engine, manager = _build_components(
        evolution_mode="disabled"
    )

    result = await coordinator.rebuild_all()

    assert result["success"] is True
    assert result["stages"]["evolution"]["status"] == "skipped"
    manager.rebuild_from_canonical.assert_not_awaited()


@pytest.mark.asyncio
async def test_database_setup_uses_coordinator_for_inconsistent_indexes() -> None:
    """启动维护路径应委托协调器，而不是绕过 graph/evolution 阶段。"""

    validator = MagicMock()
    validator.check_consistency = AsyncMock(
        return_value=SimpleNamespace(
            is_consistent=False,
            needs_rebuild=True,
            reason="索引缺失",
            documents_count=3,
            bm25_count=1,
            vector_count=1,
        )
    )
    validator.rebuild_indexes = AsyncMock()
    coordinator = MagicMock()
    coordinator.rebuild_all = AsyncMock(
        return_value={"success": True, "reason_code": "derived_rebuild_completed"}
    )

    result = await DatabaseSetup.auto_rebuild_index_if_needed(
        validator,
        MagicMock(),
        coordinator,
    )

    assert result["success"] is True
    coordinator.rebuild_all.assert_awaited_once_with()
    validator.rebuild_indexes.assert_not_awaited()
