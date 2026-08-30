"""
事件处理器
负责处理AstrBot事件钩子，委托子模块执行具体逻辑
"""

from __future__ import annotations

import asyncio
import inspect
import time
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from astrbot.api import logger
from astrbot.api.platform import MessageType

from .event_cognitive import CognitiveComponentsMixin
from .features.conversation.application.conversation_manager import ConversationManager
from .features.conversation.application.dedup_manager import DedupManager
from .features.conversation.application.message_content_extractor import (
    MessageContentExtractor,
)
from .features.identity.domain.models import IdentityTrust, ResolvedIdentity
from .features.injection.application.injection_adapter import InjectionAdapter
from .features.memory.application.memory_engine import MemoryEngine
from .features.observability.application.runtime import monitored
from .features.observability.infrastructure.debug_reporter import (
    debug_operation,
    report_debug_event,
    report_debug_exception,
)
from .features.recall.application.injection_cleaner import InjectionCleaner
from .features.recall.application.recall_handler import RecallHandler
from .features.recall.application.recall_observability import RecallTimingContext
from .features.recall.processors.memory_processor import MemoryProcessor
from .features.reflection.application.reflection_handler import ReflectionHandler
from .platform.config.cost_control import build_cost_control_from_config
from .platform.config.manager import ConfigManager
from .shared.contracts import IdentityConversationPort
from .shared.cost_control import CostControl
from .shared.extra_llm_budget import (
    ExtraLlmBudget,
    ExtraLlmBudgetObservation,
    extra_llm_budget_scope,
)

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.provider import LLMResponse, ProviderRequest

    from .features.injection.infrastructure.recorder import InjectionDecisionRecorder
    from .shared.contracts import PromptProtectionPort


class EventHandler(CognitiveComponentsMixin):
    """事件处理器 — 协调各子模块处理 AstrBot 事件"""

    _IDENTITY_SYNC_MARKER_ATTR = "_memora_identity_sync_scheduled"

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
        prompt_protection_service: PromptProtectionPort | None = None,
        write_guard_cb: Any | None = None,
        perf_tracker: Any | None = None,
        injection_recorder: InjectionDecisionRecorder | None = None,
        memory_tool_available: bool = False,
        memory_evolution_manager: Any | None = None,
        memory_quality_gate: Any | None = None,
        identity_runtime: IdentityConversationPort | None = None,
        summary_scheduler: Any | None = None,
    ) -> None:
        """绑定事件主链依赖，并接收组合根发布的协议身份端口。"""

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
        configured_cost_control = getattr(memory_engine, "cost_control", None)
        cost_control_section = config_manager.get_section("cost_control")
        if not isinstance(cost_control_section, dict):
            cost_control_section = {}
        self._cost_control = (
            configured_cost_control
            if isinstance(configured_cost_control, CostControl)
            else build_cost_control_from_config(cost_control_section)
        )
        self._identity_runtime: IdentityConversationPort | None = (
            identity_runtime
            if isinstance(identity_runtime, IdentityConversationPort)
            else None
        )

        self._dedup = DedupManager(max_size=1000, ttl=300)
        self._extractor = MessageContentExtractor()
        self._cleaner = InjectionCleaner()
        self._injection_adapter = InjectionAdapter()
        self._maintenance_tasks: set[asyncio.Task] = set()

        self._recall_handler = RecallHandler(
            context=context,
            config_manager=config_manager,
            memory_engine=cast(Any, memory_engine),
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
            identity_enricher=(
                self._identity_runtime.enricher
                if self._identity_runtime is not None
                else None
            ),
            query_rewrite_llm_caller=partial(
                memory_processor.llm_client.call_llm_with_retry,
                system_prompt="只解析记忆查询意图并返回要求的 JSON。",
                max_retries=1,
            ),
            cost_control=self._cost_control,
        )
        self._reflection_handler = ReflectionHandler(
            context=context,
            config_manager=config_manager,
            memory_engine=cast(Any, memory_engine),
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
            memory_quality_gate=memory_quality_gate,
            cost_control=self._cost_control,
            summary_scheduler=summary_scheduler,
        )

    def _new_extra_llm_budget(self, event: AstrMessageEvent) -> ExtraLlmBudget:
        """为新 AstrBot 请求创建预算并附着到同一事件对象。"""

        budget = ExtraLlmBudget(
            self._cost_control.max_extra_llm_calls_per_turn,
            observer=self._observe_extra_llm_budget,
        )
        try:
            setattr(event, "_memora_extra_llm_budget", budget)
        except Exception:
            logger.warning("额外 LLM 请求预算无法附着到事件，响应阶段将使用新预算")
        return budget

    def _extra_llm_budget_for_response(
        self,
        event: AstrMessageEvent,
    ) -> ExtraLlmBudget:
        """复用召回阶段预算；缺少召回钩子时创建独立安全预算。"""

        budget = getattr(event, "_memora_extra_llm_budget", None)
        if isinstance(budget, ExtraLlmBudget):
            return budget
        return self._new_extra_llm_budget(event)

    @staticmethod
    def _clear_extra_llm_budget(
        event: AstrMessageEvent,
        budget: ExtraLlmBudget,
    ) -> None:
        """响应结束后移除事件引用，后台任务继续持有上下文副本。"""

        try:
            if getattr(event, "_memora_extra_llm_budget", None) is budget:
                delattr(event, "_memora_extra_llm_budget")
        except Exception:
            logger.debug("额外 LLM 请求预算事件引用清理失败", exc_info=True)

    @staticmethod
    def _observe_extra_llm_budget(
        observation: ExtraLlmBudgetObservation,
    ) -> None:
        """把预算决策写入只含 allowlist 标量的诊断事件。"""

        report_debug_event(
            "extra_llm_budget",
            component="event_handler",
            stage="budget",
            status="allowed" if observation.allowed else "denied",
            reason_code=observation.reason_code,
            feature=observation.feature,
            allowed=observation.allowed,
            used=observation.used,
            remaining=observation.remaining,
        )

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
            identity = self._resolve_identity(
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
                budget = self._new_extra_llm_budget(event)
                try:
                    with extra_llm_budget_scope(budget):
                        await self._reflection_handler.maybe_schedule_summary(event)
                finally:
                    self._clear_extra_llm_budget(event, budget)

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

    @monitored
    async def handle_memory_recall(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        timing_context: RecallTimingContext | None = None,
    ) -> None:
        """在 LLM 请求前检索并注入长期记忆。"""
        budget = self._new_extra_llm_budget(event)
        with extra_llm_budget_scope(budget), debug_operation():
            identity_started = time.perf_counter()
            identity = self._resolve_identity(
                event,
                writes_blocked=self._writes_blocked(),
            )
            if timing_context is not None:
                timing_context.record_elapsed("identity_resolve_ms", identity_started)
            await self._recall_handler.handle_memory_recall(
                event,
                req,
                identity=identity,
                timing_context=timing_context,
            )

    @monitored
    async def handle_memory_reflection(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
    ) -> None:
        """在 LLM 响应后判断是否需要反思与存储记忆。"""
        budget = self._extra_llm_budget_for_response(event)
        try:
            with extra_llm_budget_scope(budget), debug_operation():
                identity = self._resolve_identity(
                    event,
                    writes_blocked=self._writes_blocked(),
                )
                await self._reflection_handler.handle_memory_reflection(
                    event,
                    resp,
                    identity=identity,
                )
        finally:
            self._clear_extra_llm_budget(event, budget)

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

    @staticmethod
    def _unavailable_identity() -> ResolvedIdentity:
        """在未注入身份端口时返回拒绝身份写入的安全降级快照。"""

        return ResolvedIdentity(
            protocol="",
            identity_namespace="",
            stable_user_id=None,
            canonical_user_id=None,
            scope_type=None,
            scope_id=None,
            global_name=None,
            scope_name=None,
            display_name=None,
            observed_at=0.0,
            trust_status=IdentityTrust.UNSUPPORTED,
            name_field_states={},
        )

    def _resolve_identity(
        self,
        event: AstrMessageEvent,
        *,
        writes_blocked: bool,
    ) -> ResolvedIdentity:
        """同步解析身份，并把可信名称目录更新交给受管理任务。"""

        runtime = self._identity_runtime
        if runtime is None:
            return self._unavailable_identity()
        identity = runtime.resolve(event)
        self._schedule_identity_sync(
            event,
            identity,
            writes_blocked=writes_blocked,
        )
        return identity

    def _schedule_identity_sync(
        self,
        event: AstrMessageEvent,
        identity: ResolvedIdentity,
        *,
        writes_blocked: bool,
    ) -> None:
        """为同一事件至多调度一次可信身份目录同步。"""

        runtime = self._identity_runtime
        if (
            runtime is None
            or writes_blocked
            or identity.trust_status is not IdentityTrust.TRUSTED
        ):
            return
        if getattr(event, self._IDENTITY_SYNC_MARKER_ATTR, False) is True:
            return

        try:
            setattr(event, self._IDENTITY_SYNC_MARKER_ATTR, True)
        except Exception:
            logger.debug("协议身份同步标记写入失败，将继续执行本次同步")

        coroutine = runtime.synchronize(
            event,
            identity,
            writes_blocked=False,
        )
        try:
            self._create_maintenance_task(
                coroutine,
                name="identity-directory-sync",
            )
        except Exception:
            coroutine.close()
            try:
                setattr(event, self._IDENTITY_SYNC_MARKER_ATTR, False)
            except Exception:
                logger.debug("协议身份同步标记回退失败")
            logger.warning("协议身份目录同步任务调度失败", exc_info=True)

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
            atomic_trim = getattr(self.conversation_manager.store, "trim_if_safe", None)
            get_epoch = getattr(
                self.conversation_manager.store, "get_summary_epoch", None
            )
            if inspect.iscoroutinefunction(atomic_trim) and inspect.iscoroutinefunction(
                get_epoch
            ):
                epoch, _cursor = await get_epoch(session_id)
                trim_result = await atomic_trim(session_id, epoch, target_delete)
                actually_deleted = int(getattr(trim_result, "deleted_count", 0) or 0)
                if actually_deleted:
                    await self.conversation_manager.invalidate_cache(session_id)
                return
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
