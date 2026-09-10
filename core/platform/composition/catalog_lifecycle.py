"""话题目录组合生命周期的构造与关停辅助。"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from ...features.memory.infrastructure.validators import IndexValidator
from .derived_rebuild_coordinator import DerivedRebuildCoordinator


def build_catalog_components(
    db_path: Path,
    db: Any,
    memory_engine: Any,
    memory_evolution_manager: Any,
) -> tuple[IndexValidator, DerivedRebuildCoordinator]:
    """构造目录重建 owner 及其索引验证器。"""
    index_validator = IndexValidator(str(db_path), db)
    coordinator = DerivedRebuildCoordinator(
        index_validator,
        memory_engine,
        memory_evolution_manager,
        catalog_store=memory_engine.topic_catalog_store,
    )
    return index_validator, coordinator


async def finalize_catalog_lifecycle(
    db_setup: Any,
    index_validator: IndexValidator,
    memory_engine: Any,
    coordinator: DerivedRebuildCoordinator,
) -> dict[str, Any]:
    """完成启动期目录重建检查并返回安全就绪决定。"""
    await db_setup.auto_rebuild_index_if_needed(
        index_validator,
        memory_engine,
        coordinator,
    )
    return await coordinator.catalog_readiness_decision()


async def reconcile_catalog_for_shutdown(initializer: Any) -> dict[str, Any]:
    """在 canonical 数据库关闭前收敛话题目录并保留可恢复状态。"""
    coordinator = initializer.derived_rebuild_coordinator
    if coordinator is None:
        return {
            "success": True,
            "status": "skipped",
            "reason_code": "catalog_coordinator_unavailable",
        }
    reconcile = getattr(coordinator, "reconcile_catalog_for_shutdown", None)
    if not callable(reconcile):
        return {
            "success": False,
            "reason_code": "catalog_shutdown_reconcile_unavailable",
        }
    result = reconcile()
    if inspect.isawaitable(result):
        result = await result
    return (
        result
        if isinstance(result, dict)
        else {
            "success": False,
            "reason_code": "catalog_shutdown_reconcile_failed",
        }
    )


__all__ = [
    "build_catalog_components",
    "finalize_catalog_lifecycle",
    "reconcile_catalog_for_shutdown",
]
