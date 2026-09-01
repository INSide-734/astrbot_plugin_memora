"""
命令处理器
负责处理插件命令
"""

import asyncio
import inspect
from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult

from ....features.conversation.application.conversation_manager import (
    ConversationManager,
)
from ....features.memory.application.memory_engine import MemoryEngine
from ....features.memory.infrastructure.validators import IndexValidator
from ....features.quality.application.gate_runtime import capture_gate_snapshot_json
from ....features.reflection.domain.summary_models import (
    SummaryReasonCode,
    SummaryWindowContext,
)
from ....shared.contracts import IdentityConversationPort
from ...config.manager import ConfigManager
from ...resources.i18n_backend import t
from .diagnostic_commands import (
    DiagnosticCommandMixin,
    DiagnosticProvider,
)
from .maintenance_commands import MaintenanceCommandMixin
from .query_commands import QueryCommandMixin
from .update_commands import UpdateCommandMixin


class CommandHandler(
    DiagnosticCommandMixin,
    QueryCommandMixin,
    MaintenanceCommandMixin,
    UpdateCommandMixin,
):
    """命令处理器"""

    def __init__(
        self,
        context,
        config_manager: ConfigManager,
        memory_engine: MemoryEngine | None,
        conversation_manager: ConversationManager | None,
        index_validator: IndexValidator | None,
        summary_scheduler=None,
        memory_processor=None,
        memory_quality_gate=None,
        initialization_status_callback=None,
        write_guard_cb=None,
        diagnostics_health_provider: DiagnosticProvider | None = None,
        diagnostics_metrics_provider: DiagnosticProvider | None = None,
        recall_trace_provider: DiagnosticProvider | None = None,
        update_manager=None,
        update_installer=None,
        identity_runtime: IdentityConversationPort | None = None,
    ) -> None:
        """
        初始化命令处理器

        参数：
            context: AstrBot Context
            config_manager: 配置管理器
            memory_engine: 记忆引擎
            conversation_manager: 会话管理器
            index_validator: 索引验证器
            memory_processor: 记忆处理器（用于手动总结）
            memory_quality_gate: canonical 写入前的记忆质量门
            initialization_status_callback: 初始化状态回调函数
            diagnostics_health_provider: 健康评分异步提供器
            diagnostics_metrics_provider: 实时指标异步提供器
            recall_trace_provider: 召回追踪异步提供器
            update_manager: runtime 更新服务
            update_installer: runtime 安装、重载与回滚服务
            identity_runtime: 组合根发布的协议身份端口
        """
        self.context = context
        self.config_manager = config_manager
        self.memory_engine = memory_engine
        self.conversation_manager = conversation_manager
        self.index_validator = index_validator
        self._identity_runtime: IdentityConversationPort | None = (
            identity_runtime
            if isinstance(identity_runtime, IdentityConversationPort)
            else None
        )
        self._summary_scheduler = summary_scheduler
        self._memory_processor = memory_processor
        self._memory_quality_gate = memory_quality_gate
        self.get_initialization_status = initialization_status_callback
        self._write_guard_cb = write_guard_cb
        self._diagnostics_health_provider = diagnostics_health_provider
        self._diagnostics_metrics_provider = diagnostics_metrics_provider
        self._recall_trace_provider = recall_trace_provider
        self._update_manager = update_manager
        self._update_installer = update_installer

    def _maintenance_write_guard_message(self) -> str | None:
        if self._write_guard_cb is None:
            return None
        try:
            if self._write_guard_cb():
                return "备份恢复已暂存，重启 AstrBot 完成恢复前暂时拒绝写入操作。"
        except Exception as error:
            logger.error(
                "[CommandHandler] 写入维护状态检查失败，异常类型=%s",
                error.__class__.__name__,
            )
            return "未接受：reason=write_guard_check_failed"
        return None

    async def _enqueue_manual_summary(self, event: AstrMessageEvent) -> str:
        """构造安全固定窗口上下文并返回即时入队反馈。"""
        manager = self.conversation_manager
        scheduler = self._summary_scheduler
        if manager is None or scheduler is None:
            return self._component_not_ready_message("总结调度器", "/memora summarize")
        session_id = str(getattr(event, "unified_msg_origin", "") or "")
        if not session_id:
            return "未接受：reason=empty_session"
        store = getattr(manager, "store", None)
        get_epoch = getattr(store, "get_summary_epoch", None)
        get_end = getattr(store, "get_message_seq_end", None)
        if not callable(get_epoch) or not callable(get_end):
            return "未接受：reason=scope_unavailable"
        epoch_value = get_epoch(session_id)
        if inspect.isawaitable(epoch_value):
            epoch_value = await epoch_value
        if not isinstance(epoch_value, (tuple, list)) or len(epoch_value) < 2:
            return "未接受：reason=scope_unavailable"
        epoch, cursor = int(epoch_value[0]), int(epoch_value[1])
        observed_end = get_end(session_id)
        if inspect.isawaitable(observed_end):
            observed_end = await observed_end
        if isinstance(observed_end, bool) or not isinstance(observed_end, int):
            return "未接受：reason=scope_unavailable"
        get_scope = getattr(store, "get_summary_scope", None)
        if not callable(get_scope):
            return "未接受：reason=scope_unavailable"
        scope = get_scope(session_id)
        if inspect.isawaitable(scope):
            scope = await scope
        if not isinstance(scope, (tuple, list)) or len(scope) != 4:
            return "未接受：reason=scope_unavailable"
        chat_type, group_id, scope_id, stored_persona = scope
        if chat_type not in {"private", "group"} or not isinstance(scope_id, str):
            return "未接受：reason=scope_unavailable"
        if chat_type == "group" and not isinstance(group_id, str):
            return "未接受：reason=scope_unavailable"
        if chat_type == "private" and group_id is not None:
            return "未接受：reason=scope_unavailable"
        if not scope_id.strip() or (chat_type == "group" and not str(group_id).strip()):
            return "未接受：reason=scope_unavailable"
        if observed_end - cursor < 2:
            return "未接受：reason=no_window"
        from ...context_helpers import get_persona_id

        persona_id = await get_persona_id(self.context, event) or stored_persona
        gate_runtime = getattr(self._memory_quality_gate, "gate_runtime", None)
        snapshot = gate_runtime.snapshot() if gate_runtime is not None else None
        gate_revision = str(getattr(snapshot, "revision", "") or "")
        gate_snapshot_json = capture_gate_snapshot_json(gate_runtime)
        context = SummaryWindowContext(
            session_id=session_id,
            session_epoch=epoch,
            start_seq=cursor,
            end_seq=cursor,
            persona_id=persona_id,
            chat_type=chat_type,
            group_id=group_id,
            scope_id=scope_id,
            gate_revision=gate_revision,
            gate_snapshot_json=gate_snapshot_json,
            window_size=max(
                2,
                int(
                    self.config_manager.get(
                        "reflection_engine.summary_trigger_rounds", 10
                    )
                )
                * 2,
            ),
        )
        result = await scheduler.enqueue_manual(context, observed_end)
        if result.accepted:
            return (
                f"已接受：queued={result.queued}，重复={result.duplicates}，"
                f"active={result.active_parallelism}，target={result.target_parallelism}"
            )
        reason = (
            result.reason_code.value
            if isinstance(result.reason_code, SummaryReasonCode)
            else str(result.reason_code)
        )
        return f"未接受：reason={reason}"

    async def _confirm_abandon_summary(self, event: AstrMessageEvent) -> str:
        """管理员确认当前会话无 canonical 证据的阻塞窗口可跳过。"""
        manager = self.conversation_manager
        scheduler = self._summary_scheduler
        if manager is None or scheduler is None:
            return self._component_not_ready_message("总结调度器", "/memora summarize")
        session_id = str(getattr(event, "unified_msg_origin", "") or "")
        if not session_id:
            return "未接受：reason=empty_session"
        get_epoch = getattr(getattr(manager, "store", None), "get_summary_epoch", None)
        confirm = getattr(scheduler, "confirm_abandon_session_jobs", None)
        if not callable(get_epoch) or not callable(confirm):
            return "未接受：reason=summary_recovery_unavailable"
        epoch_value = get_epoch(session_id)
        if inspect.isawaitable(epoch_value):
            epoch_value = await epoch_value
        if not isinstance(epoch_value, (tuple, list)) or not epoch_value:
            return "未接受：reason=summary_recovery_unavailable"
        confirmed = confirm(session_id, int(epoch_value[0]))
        if inspect.isawaitable(confirmed):
            confirmed = await confirmed
        if (
            isinstance(confirmed, bool)
            or not isinstance(confirmed, int)
            or confirmed < 1
        ):
            return "未接受：reason=no_abandonable_window"
        return f"已确认：abandoned={confirmed}"

    async def _yield_if_writes_blocked(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        message = self._maintenance_write_guard_message()
        if message:
            yield event.plain_result(message)

    async def handle_summarize(
        self, event: AstrMessageEvent, action: str = ""
    ) -> AsyncGenerator[MessageEventResult, None]:
        """立即入队总结，或显式确认跳过不可恢复的阻塞窗口。"""
        blocked_message = self._maintenance_write_guard_message()
        if blocked_message:
            yield event.plain_result(blocked_message)
            return
        if self._summary_scheduler is None:
            yield event.plain_result(
                self._component_not_ready_message("总结调度器", "/memora summarize")
            )
            return
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"", "confirm-abandon"}:
            yield event.plain_result("未接受：reason=invalid_summarize_action")
            return
        try:
            message = (
                await self._confirm_abandon_summary(event)
                if normalized_action == "confirm-abandon"
                else await self._enqueue_manual_summary(event)
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "手动总结操作失败，异常类型=%s",
                error.__class__.__name__,
            )
            message = "未接受：reason=summary_action_failed"
        yield event.plain_result(message)

    @staticmethod
    async def handle_help(
        event: AstrMessageEvent,
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /memora help 命令"""
        message = t("help.text")
        yield event.plain_result(message)
