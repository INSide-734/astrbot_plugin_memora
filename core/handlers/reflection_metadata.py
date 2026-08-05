"""反思窗口元数据的持久化、重试与低敏诊断。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from astrbot.api import logger

from ..monitoring import report_debug_event, report_debug_exception

if TYPE_CHECKING:
    from ..managers.conversation_manager import ConversationManager


async def _write_summary_metadata_once(
    conversation_manager: ConversationManager,
    *,
    session_id: str,
    end_index: int,
) -> bool:
    """原子提交总结游标并清理待重试状态。

    Args:
        conversation_manager: 提供真实 ``bool`` 持久化结果的会话管理器。
        session_id: 统一会话标识。
        end_index: 已完成窗口的结束索引（不包含）。

    Returns:
        两个字段通过同一次数据库提交写入时返回 ``True``；事务未完整
        提交时返回 ``False``，不会留下游标与 pending 的部分状态。

    Raises:
        asyncio.CancelledError: 调用方取消写入时原样传播。
        Exception: 会话管理器抛出的其他异常交给上层统一降级和重试。
    """

    return (
        await conversation_manager.update_session_metadata_fields(
            session_id,
            {
                "last_summarized_index": end_index,
                "pending_summary": None,
            },
        )
        is True
    )


async def commit_summary_metadata(
    conversation_manager: ConversationManager,
    *,
    session_id: str,
    end_index: int,
    record_pending_summary: Callable[[], Awaitable[bool]],
) -> bool:
    """提交反思窗口元数据，失败时重试一次并保留恢复窗口。

    Args:
        conversation_manager: 当前反思处理器使用的会话管理器。
        session_id: 统一会话标识。
        end_index: 已安全处理窗口的结束索引（不包含）。
        record_pending_summary: 两次提交都失败后写入恢复窗口的异步回调。

    Returns:
        初次提交或补救提交完整成功时返回 ``True``；两次均未完成时返回
        ``False``，调用方必须停止 continuity 与 backlog 成功路径。

    Raises:
        asyncio.CancelledError: 任一持久化步骤被取消时原样传播。
    """

    metadata_started = time.perf_counter()
    for attempt in range(2):
        try:
            if not await _write_summary_metadata_once(
                conversation_manager,
                session_id=session_id,
                end_index=end_index,
            ):
                raise RuntimeError("总结元数据原子提交未完成")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if attempt == 0:
                report_debug_exception(
                    "storage_task",
                    error,
                    component="reflection",
                    stage="metadata_commit",
                    status="degraded",
                    reason_code="summary_metadata_retrying",
                    task_type="storage",
                )
                logger.error(
                    f"[{session_id}] 记忆已存储但元数据更新失败：{error}。"
                    "将重试元数据提交并保留待重试窗口。",
                    exc_info=True,
                )
                continue

            report_debug_event(
                "storage_task",
                component="reflection",
                stage="metadata_commit",
                status="failed",
                reason_code="summary_metadata_failed",
                task_type="storage",
                duration_ms=max(
                    0.0,
                    (time.perf_counter() - metadata_started) * 1000.0,
                ),
            )
            logger.error(
                f"[{session_id}] 重试元数据更新仍然失败，已尝试记录待重试窗口。",
                exc_info=True,
            )
            await record_pending_summary()
            return False

        logger.info(
            f"[{session_id}] 更新滑动窗口位置：last_summarized_index = {end_index}"
        )
        report_debug_event(
            "storage_task",
            component="reflection",
            stage="metadata_commit",
            status="completed",
            reason_code="summary_metadata_committed",
            task_type="storage",
            retry_count=max(0, attempt),
            duration_ms=max(
                0.0,
                (time.perf_counter() - metadata_started) * 1000.0,
            ),
        )
        return True

    return False


async def persist_pending_summary(
    conversation_manager: ConversationManager | None,
    *,
    session_id: str,
    start_index: int,
    end_index: int,
    current_retry_count: int,
    failed_stage: str = "unknown",
    failed_count: int | None = None,
    completed_idempotency_keys: set[str] | list[str] | None = None,
) -> bool:
    """持久化待重试总结窗口，并只在真实提交后报告已记录。

    Args:
        conversation_manager: 当前会话管理器；未装配时直接返回失败。
        session_id: 统一会话标识。
        start_index: 失败窗口起始索引。
        end_index: 失败窗口结束索引（不包含）。
        current_retry_count: 当前已重试次数。
        failed_stage: 失败阶段标识。
        failed_count: 本次失败的候选数量。
        completed_idempotency_keys: 已成功写入、重试时应跳过的候选键。

    Returns:
        ``pending_summary`` 已提交时返回 ``True``；未装配管理器、返回
        非 ``True`` 或抛出普通异常时返回 ``False``。

    Raises:
        asyncio.CancelledError: 调用方取消持久化时原样传播。
    """

    if conversation_manager is None:
        return False

    new_retry_count = current_retry_count + 1
    pending_summary: dict[str, object] = {
        "start_index": start_index,
        "end_index": end_index,
        "retry_count": new_retry_count,
        "failed_stage": failed_stage,
    }
    if failed_count is not None:
        pending_summary["failed_count"] = failed_count
    if completed_idempotency_keys:
        pending_summary["completed_idempotency_keys"] = sorted(
            str(item) for item in completed_idempotency_keys
        )

    try:
        persisted = await conversation_manager.update_session_metadata(
            session_id,
            "pending_summary",
            pending_summary,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        report_debug_exception(
            "storage_task",
            error,
            component="reflection",
            stage="retry",
            status="failed",
            reason_code="summary_retry_record_failed",
            task_type="storage",
        )
        logger.error(
            f"[{session_id}] 待重试总结写入异常，无法确认恢复状态：{error}",
            exc_info=True,
        )
        return False

    if persisted is not True:
        report_debug_event(
            "storage_task",
            component="reflection",
            stage="retry",
            status="failed",
            reason_code="summary_retry_record_failed",
            task_type="storage",
        )
        logger.error(f"[{session_id}] 待重试总结未持久化，无法确认恢复状态")
        return False

    report_debug_event(
        "storage_task",
        component="reflection",
        stage="retry",
        status="waiting",
        reason_code="summary_retry_recorded",
        task_type="storage",
        retry_count=max(0, int(new_retry_count)),
        failed_count=max(0, int(failed_count or 0)),
    )
    logger.warning(
        f"[{session_id}] 记录待重试总结：范围=[{start_index}:{end_index}]，"
        f"重试次数={new_retry_count}/3"
    )
    return True


__all__ = ["commit_summary_metadata", "persist_pending_summary"]
