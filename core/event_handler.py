"""
事件处理器
负责处理AstrBot事件钩子，委托子模块执行具体逻辑
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.api.platform import MessageType

from .base.config_manager import ConfigManager
from .cleaners.injection_cleaner import InjectionCleaner
from .dedup.dedup_manager import DedupManager
from .extractors.message_content_extractor import MessageContentExtractor
from .handlers.recall_handler import RecallHandler
from .handlers.reflection_handler import ReflectionHandler
from .identity.models import IdentityTrust, ResolvedIdentity
from .identity.runtime import ProtocolIdentityRuntime
from .managers.conversation_manager import ConversationManager
from .managers.memory_engine import MemoryEngine
from .monitoring import (
    debug_operation,
    monitored,
    report_debug_event,
    report_debug_exception,
)
from .processors.memory_processor import MemoryProcessor
from .utils.injection_adapter import InjectionAdapter

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.provider import LLMResponse, ProviderRequest

    from .injection.recorder import InjectionDecisionRecorder


class EventHandler:
    """事件处理器 — 协调各子模块处理 AstrBot 事件"""

    def __init__(
        self,
        context: Any,
        config_manager: ConfigManager,
        memory_engine: MemoryEngine,
        memory_processor: MemoryProcessor,
        conversation_manager: ConversationManager,
        jargon_filter: Any | None = None,
        jargon_miner: Any | None = None,
        jargon_query_service: Any | None = None,
        affection_manager: Any | None = None,
        expression_learner: Any | None = None,
        relation_manager: Any | None = None,
        prompt_protection_service: Any | None = None,
        write_guard_cb: Any | None = None,
        perf_tracker: Any | None = None,
        injection_recorder: InjectionDecisionRecorder | None = None,
        memory_tool_available: bool = False,
        memory_evolution_manager: Any | None = None,
    ) -> None:
        """绑定事件主链依赖，并复用会话管理器持有的协议身份运行时。"""

        self.context = context
        self.config_manager = config_manager
        self.memory_engine = memory_engine
        self.memory_processor = memory_processor
        self.conversation_manager = conversation_manager
        self._jargon_filter = jargon_filter
        self._jargon_miner = jargon_miner
        self._expression_learner = expression_learner
        self._relation_manager = relation_manager
        self._write_guard_cb = write_guard_cb
        self._injection_recorder = injection_recorder
        self._memory_tool_available = memory_tool_available
        self._memory_evolution_manager = memory_evolution_manager
        identity_runtime = getattr(conversation_manager, "identity_runtime", None)
        self._identity_runtime = (
            identity_runtime
            if isinstance(identity_runtime, ProtocolIdentityRuntime)
            else ProtocolIdentityRuntime()
        )

        self._dedup = DedupManager(max_size=1000, ttl=300)
        self._extractor = MessageContentExtractor()
        self._cleaner = InjectionCleaner()
        self._injection_adapter = InjectionAdapter()
        self._maintenance_tasks: set[asyncio.Task] = set()

        self._recall_handler = RecallHandler(
            context=context,
            config_manager=config_manager,
            memory_engine=memory_engine,
            conversation_manager=conversation_manager,
            injection_adapter=self._injection_adapter,
            enforce_limit_cb=self._enforce_message_limit,
            jargon_query_service=jargon_query_service,
            expression_learner=expression_learner,
            affection_manager=affection_manager,
            relation_manager=relation_manager,
            prompt_protection_service=prompt_protection_service,
            perf_tracker=perf_tracker,
            injection_recorder=injection_recorder,
            memory_tool_available=memory_tool_available,
            identity_enricher=self._identity_runtime.enricher,
        )
        self._reflection_handler = ReflectionHandler(
            context=context,
            config_manager=config_manager,
            memory_engine=memory_engine,
            memory_processor=memory_processor,
            conversation_manager=conversation_manager,
            enforce_limit_cb=self._enforce_message_limit,
            affection_manager=affection_manager,
            expression_learner=expression_learner,
            jargon_miner=jargon_miner,
            relation_manager=relation_manager,
            prompt_protection_service=prompt_protection_service,
            write_guard_cb=write_guard_cb,
            memory_evolution_manager=memory_evolution_manager,
        )

    @property
    def summary_window_locker(self) -> ReflectionHandler:
        """反思流程与命令流程共用的会话级总结提交锁。"""
        return self._reflection_handler

    # ---- 公开事件处理方法 ----

    @monitored
    async def handle_all_group_messages(self, event: AstrMessageEvent) -> None:
        """捕获全部群聊消息并写入记忆存储链路。"""
        with debug_operation():
            await self._handle_all_group_messages(event)

    async def _handle_all_group_messages(self, event: AstrMessageEvent) -> None:
        """捕获全部群聊消息并写入记忆存储链路。"""
        if not self.config_manager.get(
            "session_manager.enable_full_group_capture", True
        ):
            report_debug_event(
                "message_capture",
                component="event_handler",
                stage="capture",
                status="skipped",
                reason_code="capture_disabled",
            )
            return

        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            report_debug_event(
                "message_capture",
                component="event_handler",
                stage="capture",
                status="skipped",
                reason_code="not_group_message",
            )
            return

        if event.get_sender_id() == event.get_self_id():
            report_debug_event(
                "message_capture",
                component="event_handler",
                stage="capture",
                status="skipped",
                reason_code="self_message",
            )
            return

        try:
            session_id = event.unified_msg_origin
            writes_blocked = self._writes_blocked()
            identity = await self._identity_runtime.prepare(
                event,
                writes_blocked=writes_blocked,
            )

            if identity.trust_status in {
                IdentityTrust.CONFLICT,
                IdentityTrust.INVALID,
            }:
                report_debug_event(
                    "message_capture",
                    component="event_handler",
                    stage="capture",
                    status="skipped",
                    reason_code="identity_untrusted",
                )
                return

            if session_id and (
                "Error:" in session_id or "error:" in session_id.lower()
            ):
                logger.warning("检测到异常会话标识，跳过输出具体标识")

            content = await self._extractor.extract_message_content(event)
            dedup_key = await self._dedup.build_dedup_key(
                event,
                session_id,
                content,
                sender_id_override=self._conversation_sender_override(identity),
            )

            if dedup_key and await self._dedup.is_duplicate(dedup_key):
                report_debug_event(
                    "message_capture",
                    component="event_handler",
                    stage="capture",
                    status="skipped",
                    reason_code="duplicate",
                )
                logger.debug("检测到重复群消息，已跳过")
                return

            if writes_blocked:
                report_debug_event(
                    "message_capture",
                    component="event_handler",
                    stage="capture",
                    status="skipped",
                    reason_code="write_blocked",
                )
                logger.warning("写保护已启用，跳过群聊消息写入")
                return

            await self.conversation_manager.add_message_from_event(
                event=event,
                role="user",
                content=content,
                identity=identity,
            )
            await self._feed_cognitive_components(event, content, identity)
            if dedup_key:
                await self._dedup.mark_processed(dedup_key)

            # 会唤醒 Bot 的消息仍在 on_llm_response 后检查，避免响应生成前
            # 提前冻结窗口；只有不会产生回复的环境消息在捕获阶段主动触发。
            if not bool(getattr(event, "is_at_or_wake_command", False)):
                await self._reflection_handler.maybe_schedule_summary(event)

            self._create_maintenance_task(
                self._enforce_message_limit(session_id),
                name="message-limit-cleanup",
            )
            report_debug_event(
                "message_capture",
                component="event_handler",
                stage="capture",
                status="completed",
                count=1,
            )

        except asyncio.CancelledError:
            report_debug_event(
                "message_capture",
                component="event_handler",
                stage="capture",
                status="cancelled",
                reason_code="capture_cancelled",
            )
            raise
        except Exception as exception:
            report_debug_exception(
                "message_capture",
                exception,
                component="event_handler",
                stage="capture",
                status="failed",
                reason_code="capture_error",
            )
            logger.error("处理群聊全量消息时发生错误", exc_info=True)

    async def _feed_cognitive_components(
        self,
        event: AstrMessageEvent,
        content: str,
        identity: ResolvedIdentity,
    ) -> None:
        """尽力向可选的 v1.0+ 认知模块投喂输入数据。"""
        group_id = event.unified_msg_origin or "default"
        sender_id = self._user_id_for_identity(event, identity)
        if sender_id is None:
            return
        try:
            if self._jargon_filter is not None:
                self._jargon_filter.update(content, group_id, sender_id)
        except Exception:
            logger.debug("[认知模块] 黑话过滤器更新失败", exc_info=True)

        try:
            if self._expression_learner is not None:
                self._expression_learner.buffer_message(
                    group_id=group_id,
                    sender_id=sender_id,
                    content=content,
                )
                await self._expression_learner.maybe_learn(group_id)
        except Exception:
            logger.debug("[认知模块] 表达模式学习器更新失败", exc_info=True)

        try:
            if self._relation_manager is not None:
                await self._relation_manager.apply_delta(
                    from_user=sender_id,
                    to_user="bot",
                    group_id=group_id,
                    delta=0.01,
                    reason="group_message",
                )
        except Exception:
            logger.debug("[认知模块] 社交关系更新失败", exc_info=True)

        try:
            if self._jargon_miner is not None:
                stats = (
                    self._jargon_filter.get_stats(group_id)
                    if self._jargon_filter is not None
                    else None
                )
                if stats and stats.candidate_count > 0:
                    await self._jargon_miner.run_once(group_id, limit=2)
        except Exception:
            logger.debug("[认知模块] 黑话挖掘执行失败", exc_info=True)

    @monitored
    async def handle_memory_recall(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        """在 LLM 请求前检索并注入长期记忆。"""
        with debug_operation():
            identity = await self._identity_runtime.prepare(
                event,
                writes_blocked=self._writes_blocked(),
            )
            await self._recall_handler.handle_memory_recall(
                event,
                req,
                identity=identity,
            )

    @monitored
    async def handle_memory_reflection(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
    ) -> None:
        """在 LLM 响应后判断是否需要反思与存储记忆。"""
        with debug_operation():
            identity = await self._identity_runtime.prepare(
                event,
                writes_blocked=self._writes_blocked(),
            )
            await self._reflection_handler.handle_memory_reflection(
                event,
                resp,
                identity=identity,
            )

    async def handle_session_reset(self, event: AstrMessageEvent) -> None:
        """处理 /reset 或 /new 触发的会话清空"""
        session_id = event.unified_msg_origin
        if not session_id:
            return
        try:
            await self.conversation_manager.clear_session(session_id)
            logger.info(f"[{session_id}] 已同步清空插件会话上下文（/reset 或 /new）")
        except Exception as e:
            logger.error(f"[{session_id}] 清空插件会话上下文失败: {e}", exc_info=True)

    async def shutdown(self) -> None:
        """关闭事件处理器，等待所有存储任务完成"""
        shutdown_started = time.perf_counter()
        queue_depth = len(self._maintenance_tasks)
        report_debug_event(
            "shutdown_step",
            component="event_handler",
            stage="event_handler",
            status="started",
            reason_code="event_handler_shutdown_started",
            queue_depth=queue_depth,
        )
        try:
            await self._reflection_handler.shutdown()
            if self._maintenance_tasks:
                try:
                    _, pending = await asyncio.wait(
                        list(self._maintenance_tasks),
                        timeout=3.0,
                    )
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                finally:
                    self._maintenance_tasks.clear()
            await self._identity_runtime.close()
            if self._injection_recorder is not None:
                await self._injection_recorder.close(timeout=5.0)
        except asyncio.CancelledError:
            report_debug_event(
                "shutdown_step",
                component="event_handler",
                stage="event_handler",
                status="cancelled",
                reason_code="event_handler_shutdown_cancelled",
                duration_ms=max(0.0, (time.perf_counter() - shutdown_started) * 1000.0),
                queue_depth=len(self._maintenance_tasks),
            )
            raise
        except Exception as exception:
            report_debug_exception(
                "shutdown_step",
                exception,
                component="event_handler",
                stage="event_handler",
                status="failed",
                reason_code="event_handler_shutdown_error",
                duration_ms=max(0.0, (time.perf_counter() - shutdown_started) * 1000.0),
                queue_depth=len(self._maintenance_tasks),
            )
            raise

        logger.info("EventHandler 已关闭")
        report_debug_event(
            "shutdown_step",
            component="event_handler",
            stage="event_handler",
            status="completed",
            reason_code="event_handler_closed",
            duration_ms=max(0.0, (time.perf_counter() - shutdown_started) * 1000.0),
            queue_depth=0,
            count=queue_depth,
        )

    # ---- 内部方法 ----

    def _create_maintenance_task(self, coro, *, name: str) -> asyncio.Task:
        """登记短生命周期维护任务，确保关闭阶段能够感知其失败。"""
        task = asyncio.create_task(coro, name=name)
        self._maintenance_tasks.add(task)
        task.add_done_callback(self._on_maintenance_task_done)
        return task

    def _on_maintenance_task_done(self, task: asyncio.Task) -> None:
        self._maintenance_tasks.discard(task)
        if task.cancelled():
            report_debug_event(
                "maintenance_task",
                component="maintenance",
                stage="maintenance",
                status="cancelled",
                task_type="other",
                reason_code="task_cancelled",
            )
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            report_debug_event(
                "maintenance_task",
                component="maintenance",
                stage="maintenance",
                status="cancelled",
                task_type="other",
                reason_code="task_cancelled",
            )
            return
        if exc is not None:
            report_debug_exception(
                "maintenance_task",
                exc,
                component="maintenance",
                stage="maintenance",
                status="failed",
                task_type="other",
                reason_code="task_error",
            )
            logger.error("[事件处理器] 维护任务执行失败", exc_info=exc)
        else:
            report_debug_event(
                "maintenance_task",
                component="maintenance",
                stage="maintenance",
                status="completed",
                task_type="other",
            )

    async def _enforce_message_limit(self, session_id: str) -> None:
        """执行消息数量上限控制，只删除已被总结的消息"""
        if not self.conversation_manager:
            return

        max_messages = self.config_manager.get(
            "session_manager.max_messages_per_session", 1000
        )
        cleanup_batch_size = self.config_manager.get(
            "session_manager.cleanup_batch_size", 50
        )
        try:
            cleanup_batch_size = int(cleanup_batch_size)
        except (TypeError, ValueError):
            cleanup_batch_size = 50
        cleanup_batch_size = max(1, cleanup_batch_size)

        if (
            not self.conversation_manager.store
            or not self.conversation_manager.store.connection
        ):
            return

        try:
            actual_count = await self.conversation_manager.store.get_message_count(
                session_id
            )

            if actual_count <= max_messages:
                return

            last_summarized_index = (
                await self.conversation_manager.get_session_metadata(
                    session_id,
                    "last_summarized_index",
                    0,
                )
            )

            overflow_count = actual_count - max_messages
            target_delete = max(overflow_count, cleanup_batch_size)
            safe_to_delete = min(target_delete, last_summarized_index)

            if safe_to_delete <= 0:
                logger.debug(
                    f"[{session_id}] 无可删除消息: "
                    f"溢出={overflow_count}, 批量={cleanup_batch_size}, "
                    f"目标删除={target_delete}, 已总结={last_summarized_index}"
                )
                return

            logger.info(
                f"[{session_id}] 开始清理已总结消息: "
                f"总数={actual_count}, 上限={max_messages}, "
                f"溢出={overflow_count}, 批量={cleanup_batch_size}, "
                f"目标删除={target_delete}, 已总结={last_summarized_index}, "
                f"实际删除={safe_to_delete}"
            )

            actually_deleted = (
                await self.conversation_manager.store.trim_session_messages(
                    session_id,
                    safe_to_delete,
                )
            )

            new_actual_count = max(0, actual_count - actually_deleted)
            new_summarized_index = await self.conversation_manager.get_session_metadata(
                session_id,
                "last_summarized_index",
                max(0, last_summarized_index - actually_deleted),
            )

            await self.conversation_manager.invalidate_cache(session_id)

            logger.info(
                f"[{session_id}] 消息清理完成: "
                f"删除={actually_deleted}条, 剩余={new_actual_count}条, "
                f"总结索引: {last_summarized_index} -> {new_summarized_index}"
            )

        except Exception as e:
            logger.error(f"[{session_id}] 删除旧消息失败: {e}", exc_info=True)

    def _writes_blocked(self) -> bool:
        """读取写保护状态，检查失败时按禁止写入处理。"""

        if self._write_guard_cb is None:
            return False
        try:
            return bool(self._write_guard_cb())
        except Exception:
            logger.error("[EventHandler] 写入维护状态检查失败", exc_info=True)
            return True

    @staticmethod
    def _conversation_sender_override(identity: ResolvedIdentity) -> str | None:
        """返回可用于会话去重的可信或群内匿名发送者标识。"""

        if identity.trust_status is IdentityTrust.TRUSTED:
            return identity.canonical_user_id
        if identity.trust_status is IdentityTrust.ANONYMOUS:
            return identity.conversation_sender_id
        return None

    @staticmethod
    def _user_id_for_identity(
        event: AstrMessageEvent,
        identity: ResolvedIdentity,
    ) -> str | None:
        """选择用户级状态标识；未注册协议保留 AstrBot 原有行为。"""

        if identity.trust_status is IdentityTrust.TRUSTED:
            return identity.canonical_user_id
        if identity.trust_status is not IdentityTrust.UNSUPPORTED:
            return None
        getter = getattr(event, "get_sender_id", None)
        if not callable(getter):
            return None
        try:
            sender_id = getter()
        except Exception:
            return None
        if sender_id is None:
            return None
        normalized = str(sender_id).strip()
        return normalized or None
