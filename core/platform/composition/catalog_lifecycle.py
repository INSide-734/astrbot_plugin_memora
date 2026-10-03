"""话题目录组合生命周期的构造与关停辅助。"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ...features.memory.infrastructure.validators import IndexValidator
from ...shared.errors import InitializationError
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


def runtime_publish_gate_required(backup_manager: Any, memory_engine: Any) -> bool:
    """判断本次启动是否处于迁移/恢复发布门（required）场景。

    只依据既有 owner 已发布的安全状态：存在阻塞中的 restore 事务（恢复到
    标记成功前持续阻塞），或 canonical 刚刚完成一次真实迁移。任一信号
    读取失败时按 required 处理，避免在未证明状态上发布 runtime。
    """

    if _restore_in_progress(backup_manager):
        return True
    status = getattr(memory_engine, "schema_migration_status", None)
    return str(getattr(status, "stage", "")) == "completed"


def _restore_in_progress(backup_manager: Any) -> bool:
    """读取阻塞中的恢复事务；证据不可读时 fail closed。"""

    if backup_manager is None:
        return False
    try:
        getter = getattr(backup_manager, "get_maintenance_state", None)
        if callable(getter):
            state = getter()
            return not isinstance(state, dict) or state.get("blocked") is not False
        checker = getattr(backup_manager, "has_pending_restores", None)
        return bool(checker()) if callable(checker) else True
    except Exception:
        logger.error("恢复维护状态读取失败，按发布门处理")
        return True


async def finalize_catalog_lifecycle(
    db_setup: Any,
    index_validator: IndexValidator,
    memory_engine: Any,
    coordinator: DerivedRebuildCoordinator,
    *,
    required: bool = False,
) -> dict[str, Any]:
    """完成启动期派生重建检查并返回安全就绪决定。

    ``required`` 由组合根按迁移/恢复发布门判定：此时强制完整重建派生面，
    任一 required 阶段失败或不可用都阻断启动，不进入 catalog readiness。
    聚合 ``success`` 不足以证明派生面完整——阶段 owner 把「运行时配置明确
    关闭该功能」也报告为 ``success=True``，所以 required 场景还要按阶段
    复核可用性（见 ``required_stage_block_reason``）。普通维护路径保持既有
    「派生失败只降级、canonical 不回滚」语义。
    """

    rebuild = await db_setup.auto_rebuild_index_if_needed(
        index_validator,
        memory_engine,
        coordinator,
        force_rebuild=required,
    )
    report = rebuild if isinstance(rebuild, dict) else {}
    rebuild_success = bool(report.get("success"))
    rebuild_reason = str(report.get("reason_code") or "")
    block_reason: str | None = None
    if required:
        if not rebuild_success:
            block_reason = rebuild_reason or "derived_rebuild_failed"
        else:
            block_reason = _required_stages_block_reason(coordinator, report)
    if block_reason is not None:
        logger.error(
            "迁移/恢复所需的派生重建未完成，reason_code=%s",
            block_reason,
        )
        raise InitializationError("runtime_publish_gate_blocked:" + block_reason)
    decision = await coordinator.catalog_readiness_decision()
    if not isinstance(decision, dict):
        decision = {}
    return {
        **decision,
        "publish_gate_required": required,
        "derived_rebuild_success": rebuild_success,
        "derived_rebuild_reason_code": rebuild_reason or "derived_rebuild_skipped",
    }


def _required_stages_block_reason(
    coordinator: DerivedRebuildCoordinator,
    report: dict[str, Any],
) -> str | None:
    """在 required 场景复核派生阶段可用性；无法证明完整时 fail closed。"""

    checker = getattr(coordinator, "required_stage_block_reason", None)
    if not callable(checker):
        return "required_stages_unverifiable"
    reason = checker(report)
    if reason is None:
        return None
    if isinstance(reason, str) and reason:
        return reason
    return "required_stages_unverifiable"


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
    "runtime_publish_gate_required",
]
