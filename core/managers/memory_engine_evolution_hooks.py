"""MemoryEngine canonical 提交后的 Evolution 调度与失效钩子。"""

from __future__ import annotations

import asyncio

from astrbot.api import logger

from ..features.memory.domain.revision import memory_revision
from ..features.observability.application.memory_write_timing import (
    measure_memory_write_stage,
)
from ..features.observability.infrastructure.debug_reporter import (
    report_debug_event,
    report_debug_exception,
)
from .write_coordinator import write_with_retry


class MemoryEngineEvolutionHooksMixin:
    """为 MemoryEngine 提供 canonical 提交后的派生维护边界。"""

    async def _invalidate_evolution_after_revision(self, memory_id: int) -> None:
        """提交 canonical 元数据更新后，让旧 revision 派生对象失效。"""

        store = getattr(self, "memory_evolution_store", None)
        if store is None:
            return
        try:
            sources = await store.load_sources((int(memory_id),))
            if not sources:
                return
            await write_with_retry(
                lambda: store.invalidate_for_source_revision(
                    int(memory_id),
                    sources[0].revision_token,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                f"[更新] 派生 revision 失效标记失败 (memory_id={memory_id})",
                exc_info=True,
            )

    async def _schedule_evolution_after_write(self, memory_id: int) -> None:
        """canonical 提交后重新读取 source，并隔离演化调度失败。"""

        with measure_memory_write_stage("evolution"):
            manager = getattr(self, "memory_evolution_manager", None)
            if manager is None or getattr(manager, "mode", None) == "disabled":
                report_debug_event(
                    "storage_task",
                    component="memory_engine",
                    stage="evolution_schedule",
                    status="skipped",
                    reason_code="evolution_disabled",
                    task_type="evolution",
                )
                return
            try:
                sources = await manager.store.load_sources((int(memory_id),))
                if not sources:
                    report_debug_event(
                        "storage_task",
                        component="memory_engine",
                        stage="evolution_schedule",
                        status="skipped",
                        reason_code="evolution_source_missing",
                        task_type="evolution",
                    )
                    return
                decision = await write_with_retry(
                    lambda: manager.schedule_consider(sources[0])
                )
                should_enqueue = getattr(decision, "should_enqueue", False) is True
                report_debug_event(
                    "storage_task",
                    component="memory_engine",
                    stage="evolution_schedule",
                    status="completed" if should_enqueue else "skipped",
                    reason_code=(
                        "evolution_scheduled" if should_enqueue else "evolution_skipped"
                    ),
                    task_type="evolution",
                    count=1 if should_enqueue else 0,
                )
            except asyncio.CancelledError:
                report_debug_event(
                    "storage_task",
                    component="memory_engine",
                    stage="evolution_schedule",
                    status="cancelled",
                    reason_code="evolution_cancelled",
                    task_type="evolution",
                )
                raise
            except Exception as error:
                report_debug_exception(
                    "storage_task",
                    error,
                    component="memory_engine",
                    stage="evolution_schedule",
                    status="failed",
                    reason_code="evolution_schedule_error",
                    task_type="evolution",
                )
                # Evolution 是派生维护链；调度异常不能回滚已经提交的 canonical。
                logger.warning(
                    "[写入] canonical 已提交但演化调度失败，异常类型=%s",
                    error.__class__.__name__,
                )

    async def _invalidate_evolution_after_delete(self, memory_id: int) -> None:
        """canonical 删除提交后标记关联 relation/projection 不可见。"""

        store = getattr(self, "memory_evolution_store", None)
        if store is None:
            return
        try:
            await store.invalidate_for_deleted_source(int(memory_id))
        except asyncio.CancelledError:
            raise
        except Exception:
            # 派生维护失败只进入可恢复维护路径，不能回滚已提交的 canonical 删除。
            logger.warning(
                f"[删除] 派生 source 失效标记失败 (memory_id={memory_id})",
                exc_info=True,
            )


__all__ = ["MemoryEngineEvolutionHooksMixin", "memory_revision"]
