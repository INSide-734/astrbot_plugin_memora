"""记忆反思处理器 —— 在 LLM 响应后执行反思并后台存储记忆。"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from astrbot.api import logger
from astrbot.api.platform import MessageType

from ....platform.config.manager import ConfigManager
from ....platform.context_helpers import get_persona_id
from ....shared.contracts import ReflectionWritePort
from ....shared.cost_control import CostControl
from ...conversation.application.conversation_manager import ConversationManager
from ...identity.domain.models import IdentityTrust, ResolvedIdentity
from ...observability.application import runtime as observability
from ...quality.application.gate_runtime import capture_gate_snapshot_json
from ...recall.processors.memory_processor import MemoryProcessor
from ..domain.summary_models import SummaryWindowContext
from .continuity import resolve_continuity_session as resolve_continuity_session
from .reflection_context import ReflectionContextMixin

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.provider import LLMResponse

    from ....shared.contracts import PromptProtectionPort


class ReflectionHandler(ReflectionContextMixin):
    """在 LLM 响应后执行反思与后台总结入队。"""

    def __init__(
        self,
        context: Any,
        config_manager: ConfigManager,
        memory_engine: ReflectionWritePort,
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
        summary_scheduler: Any | None = None,
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
        self._summary_scheduler = summary_scheduler

    async def _resolve_persona_id(self, event: AstrMessageEvent) -> str | None:
        """通过处理器拥有的平台上下文解析当前人格标识。"""
        return await get_persona_id(self._context, event)

    def _summary_gate_context(self) -> tuple[str, str]:
        """返回入队任务使用的当前门禁修订号和安全快照 JSON。"""
        gate_runtime = getattr(self._memory_quality_gate, "gate_runtime", None)
        gate_revision = ""
        if gate_runtime is not None:
            snapshot = gate_runtime.snapshot()
            gate_revision = str(getattr(snapshot, "revision", "") or "")
        gate_snapshot_json = capture_gate_snapshot_json(gate_runtime)
        return gate_revision, gate_snapshot_json

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
            logger.debug("[反思处理] 开始处理响应后的稳定消息")
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
                logger.warning("备份恢复待应用，跳过 LLM 回复写入")
                return

            if "Error:" in session_id or "error:" in session_id.lower():
                logger.warning("检测到异常会话标识，跳过总结相关处理")

            if not response_text or not response_text.strip():
                observability.report_debug_event(
                    "reflection_state",
                    component="reflection",
                    stage="response",
                    status="skipped",
                    reason_code="empty_response_after_sanitization",
                )
                logger.warning("模型回复经安全清洗后为空，跳过记录")
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
                logger.debug("检测到 Provider 错误响应，跳过记录")
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
            logger.debug("[反思处理] 助手响应消息已持久化")

            is_group = event.get_message_type() == MessageType.GROUP_MESSAGE
            if not is_group:
                await self._enforce_limit_cb(session_id)

            await self.maybe_schedule_summary(event, identity=identity)

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
            logger.error(
                "处理 on_llm_response 钩子时发生错误，异常类型=%s",
                e.__class__.__name__,
            )

    async def maybe_schedule_summary(
        self,
        event: AstrMessageEvent,
        *,
        identity: ResolvedIdentity | None = None,
    ) -> None:
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
            scheduler = self._summary_scheduler
            if scheduler is None:
                observability.report_debug_event(
                    "reflection_state",
                    component="reflection",
                    stage="summary_scheduler",
                    status="skipped",
                    reason_code="component_unavailable",
                )
                return
            store = self._conversation_manager.store
            get_scope = cast(Any, getattr(store, "get_summary_scope", None))
            get_epoch = cast(Any, getattr(store, "get_summary_epoch", None))
            get_end = cast(Any, getattr(store, "get_message_seq_end", None))
            if not all(callable(method) for method in (get_scope, get_epoch, get_end)):
                return
            scope = get_scope(session_id)
            if inspect.isawaitable(scope):
                scope = await scope
            if not isinstance(scope, tuple) or len(scope) != 4:
                return
            chat_type, group_id, scope_id, stored_persona = scope
            if identity is not None:
                if identity.trust_status in {
                    IdentityTrust.CONFLICT,
                    IdentityTrust.INVALID,
                }:
                    return
                if identity.scope_type == "group" and identity.scope_id != group_id:
                    return
                if identity.scope_type == "private" and group_id is not None:
                    return
            epoch_value = get_epoch(session_id)
            if inspect.isawaitable(epoch_value):
                epoch_value = await epoch_value
            if not isinstance(epoch_value, (tuple, list)) or len(epoch_value) < 2:
                return
            epoch, cursor = int(epoch_value[0]), int(epoch_value[1])
            observed_end = get_end(session_id)
            if inspect.isawaitable(observed_end):
                observed_end = await observed_end
            if isinstance(observed_end, bool) or not isinstance(observed_end, int):
                return
            persona_id = await self._resolve_persona_id(event)
            gate_revision, gate_snapshot_json = self._summary_gate_context()
            context = SummaryWindowContext(
                session_id=session_id,
                session_epoch=epoch,
                start_seq=cursor,
                end_seq=cursor,
                persona_id=persona_id or stored_persona,
                chat_type=chat_type,
                group_id=group_id,
                scope_id=scope_id,
                triggered_by="automatic",
                gate_revision=gate_revision,
                gate_snapshot_json=gate_snapshot_json,
                window_size=max(
                    2,
                    int(
                        self._config_manager.get(
                            "reflection_engine.summary_trigger_rounds", 10
                        )
                    )
                    * 2,
                ),
            )
            result = await scheduler.enqueue_automatic(context, observed_end)
            observability.report_debug_event(
                "reflection_state",
                component="reflection",
                stage="reflection",
                status="completed" if result.accepted else "skipped",
                reason_code=str(
                    getattr(result.reason_code, "value", result.reason_code)
                ),
                count=result.queued,
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
            logger.error(
                "检查或调度记忆反思时发生错误，异常类型=%s",
                exception.__class__.__name__,
            )

    async def shutdown(self) -> None:
        """标记反思入口停止接收新事件；总结调度器由组合根统一关闭。"""
        self._shutting_down = True
        logger.info("反思处理器已关闭")
