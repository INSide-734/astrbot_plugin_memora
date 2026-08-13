"""自主学习生产配置提交后的 reload operation 调度。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping

from astrbot.api import logger


async def schedule_learning_reload_operation(
    api: object,
    manager: object,
    *,
    action: str,
    candidate_id: str,
    operation_id: str,
    applied_revision: str,
    changed_paths: tuple[str, ...],
) -> str:
    """先持久化 queued，再同步安排插件重载并收口真实结果。

    Args:
        api: 提供插件实例或兼容重载回调的 API 对象。
        manager: 提供 reload operation 持久化方法的自主学习管理器。
        action: 当前生产动作，取值为 publish 或 rollback。
        candidate_id: 与动作绑定的候选 ID。
        operation_id: 本次配置提交的 operation ID。
        applied_revision: 已持久化的生产配置 revision。
        changed_paths: 经过 allowlist 裁剪的真实配置变更路径。

    Returns:
        ``queued`` 或保守的 ``restart_required`` 状态。

    Raises:
        asyncio.CancelledError: 任一持久化阶段被取消时保持取消语义。
    """

    recorder = getattr(manager, "record_reload_operation", None)
    if not callable(recorder):
        return "restart_required"
    try:
        recorded = recorder(
            action=action,
            candidate_id=candidate_id,
            operation_id=operation_id,
            applied_revision=applied_revision,
            changed_paths=changed_paths,
            state="queued",
        )
        if inspect.isawaitable(recorded):
            recorded = await recorded
    except asyncio.CancelledError:
        await _mark_reload_not_queued(manager, operation_id)
        raise
    except Exception:
        logger.error("[LearningApi] 自主学习 reload operation 持久化失败")
        await _mark_reload_not_queued(manager, operation_id)
        return "restart_required"
    if not isinstance(recorded, Mapping) or recorded.get("state") != "queued":
        await _mark_reload_not_queued(manager, operation_id)
        return "restart_required"

    plugin = getattr(api, "plugin", None)
    learning_scheduler = getattr(plugin, "schedule_learning_reload", None)
    compatibility_scheduler = getattr(api, "_schedule_plugin_reload", None)
    if not callable(compatibility_scheduler):
        compatibility_scheduler = None
    try:
        scheduled = bool(
            learning_scheduler(operation_id)
            if callable(learning_scheduler)
            else compatibility_scheduler(changed_paths)
            if callable(compatibility_scheduler)
            else False
        )
    except asyncio.CancelledError:
        await _mark_reload_not_queued(manager, operation_id)
        raise
    except Exception:
        logger.error("[LearningApi] 自主学习配置已提交，但安排插件重载失败")
        scheduled = False
    if scheduled:
        return "queued"

    await _mark_reload_not_queued(manager, operation_id)
    return "restart_required"


async def _mark_reload_not_queued(manager: object, operation_id: str) -> bool:
    """把未创建 task 的 operation 保守收口为 restart_required。"""

    updater = getattr(manager, "update_reload_operation", None)
    if not callable(updater):
        return False
    try:
        updated = updater(
            operation_id,
            state="restart_required",
            reason_code="reload_not_queued",
        )
        if inspect.isawaitable(updated):
            updated = await updated
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.error("[LearningApi] 自主学习 reload 未排队状态持久化失败")
        return False
    confirmed = (
        isinstance(updated, Mapping) and updated.get("state") == "restart_required"
    )
    if not confirmed:
        logger.error("[LearningApi] 自主学习 reload 未排队状态未确认")
    return confirmed


__all__ = ["schedule_learning_reload_operation"]
