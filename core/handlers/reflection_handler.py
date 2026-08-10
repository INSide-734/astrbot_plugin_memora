"""记忆反思处理器 —— 在 LLM 响应后执行反思并后台存储记忆。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.api.platform import MessageType

from ..base.config_manager import ConfigManager
from ..features.observability.application import runtime as observability
from ..features.reflection.application.candidate_writer import (
    build_reflection_idempotency_key,
    store_reflection_candidates,
)
from ..features.reflection.application.continuity import resolve_continuity_session
from ..features.reflection.application.reflection_trigger import ReflectionTrigger
from ..features.reflection.domain.storage_outcomes import (
    ReflectionStoreOutcome,
    ReflectionStoreResult,
    summarize_store_results,
)
from ..identity.models import IdentityTrust, ResolvedIdentity
from ..managers.conversation_manager import ConversationManager
from ..managers.memory_engine import MemoryEngine
from ..processors.memory_processor import MemoryProcessor
from ..shared.contracts.prompt_protection import (
    PROMPT_PROTECTION_REQUIRED_ATTR,
    PROMPT_PROTECTION_REQUIRED_EXTRA_KEY,
    PROMPT_PROTECTION_SCOPE_ATTR,
    PROMPT_PROTECTION_SCOPE_EXTRA_KEY,
)
from ..shared.cost_control import CostControl
from ..utils import OperationContext, get_persona_id
from .reflection_backlog import ReflectionBacklogMixin
from .reflection_llm_budget import (
    fit_batches_to_extra_llm_budget,
    process_reflection_batches,
)
from .reflection_metadata import commit_summary_metadata, persist_pending_summary
from .topic_batch_preparer import TopicBatchPreparer

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.provider import LLMResponse

    from ..shared.contracts import PromptProtectionPort


class ReflectionHandler(ReflectionBacklogMixin):
    """在 LLM 响应后执行反思与后台记忆存储。"""

    def __init__(
        self,
        context: Any,
        config_manager: ConfigManager,
        memory_engine: MemoryEngine,
        memory_processor: MemoryProcessor,
        conversation_manager: ConversationManager,
        enforce_limit_cb: Callable,
        affection_manager: Any | None = None,
        expression_learner: Any | None = None,
        jargon_miner: Any | None = None,
        relation_manager: Any | None = None,
        prompt_protection_service: PromptProtectionPort | None = None,
        write_guard_cb: Any | None = None,
        memory_evolution_manager: Any | None = None,
        memory_quality_gate: Any | None = None,
        cost_control: CostControl | None = None,
    ) -> None:
        """装配响应清洗、反思存储及可选认知组件。"""

        self._context = context
        self._config_manager = config_manager
        self._memory_engine = memory_engine
        self._memory_processor = memory_processor
        self._conversation_manager = conversation_manager
        self._enforce_limit_cb = enforce_limit_cb
        self._affection_manager = affection_manager
        self._expression_learner = expression_learner
        self._jargon_miner = jargon_miner
        self._relation_manager = relation_manager
        self._prompt_protection = prompt_protection_service
        self._write_guard_cb = write_guard_cb
        self._memory_evolution_manager = memory_evolution_manager
        self._memory_quality_gate = memory_quality_gate
        self._cost_control = cost_control or CostControl()

        self._storage_tasks: set[asyncio.Task] = set()
        self._storage_sessions_inflight: set[str] = set()
        self._storage_state_lock = asyncio.Lock()
        self._shutting_down = False

        self._batch_preparer = TopicBatchPreparer(
            config_manager=config_manager,
            memory_engine=memory_engine,
            memory_processor=memory_processor,
            cost_control=self._cost_control,
        )
        self._summary_trigger = ReflectionTrigger(
            context=context,
            config_manager=config_manager,
            conversation_manager=conversation_manager,
        )

    async def handle_memory_reflection(
        self,
        event: AstrMessageEvent,
        resp: LLMResponse,
        identity: ResolvedIdentity | None = None,
    ) -> None:
        """在 LLM 响应后检查反思与存储，并保留已解析协议作用域。"""
        observability.report_debug_event(
            "reflection_state",
            component="reflection",
            stage="reflection",
            status="started",
            reason_code="response_received",
        )
        logger.debug(f"[反思处理] 进入 handle_memory_reflection，resp.role={resp.role}")

        scope_id, protection_required, scope_lookup_failed = (
            self._get_prompt_protection_context(event)
        )
        if resp.role != "assistant":
            self._clear_prompt_protection_context(event, scope_id)
            observability.report_debug_event(
                "reflection_state",
                component="reflection",
                stage="response",
                status="skipped",
                reason_code="non_assistant_response",
            )
            return
        if scope_lookup_failed:
            resp.completion_text = ""
            self._clear_prompt_protection_context(event, scope_id)
            observability.report_debug_event(
                "reflection_state",
                component="reflection",
                stage="protection",
                status="skipped",
                reason_code="protection_scope_lookup_failed",
            )
            return

        if protection_required and not scope_id:
            resp.completion_text = ""
            self._clear_prompt_protection_context(event, scope_id)
            observability.report_debug_event(
                "reflection_state",
                component="reflection",
                stage="protection",
                status="skipped",
                reason_code="missing_protection_scope",
            )
            return
        session_id = getattr(event, "unified_msg_origin", "") or ""
        response_text = str(getattr(resp, "completion_text", "") or "")
        response_text = self._sanitize_response_text(
            response_text,
            session_id,
            scope_id=scope_id,
            protection_required=protection_required,
            event=event,
        )
        resp.completion_text = response_text

        if resp.tools_call_name:
            logger.debug(
                f"[反思处理] 检测到工具调用响应（tools={resp.tools_call_name}），跳过记录"
            )
            observability.report_debug_event(
                "reflection_state",
                component="reflection",
                stage="response",
                status="skipped",
                reason_code="tool_call_response",
            )
            return

        if resp.tools_call_extra_content:
            logger.debug(
                "[反思处理] 检测到工具循环总结响应（tools_call_extra_content 非空），跳过记录"
            )
            observability.report_debug_event(
                "reflection_state",
                component="reflection",
                stage="response",
                status="skipped",
                reason_code="tool_loop_summary",
            )
            return

        try:
            logger.debug(f"[反思处理] 获取到 unified_msg_origin: {session_id}")

            if not session_id:
                observability.report_debug_event(
                    "reflection_state",
                    component="reflection",
                    stage="session",
                    status="skipped",
                    reason_code="empty_session",
                )
                logger.warning("[反思处理] 会话 ID 为空，跳过反思")
                return

            if self._writes_blocked():
                observability.report_debug_event(
                    "reflection_state",
                    component="reflection",
                    stage="write_guard",
                    status="skipped",
                    reason_code="write_blocked",
                )
                logger.warning(f"[{session_id}] 备份恢复待应用，跳过 LLM 回复写入")
                return

            if "Error:" in session_id or "error:" in session_id.lower():
                logger.warning(
                    f"[{session_id}] 检测到异常的会话 ID，这可能导致记忆总结异常。"
                )

            if not response_text or not response_text.strip():
                observability.report_debug_event(
                    "reflection_state",
                    component="reflection",
                    stage="response",
                    status="skipped",
                    reason_code="empty_response_after_sanitization",
                )
                logger.warning(f"[{session_id}] 模型回复经安全清洗后为空，跳过记录")
                return
            error_indicators = [
                "api error",
                "request failed",
                "rate limit",
                "timeout",
                "connection error",
                "服务暂时不可用",
                "请求失败",
                "接口错误",
            ]
            response_lower = response_text.lower()
            if any(indicator in response_lower for indicator in error_indicators):
                observability.report_debug_event(
                    "reflection_state",
                    component="reflection",
                    stage="response",
                    status="skipped",
                    reason_code="provider_error_response",
                )
                logger.debug(
                    f"[{session_id}] 检测到错误响应，跳过记录: {response_text[:50]}..."
                )
                return

            await self._conversation_manager.add_message_from_event(
                event=event,
                role="assistant",
                content=response_text,
                identity=identity,
            )
            await self._feed_cognitive_components(event, response_text, identity)
            observability.report_debug_event(
                "reflection_state",
                component="reflection",
                stage="response",
                status="completed",
                reason_code="assistant_response_persisted",
                count=1,
            )
            logger.debug(f"[反思处理] [{session_id}] 已添加助手响应消息")

            is_group = event.get_message_type() == MessageType.GROUP_MESSAGE
            if not is_group:
                await self._enforce_limit_cb(session_id)

            await self.maybe_schedule_summary(event)

        except asyncio.CancelledError:
            observability.report_debug_event(
                "reflection_state",
                component="reflection",
                stage="reflection",
                status="cancelled",
                reason_code="reflection_cancelled",
            )
            raise
        except Exception as e:
            observability.report_debug_exception(
                "reflection_state",
                e,
                component="reflection",
                stage="reflection",
                status="failed",
                reason_code="reflection_error",
            )
            logger.error(f"处理 on_llm_response 钩子时发生错误：{e}", exc_info=True)

    async def maybe_schedule_summary(self, event: AstrMessageEvent) -> None:
        """检查当前会话阈值，并在可用时调度后台记忆反思。

        该入口同时服务于普通群消息捕获和 LLM assistant 响应。普通可恢复
        错误只记录并降级，避免反思检查破坏聊天主链；取消信号保持传播。

        Args:
            event: 提供统一会话标识与人格作用域的 AstrBot 消息事件。

        Returns:
            无返回值；达到阈值时创建并登记后台存储任务。

        Raises:
            asyncio.CancelledError: 当前任务在关闭或取消期间被中止。
        """

        session_id = getattr(event, "unified_msg_origin", "") or ""
        if not session_id:
            observability.report_debug_event(
                "reflection_state",
                component="reflection",
                stage="session",
                status="skipped",
                reason_code="empty_session",
            )
            return

        if self._writes_blocked():
            observability.report_debug_event(
                "reflection_state",
                component="reflection",
                stage="write_guard",
                status="skipped",
                reason_code="write_blocked",
            )
            return

        try:
            request = await self._summary_trigger.prepare(event, session_id)
            if request is None:
                return

            if self._shutting_down:
                observability.report_debug_event(
                    "reflection_state",
                    component="reflection",
                    stage="summary_window",
                    status="skipped",
                    reason_code="shutdown_in_progress",
                )
                return

            if not await self.try_begin_summary_window(session_id):
                observability.report_debug_event(
                    "reflection_state",
                    component="reflection",
                    stage="summary_window",
                    status="skipped",
                    reason_code="storage_task_already_running",
                )
                logger.info(f"[{session_id}] 已有记忆总结任务在执行，跳过本次触发")
                return

            try:
                task = asyncio.create_task(self._drain_summary_backlog(request))
            except Exception:
                self.finish_summary_window(session_id)
                raise

            self._storage_tasks.add(task)
            task.add_done_callback(
                lambda completed, sid=session_id: self._on_storage_task_done(
                    completed, sid
                )
            )
            observability.report_debug_event(
                "reflection_state",
                component="reflection",
                stage="reflection",
                status="completed",
                reason_code="storage_task_scheduled",
                count=len(request.history_messages),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exception:
            observability.report_debug_exception(
                "reflection_state",
                exception,
                component="reflection",
                stage="reflection",
                status="failed",
                reason_code="reflection_error",
            )
            logger.error("检查或调度记忆反思时发生错误", exc_info=True)

    def _sanitize_response_text(
        self,
        response_text: str,
        session_id: str,
        *,
        scope_id: str | None = None,
        protection_required: bool = False,
        event: AstrMessageEvent | None = None,
    ) -> str:
        """清理用户可见回复，并消费与该请求关联的保护作用域。"""
        try:
            if protection_required:
                if self._prompt_protection is None:
                    return ""
                has_scope = getattr(self._prompt_protection, "has_scope", None)
                if callable(has_scope) and not has_scope(scope_id):
                    self._discard_prompt_protection_scope(scope_id)
                    return ""
            if not protection_required and not self._config_manager.get(
                "security.sanitize_llm_response", True
            ):
                self._discard_prompt_protection_scope(scope_id)
                return response_text
            if self._prompt_protection is None:
                return response_text
            sanitized, report = self._prompt_protection.sanitize_response(
                response_text,
                enable_validation=self._config_manager.get(
                    "security.double_check_enabled",
                    True,
                ),
                scope_id=scope_id,
                consume_scope=True,
            )
            leaks = report.get("leaks_removed") or []
            validation_passed = report.get("validation_passed", True)
            if leaks or not validation_passed:
                logger.warning(
                    f"[{session_id}] LLM 回复触发安全清洗："
                    f"移除项数量={len(leaks)}, 校验通过={validation_passed}"
                )
            return sanitized if validation_passed else ""
        except asyncio.CancelledError:
            self._discard_prompt_protection_scope(scope_id)
            raise
        except Exception:
            self._discard_prompt_protection_scope(scope_id)
            logger.warning(
                f"[{session_id}] LLM 回复安全清洗失败，已阻止输出",
                exc_info=True,
            )
            return ""
        finally:
            if event is not None:
                self._clear_prompt_protection_context(event, scope_id)

    def _discard_prompt_protection_scope(self, scope_id: str | None) -> None:
        if self._prompt_protection is None or not scope_id:
            return
        discard = getattr(self._prompt_protection, "discard_scope", None)
        if callable(discard):
            try:
                discard(scope_id)
            except Exception:
                logger.warning("[反思处理] 请求安全关联清理失败", exc_info=True)

    def _clear_prompt_protection_context(
        self,
        event: AstrMessageEvent,
        scope_id: str | None,
    ) -> None:
        self._discard_prompt_protection_scope(scope_id)
        try:
            setter = getattr(event, "set_extra", None)
        except asyncio.CancelledError:
            raise
        except Exception:
            setter = None
            logger.warning(
                "[反思处理] 请求安全关联官方通道清理失败",
                exc_info=True,
            )
        if callable(setter):
            for key, value in (
                (PROMPT_PROTECTION_SCOPE_EXTRA_KEY, None),
                (PROMPT_PROTECTION_REQUIRED_EXTRA_KEY, False),
            ):
                try:
                    setter(key, value)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "[反思处理] 请求安全关联官方通道清理失败",
                        exc_info=True,
                    )
        for attr, reset_value in (
            (PROMPT_PROTECTION_SCOPE_ATTR, None),
            (PROMPT_PROTECTION_REQUIRED_ATTR, False),
        ):
            try:
                delattr(event, attr)
            except asyncio.CancelledError:
                raise
            except Exception:
                try:
                    setattr(event, attr, reset_value)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "[反思处理] 请求安全关联私有通道清理失败",
                        exc_info=True,
                    )

    @staticmethod
    def _get_prompt_protection_context(
        event: AstrMessageEvent,
    ) -> tuple[str | None, bool, bool]:
        official_scope: Any = None
        official_required: Any = False
        official_failed = False
        getter = getattr(event, "get_extra", None)
        if callable(getter):
            try:
                official_scope = getter(PROMPT_PROTECTION_SCOPE_EXTRA_KEY)
                official_required = getter(PROMPT_PROTECTION_REQUIRED_EXTRA_KEY)
            except asyncio.CancelledError:
                raise
            except Exception:
                official_failed = True
                logger.warning(
                    "[反思处理] 请求安全关联官方通道读取失败",
                    exc_info=True,
                )
        try:
            private_scope = getattr(event, PROMPT_PROTECTION_SCOPE_ATTR, None)
            private_required = getattr(event, PROMPT_PROTECTION_REQUIRED_ATTR, False)
        except Exception:
            private_scope = None
            private_required = False
        scope_id = (
            official_scope
            if isinstance(official_scope, str) and official_scope
            else private_scope
            if isinstance(private_scope, str) and private_scope
            else None
        )
        required = official_required is True or private_required is True
        lookup_failed = (
            official_failed and scope_id is None and private_required is not True
        )
        return scope_id, required, lookup_failed

    def _writes_blocked(self) -> bool:
        if self._write_guard_cb is None:
            return False
        try:
            return bool(self._write_guard_cb())
        except Exception:
            logger.error("[反思处理] 写入维护状态检查失败", exc_info=True)
            return True

    async def _feed_cognitive_components(
        self,
        event: AstrMessageEvent,
        response_text: str,
        identity: ResolvedIdentity | None = None,
    ) -> None:
        """尽力将助手回复投喂给可选认知模块。"""
        session_id = event.unified_msg_origin or "default"
        sender_id = self._user_id_for_identity(event, identity)
        persona_id = await get_persona_id(self._context, event)
        try:
            if self._expression_learner is not None:
                self._expression_learner.buffer_message(
                    group_id=session_id,
                    sender_id=getattr(self._expression_learner, "bot_id", "bot"),
                    content=response_text,
                )
                await self._expression_learner.maybe_learn(
                    session_id,
                    persona_id=persona_id or "default",
                    user_id=None,
                )
        except Exception:
            logger.debug("[认知模块] 助手回复投喂到表达模式学习器失败", exc_info=True)

        try:
            if self._affection_manager is not None and sender_id is not None:
                user_text = await self._latest_user_text(session_id)
                await self._affection_manager.process_interaction(
                    user_id=sender_id,
                    group_id=session_id,
                    message=user_text,
                    bot_response=response_text,
                )
        except Exception:
            logger.debug("[认知模块] 好感度更新失败", exc_info=True)

        try:
            if (
                self._jargon_miner is not None
                and event.get_message_type() == MessageType.GROUP_MESSAGE
            ):
                await self._jargon_miner.run_once(session_id, limit=2)
        except Exception:
            logger.debug("[认知模块] 基于助手回复触发黑话挖掘失败", exc_info=True)

    @staticmethod
    def _user_id_for_identity(
        event: AstrMessageEvent,
        identity: ResolvedIdentity | None,
    ) -> str | None:
        """选择好感度用户标识；未注册协议保持旧事件发送者语义。"""

        if identity is not None:
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

    async def _latest_user_text(self, session_id: str) -> str:
        try:
            recent = await self._conversation_manager.get_context(
                session_id,
                max_messages=4,
            )
            for msg in reversed(recent or []):
                if msg.get("role") == "user" and msg.get("content"):
                    return str(msg["content"])
        except Exception:
            logger.debug("[认知模块] 查询最近一条用户消息失败", exc_info=True)
        return ""

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
        return fit_batches_to_extra_llm_budget(batches, self._cost_control)

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
                    batch_results = await process_reflection_batches(
                        batches,
                        process_conversation=(
                            self._memory_processor.process_conversation
                        ),
                        cost_control=self._cost_control,
                        is_group_chat=is_group_chat,
                        persona_id=persona_id,
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
                        "幂等跳过=%d，失败=%d（%d条消息）",
                        session_id,
                        store_summary.canonical_count,
                        store_summary.quarantine_count,
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
                    metadata_committed = await commit_summary_metadata(
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

                resolve_continuity_session(self._memory_engine, session_id)

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
        return await persist_pending_summary(
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

    async def shutdown(self) -> None:
        """关闭反思处理器，并等待所有存储任务完成。"""
        self._shutting_down = True
        if self._storage_tasks:
            logger.info(f"等待 {len(self._storage_tasks)} 个存储任务完成……")
            await asyncio.gather(*self._storage_tasks, return_exceptions=True)
            self._storage_tasks.clear()
        self._storage_sessions_inflight.clear()
        logger.info("反思处理器已关闭")
