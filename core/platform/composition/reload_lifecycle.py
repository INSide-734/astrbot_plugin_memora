"""平台组合根的插件延迟重载及自主学习 operation 回调编排。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from typing import Any

from astrbot.api import logger

_PLUGIN_NAME = "astrbot_plugin_memora"


def supports_learning_reload_callback(plugin: object) -> bool:
    """判断当前已发布运行时是否能持久化自主学习 reload 回调。"""

    manager = _learning_manager(plugin)
    return callable(getattr(manager, "update_reload_operation", None))


def schedule_learning_reload(plugin: object, operation_id: str) -> bool:
    """读取当前 revision 并安排自主学习配置的延迟重载。"""

    if not supports_learning_reload_callback(plugin):
        return False
    manager = _learning_manager(plugin)
    revision = getattr(manager, "_state_revision", None)
    expected_state_revision = revision if isinstance(revision, str) else None
    scheduler = getattr(plugin, "_schedule_plugin_reload", None)
    if not callable(scheduler):
        return False
    return bool(
        scheduler(
            "auto_learning",
            learning_operation_id=operation_id,
            expected_state_revision=expected_state_revision,
        )
    )


async def run_scheduled_plugin_reload(
    plugin: object,
    reload_plugin: object,
    *,
    reason: str,
    backup_operation_id: str | None = None,
    learning_operation_id: str | None = None,
    expected_state_revision: str | None = None,
) -> None:
    """延迟调用宿主重载，并按真实执行阶段持久化低敏状态。

    Args:
        plugin: 持有 initializer、备份管理器和关停标记的插件实例。
        reload_plugin: AstrBot 提供的单插件异步重载入口。
        reason: 仅用于低敏日志分类的稳定调度原因。
        backup_operation_id: 可选的备份恢复 operation ID。
        learning_operation_id: 可选的自主学习 operation ID。

    Raises:
        asyncio.CancelledError: 延迟或宿主调用被取消时，保存保守状态后继续传播。
        Exception: 宿主重载异常在记录失败状态后继续交给既有 task 回调处理。
    """

    try:
        await asyncio.sleep(0.5)
        if bool(getattr(plugin, "_terminating", False)):
            await _mark_learning_reload(
                plugin,
                learning_operation_id,
                state="restart_required",
                reason_code="plugin_terminating",
                expected_state_revision=expected_state_revision,
            )
            logger.debug("插件正在停止，跳过延迟重载")
            return
        if learning_operation_id is not None:
            started = await _mark_learning_reload(
                plugin,
                learning_operation_id,
                state="running",
                reason_code="reload_started",
                expected_state_revision=expected_state_revision,
            )
            if not started:
                await _mark_learning_reload(
                    plugin,
                    learning_operation_id,
                    state="restart_required",
                    reason_code="reload_not_executed",
                    expected_state_revision=expected_state_revision,
                )
                logger.warning(
                    "自主学习重载未执行 reason=%s operation_id=%s",
                    reason,
                    learning_operation_id,
                )
                return
        result = await reload_plugin(_PLUGIN_NAME)  # type: ignore[operator]
    except asyncio.CancelledError:
        await _mark_learning_reload(
            plugin,
            learning_operation_id,
            state="restart_required",
            reason_code="reload_cancelled",
            expected_state_revision=expected_state_revision,
        )
        raise
    except Exception:
        await _mark_learning_reload(
            plugin,
            learning_operation_id,
            state="failed",
            reason_code="host_reload_failed",
            expected_state_revision=expected_state_revision,
        )
        raise

    if not _reload_failed(result):
        return
    logger.warning("插件重载返回失败 reason=%s", reason)
    if backup_operation_id:
        marker = getattr(
            getattr(plugin, "_backup_manager", None),
            "mark_reload_scheduled",
            None,
        )
        if callable(marker):
            marker(backup_operation_id, False)
    await _mark_learning_reload(
        plugin,
        learning_operation_id,
        state="failed",
        reason_code="host_reload_failed",
        expected_state_revision=expected_state_revision,
    )


def _learning_manager(plugin: object) -> object | None:
    """从已发布的 MemoryEngine 获取唯一 AutoLearningManager。"""

    initializer = getattr(plugin, "initializer", None)
    memory_engine = getattr(initializer, "memory_engine", None)
    return getattr(memory_engine, "auto_learning", None)


async def _mark_learning_reload(
    plugin: object,
    operation_id: str | None,
    *,
    state: str,
    reason_code: str,
    expected_state_revision: str | None = None,
) -> bool:
    """调用 manager 回调并确认目标 operation 已持久化为预期状态。"""

    if operation_id is None:
        return False
    manager = _learning_manager(plugin)
    updater = getattr(manager, "update_reload_operation", None)
    if not callable(updater):
        return False
    try:
        kwargs = {"state": state, "reason_code": reason_code}
        if expected_state_revision is not None:
            kwargs["expected_state_revision"] = expected_state_revision
        result = updater(operation_id, **kwargs)
        if inspect.isawaitable(result):
            result = await result
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(
            "自主学习 reload 状态持久化失败 state=%s type=%s",
            state,
            type(exc).__name__,
        )
        return False
    return (
        isinstance(result, Mapping)
        and result.get("operation_id") == operation_id
        and result.get("state") == state
    )


def _reload_failed(result: Any) -> bool:
    """统一解释 AstrBot 布尔或元组形态的重载结果。"""

    if result is False:
        return True
    if isinstance(result, tuple):
        return not result or not bool(result[0])
    return False


__all__ = [
    "run_scheduled_plugin_reload",
    "schedule_learning_reload",
    "supports_learning_reload_callback",
]
