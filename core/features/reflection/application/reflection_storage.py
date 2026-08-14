"""反思窗口、候选存储与演化调度辅助。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.api import logger

from ....shared.data_helpers import OperationContext
from ...observability.application import runtime as observability
from ..domain.storage_outcomes import (
    ReflectionStoreOutcome,
    ReflectionStoreResult,
    summarize_store_results,
)
from . import llm_budget as budget_ops
from . import reflection_metadata as metadata_ops
from .candidate_writer import (
    build_reflection_idempotency_key,
    store_reflection_candidates,
)


class ReflectionStorageMixin:
    """为 ReflectionHandler 提供窗口与后台写入实现。"""

    def _on_storage_task_done(self, task: asyncio.Task, session_id: str) -> None:
        """存储任务完成回调：回收任务状态并记录异常"""
        self._storage_tasks.discard(task)
        self.finish_summary_window(session_id)

        if task.cancelled():
            observability.report_debug_event(
                "storage_task",
                component="reflection",
                stage="storage",
                status="cancelled",
                reason_code="storage_cancelled",
                task_type="storage",
            )
            return

        try:
            exc = task.exception()
        except asyncio.CancelledError:
            observability.report_debug_event(
                "storage_task",
                component="reflection",
                stage="storage",
                status="cancelled",
                reason_code="storage_cancelled",
                task_type="storage",
            )
            return

        if exc:
            observability.report_debug_exception(
                "storage_task",
                exc,
                component="reflection",
                stage="storage",
                status="failed",
                reason_code="storage_error",
                task_type="storage",
            )
            logger.error(f"[{session_id}] 记忆存储任务异常退出: {exc}")
        else:
            observability.report_debug_event(
                "storage_task",
                component="reflection",
                stage="storage",
                status="completed",
                reason_code="storage_completed",
                task_type="storage",
            )

    async def try_begin_summary_window(self, session_id: str) -> bool:
        """为后台或手动提交预留会话总结窗口。"""
        if self._shutting_down:
            return False
        async with self._storage_state_lock:
            if session_id in self._storage_sessions_inflight:
                return False
            self._storage_sessions_inflight.add(session_id)
            return True

    def finish_summary_window(self, session_id: str) -> None:
        """释放会话总结窗口占用。"""
        self._storage_sessions_inflight.discard(session_id)

    async def _prepare_message_batches(
        self, history_messages: list, is_group_chat: bool
    ) -> list[list]:
        """通过 ``TopicBatchPreparer`` 准备消息批次。"""
        batches = await self._batch_preparer.prepare_batches(
            history_messages, is_group_chat
        )
        return budget_ops.fit_batches_to_extra_llm_budget(batches, self._cost_control)

    async def _storage_task(
        self,
        session_id: str,
        history_messages: list,
        persona_id: str | None,
        start_index: int,
        end_index: int,
        retry_count: int = 0,
    ) -> None:
        """后台存储任务"""
        storage_started = time.perf_counter()
        observability.report_debug_event(
            "storage_task",
            component="reflection",
            stage="storage",
            status="started",
            reason_code="storage_started",
            task_type="storage",
            message_count=len(history_messages),
            retry_count=max(0, int(retry_count)),
        )
        async with OperationContext("记忆存储", session_id):
            try:
                current_summarized = (
                    await self._conversation_manager.get_session_metadata(
                        session_id,
                        "last_summarized_index",
                        0,
                    )
                )
                try:
                    summarized_index = int(current_summarized)
                except (TypeError, ValueError):
                    summarized_index = 0
                pending_summary = (
                    await self._conversation_manager.get_session_metadata(
                        session_id,
                        "pending_summary",
                        None,
                    )
                    if self._conversation_manager
                    else None
                )
                completed_idempotency_keys: set[str] = set()
                if isinstance(pending_summary, dict):
                    completed_idempotency_keys = {
                        str(item)
                        for item in (
                            pending_summary.get("completed_idempotency_keys") or []
                        )
                    }

                if summarized_index >= end_index:
                    observability.report_debug_event(
                        "storage_task",
                        component="reflection",
                        stage="window_check",
                        status="skipped",
                        reason_code="stale_summary_task",
                        task_type="storage",
                    )
                    logger.info(
                        f"[{session_id}] 检测到过期总结任务，跳过："
                        f"current={summarized_index}, target_end={end_index}"
                    )
                    return

                is_group_chat = bool(
                    history_messages[0].group_id if history_messages else False
                )
                if not is_group_chat and "GroupMessage" in session_id:
                    is_group_chat = True
                group_id: str | None = None
                if history_messages:
                    first_group_id = getattr(history_messages[0], "group_id", None)
                    if first_group_id:
                        group_id = str(first_group_id)

                logger.info(
                    f"[{session_id}] 开始处理记忆，类型={'群聊' if is_group_chat else '私聊'}, "
                    f"范围=[{start_index}:{end_index}], 重试次数={retry_count}, "
                    f"当前人格={persona_id or '未设置'}"
                )

                if not self._memory_processor:
                    observability.report_debug_event(
                        "storage_task",
                        component="reflection",
                        stage="memory_extract",
                        status="failed",
                        reason_code="memory_processor_unavailable",
                        task_type="storage",
                        retry_count=max(0, int(retry_count)),
                    )
                    logger.error(f"[{session_id}] 记忆处理器未初始化，记录待重试")
                    await self._record_pending_summary(
                        session_id,
                        start_index,
                        end_index,
                        retry_count,
                    )
                    return

                try:
                    # 准备消息批次（A/B 策略单批次，C/D 策略多批次）
                    batch_started = time.perf_counter()
                    batches = await self._prepare_message_batches(
                        history_messages, is_group_chat
                    )
                    observability.report_debug_event(
                        "storage_task",
                        component="reflection",
                        stage="batch_prepare",
                        status="completed",
                        reason_code="batches_prepared",
                        task_type="storage",
                        duration_ms=max(
                            0.0, (time.perf_counter() - batch_started) * 1000.0
                        ),
                        message_count=len(history_messages),
                        batch_count=len(batches),
                    )
                    logger.info(
                        f"[{session_id}] 调用记忆处理器，"
                        f"{len(history_messages)} 条消息 → {len(batches)} 个批次"
                    )

                    all_memories: list[dict[str, Any]] = []
                    batch_processing_failed = False
                    failed_batch_count = 0
                    extraction_started = time.perf_counter()
                    batch_results = await budget_ops.process_reflection_batches(
                        batches,
                        process_conversation=(
                            self._memory_processor.process_conversation
                        ),
                        cost_control=self._cost_control,
                        is_group_chat=is_group_chat,
                        persona_id=persona_id,
                        group_id=group_id,
                    )
                    for i, result in enumerate(batch_results):
                        if isinstance(result, BaseException):
                            batch_processing_failed = True
                            failed_batch_count += 1
                            logger.error(
                                "反思批次 %d/%d LLM 处理失败，异常类型=%s",
                                i + 1,
                                len(batches),
                                result.__class__.__name__,
                            )
                        else:
                            all_memories.extend(result)

                    if batch_processing_failed:
                        observability.report_debug_event(
                            "storage_task",
                            component="reflection",
                            stage="memory_extract",
                            status="failed",
                            reason_code="batch_extraction_failed",
                            task_type="storage",
                            duration_ms=max(
                                0.0, (time.perf_counter() - extraction_started) * 1000.0
                            ),
                            batch_count=len(batches),
                            failed_count=failed_batch_count,
                            success_count=max(0, len(batches) - failed_batch_count),
                        )
                        await self._record_pending_summary(
                            session_id,
                            start_index,
                            end_index,
                            retry_count,
                            failed_stage="llm_batch",
                        )
                        return

                    memories = all_memories
                    observability.report_debug_event(
                        "storage_task",
                        component="reflection",
                        stage="memory_extract",
                        status="completed",
                        reason_code="memories_extracted",
                        task_type="storage",
                        duration_ms=max(
                            0.0, (time.perf_counter() - extraction_started) * 1000.0
                        ),
                        batch_count=len(batches),
                        count=len(memories),
                    )
                    for memory_index, mem in enumerate(memories):
                        metadata = mem.setdefault("metadata", {})
                        key = self._memory_idempotency_key(
                            session_id=session_id,
                            start_index=start_index,
                            end_index=end_index,
                            batch_index=int(metadata.get("batch_index", 0) or 0),
                            memory_index=memory_index,
                            content=str(mem.get("content", "") or ""),
                        )
                        metadata["idempotency_key"] = key
                    logger.info(
                        f"[{session_id}] LLM 生成 {len(memories)} 条独立记忆"
                        f"（来自 {len(batches)} 个批次）"
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    observability.report_debug_exception(
                        "storage_task",
                        e,
                        component="reflection",
                        stage="memory_extract",
                        status="failed",
                        reason_code="memory_extraction_error",
                        task_type="storage",
                        retry_count=max(0, int(retry_count)),
                    )
                    logger.error(
                        "反思 LLM 处理失败（重试 %d/3），异常类型=%s",
                        retry_count + 1,
                        e.__class__.__name__,
                        exc_info=True,
                    )
                    await self._record_pending_summary(
                        session_id,
                        start_index,
                        end_index,
                        retry_count,
                    )
                    return

                if self._memory_engine:
                    write_started = time.perf_counter()
                    observability.report_debug_event(
                        "storage_task",
                        component="reflection",
                        stage="memory_write",
                        status="started",
                        reason_code="memory_write_started",
                        task_type="storage",
                        count=len(memories),
                    )

                    write_results = await store_reflection_candidates(
                        memories,
                        completed_idempotency_keys=completed_idempotency_keys,
                        session_id=session_id,
                        persona_id=persona_id,
                        start_index=start_index,
                        end_index=end_index,
                        is_group_chat=is_group_chat,
                        group_id=group_id,
                        memory_engine=self._memory_engine,
                        memory_quality_gate=self._memory_quality_gate,
                        schedule_evolution_after_write=(
                            self._schedule_evolution_after_write
                        ),
                    )
                    store_summary = summarize_store_results(write_results)
                    successful_keys = set(store_summary.completed_idempotency_keys)

                    logger.info(
                        "[%s] 反思候选处理完成：canonical=%d，quarantine=%d，"
                        "discard=%d，mark_write=%d，幂等跳过=%d，失败=%d（%d条消息）",
                        session_id,
                        store_summary.canonical_count,
                        store_summary.quarantine_count,
                        store_summary.discard_count,
                        store_summary.mark_write_count,
                        store_summary.skipped_idempotent_count,
                        store_summary.failed_count,
                        len(history_messages),
                    )
                    observability.report_debug_event(
                        "storage_task",
                        component="reflection",
                        stage="memory_write",
                        status="completed"
                        if store_summary.failed_count == 0
                        else "degraded",
                        reason_code="memory_write_completed"
                        if store_summary.failed_count == 0
                        else "memory_write_partial",
                        task_type="storage",
                        duration_ms=max(
                            0.0, (time.perf_counter() - write_started) * 1000.0
                        ),
                        success_count=store_summary.canonical_count,
                        canonical_count=store_summary.canonical_count,
                        quarantine_count=store_summary.quarantine_count,
                        failed_count=store_summary.failed_count,
                        skipped_count=store_summary.skipped_idempotent_count,
                        skipped_idempotent_count=(
                            store_summary.skipped_idempotent_count
                        ),
                    )
                else:
                    store_summary = summarize_store_results(
                        ReflectionStoreResult(ReflectionStoreOutcome.FAILED)
                        for _ in memories
                    )
                    successful_keys = set()
                    observability.report_debug_event(
                        "storage_task",
                        component="reflection",
                        stage="memory_write",
                        status="skipped",
                        reason_code="memory_engine_unavailable",
                        task_type="storage",
                        count=len(memories),
                    )

                if store_summary.failed_count > 0:
                    logger.warning(
                        f"[{session_id}] 有 {store_summary.failed_count} 条候选写入失败，"
                        f"保留待重试窗口：范围=[{start_index}:{end_index}]"
                    )
                    await self._record_pending_summary(
                        session_id,
                        start_index,
                        end_index,
                        retry_count,
                        failed_stage="memory_write",
                        failed_count=store_summary.failed_count,
                        completed_idempotency_keys=successful_keys,
                    )
                    return

                if self._conversation_manager:
                    metadata_committed = await metadata_ops.commit_summary_metadata(
                        self._conversation_manager,
                        session_id=session_id,
                        end_index=end_index,
                        record_pending_summary=lambda: self._record_pending_summary(
                            session_id,
                            start_index,
                            end_index,
                            retry_count,
                            failed_stage="metadata_commit",
                            failed_count=0,
                            completed_idempotency_keys=successful_keys,
                        ),
                    )
                    if not metadata_committed:
                        # 元数据未完成时不能把 canonical 写入误报为完整成功，
                        # 也不能让积压 drain 根据旧游标继续下一窗口。
                        return

                from . import reflection_handler

                reflection_handler.resolve_continuity_session(
                    self._memory_engine, session_id
                )

                observability.report_debug_event(
                    "storage_task",
                    component="reflection",
                    stage="storage",
                    status="completed",
                    reason_code="memories_stored",
                    task_type="storage",
                    count=store_summary.canonical_count,
                    canonical_count=store_summary.canonical_count,
                    quarantine_count=store_summary.quarantine_count,
                    failed_count=store_summary.failed_count,
                    skipped_idempotent_count=(store_summary.skipped_idempotent_count),
                    duration_ms=max(
                        0.0, (time.perf_counter() - storage_started) * 1000.0
                    ),
                )

            except asyncio.CancelledError:
                observability.report_debug_event(
                    "storage_task",
                    component="reflection",
                    stage="storage",
                    status="cancelled",
                    reason_code="storage_cancelled",
                    task_type="storage",
                    duration_ms=max(
                        0.0, (time.perf_counter() - storage_started) * 1000.0
                    ),
                )
                raise
            except Exception as e:
                observability.report_debug_exception(
                    "storage_task",
                    e,
                    component="reflection",
                    stage="storage",
                    status="failed",
                    reason_code="storage_error",
                    task_type="storage",
                    duration_ms=max(
                        0.0, (time.perf_counter() - storage_started) * 1000.0
                    ),
                )
                logger.error(f"[{session_id}] 存储记忆失败：{e}", exc_info=True)
                await self._record_pending_summary(
                    session_id,
                    start_index,
                    end_index,
                    retry_count,
                )

    async def _record_pending_summary(
        self,
        session_id: str,
        start_index: int,
        end_index: int,
        current_retry_count: int,
        failed_stage: str = "unknown",
        failed_count: int | None = None,
        completed_idempotency_keys: set[str] | list[str] | None = None,
    ) -> bool:
        """委托共享 helper 持久化待重试总结窗口。

        Args:
            session_id: 统一会话标识。
            start_index: 失败窗口起始索引。
            end_index: 失败窗口结束索引（不包含）。
            current_retry_count: 当前已重试次数。
            failed_stage: 失败阶段标识。
            failed_count: 本次失败的候选数量。
            completed_idempotency_keys: 已成功写入、重试时应跳过的候选键。

        Returns:
            ``True`` 表示待重试状态已提交；没有会话管理器或提交失败时返回
            ``False``，且不会发出“已记录”诊断事件。

        Raises:
            asyncio.CancelledError: 调用方取消持久化时原样传播。
        """
        return await metadata_ops.persist_pending_summary(
            self._conversation_manager,
            session_id=session_id,
            start_index=start_index,
            end_index=end_index,
            current_retry_count=current_retry_count,
            failed_stage=failed_stage,
            failed_count=failed_count,
            completed_idempotency_keys=completed_idempotency_keys,
        )

    async def _schedule_evolution_after_write(self, memory_id: int) -> None:
        """从 canonical Store 重读 source 后再通知记忆演化管理器。"""

        manager = self._memory_evolution_manager
        if manager is None or getattr(manager, "mode", None) == "disabled":
            observability.report_debug_event(
                "storage_task",
                component="reflection",
                stage="evolution_schedule",
                status="skipped",
                reason_code="evolution_disabled",
                task_type="evolution",
            )
            return
        try:
            sources = await manager.store.load_sources((int(memory_id),))
            if sources:
                decision = await manager.schedule_consider(sources[0])
                should_enqueue = getattr(decision, "should_enqueue", False) is True
                observability.report_debug_event(
                    "storage_task",
                    component="reflection",
                    stage="evolution_schedule",
                    status="completed" if should_enqueue else "skipped",
                    reason_code=(
                        "evolution_scheduled" if should_enqueue else "evolution_skipped"
                    ),
                    task_type="evolution",
                    count=1 if should_enqueue else 0,
                )
            else:
                observability.report_debug_event(
                    "storage_task",
                    component="reflection",
                    stage="evolution_schedule",
                    status="skipped",
                    reason_code="evolution_source_missing",
                    task_type="evolution",
                )
        except asyncio.CancelledError:
            observability.report_debug_event(
                "storage_task",
                component="reflection",
                stage="storage",
                status="cancelled",
                reason_code="evolution_cancelled",
                task_type="evolution",
            )
            raise
        except Exception as exception:
            observability.report_debug_exception(
                "storage_task",
                exception,
                component="reflection",
                stage="storage",
                status="failed",
                reason_code="evolution_schedule_error",
                task_type="evolution",
            )
            logger.warning("canonical 写入成功，但记忆演化任务调度失败")

    @staticmethod
    def _memory_idempotency_key(
        *,
        session_id: str,
        start_index: int,
        end_index: int,
        batch_index: int,
        memory_index: int,
        content: str,
    ) -> str:
        """兼容现有调用方，委托共享候选幂等键实现。"""

        return build_reflection_idempotency_key(
            session_id=session_id,
            start_index=start_index,
            end_index=end_index,
            batch_index=batch_index,
            memory_index=memory_index,
            content=content,
        )

    _config_manager: Any
    _conversation_manager: Any
    _memory_engine: Any
    _memory_processor: Any
    _batch_preparer: Any
    _cost_control: Any
    _memory_quality_gate: Any
    _memory_evolution_manager: Any
    _storage_tasks: set[asyncio.Task]
    _storage_sessions_inflight: set[str]
    _storage_state_lock: asyncio.Lock
    _shutting_down: bool
