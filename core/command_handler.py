"""
命令处理器
负责处理插件命令
"""

from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult

from .base.config_manager import ConfigManager
from .commands.diagnostic_commands import DiagnosticCommandMixin, DiagnosticProvider
from .commands.maintenance_commands import MaintenanceCommandMixin
from .commands.query_commands import QueryCommandMixin
from .i18n_backend import t, t_list
from .managers.conversation_manager import ConversationManager
from .managers.memory_engine import MemoryEngine
from .validators.index_validator import IndexValidator


class CommandHandler(
    DiagnosticCommandMixin,
    QueryCommandMixin,
    MaintenanceCommandMixin,
):
    """命令处理器"""

    def __init__(
        self,
        context,
        config_manager: ConfigManager,
        memory_engine: MemoryEngine | None,
        conversation_manager: ConversationManager | None,
        index_validator: IndexValidator | None,
        memory_processor=None,
        initialization_status_callback=None,
        summary_window_locker=None,
        write_guard_cb=None,
        diagnostics_health_provider: DiagnosticProvider | None = None,
        diagnostics_metrics_provider: DiagnosticProvider | None = None,
        recall_trace_provider: DiagnosticProvider | None = None,
    ):
        """
        初始化命令处理器

        参数：
            context: AstrBot Context
            config_manager: 配置管理器
            memory_engine: 记忆引擎
            conversation_manager: 会话管理器
            index_validator: 索引验证器
            memory_processor: 记忆处理器（用于手动总结）
            initialization_status_callback: 初始化状态回调函数
            diagnostics_health_provider: 健康评分异步提供器
            diagnostics_metrics_provider: 实时指标异步提供器
            recall_trace_provider: 召回追踪异步提供器
        """
        self.context = context
        self.config_manager = config_manager
        self.memory_engine = memory_engine
        self.conversation_manager = conversation_manager
        self.index_validator = index_validator
        self._memory_processor = memory_processor
        self.get_initialization_status = initialization_status_callback
        self._summary_window_locker = summary_window_locker
        self._write_guard_cb = write_guard_cb
        self._diagnostics_health_provider = diagnostics_health_provider
        self._diagnostics_metrics_provider = diagnostics_metrics_provider
        self._recall_trace_provider = recall_trace_provider

    def _maintenance_write_guard_message(self) -> str | None:
        if self._write_guard_cb is None:
            return None
        try:
            if self._write_guard_cb():
                return "备份恢复已暂存，重启 AstrBot 完成恢复前暂时拒绝写入操作。"
        except Exception as exc:
            logger.error("[CommandHandler] 写入维护状态检查失败: %s", exc, exc_info=True)
            return f"维护状态检查失败: {exc}"
        return None

    async def _yield_if_writes_blocked(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        message = self._maintenance_write_guard_message()
        if message:
            yield event.plain_result(message)

    async def handle_summarize(
        self, event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem summarize 命令 - 立即触发记忆总结"""
        blocked_message = self._maintenance_write_guard_message()
        if blocked_message:
            yield event.plain_result(blocked_message)
            return
        if not self.conversation_manager or not self.memory_engine:
            yield event.plain_result(
                self._component_not_ready_message(
                    "会话管理器或记忆引擎", "/lmem summarize"
                )
            )
            return

        session_id = event.unified_msg_origin
        window_reserved = False
        try:
            if self._summary_window_locker is not None:
                window_reserved = await self._summary_window_locker.try_begin_summary_window(
                    session_id
                )
                if not window_reserved:
                    yield event.plain_result("该会话已有记忆总结任务正在执行，请稍后再试。")
                    return

            # 获取当前消息数和总结进度
            actual_count = await self.conversation_manager.store.get_message_count(
                session_id
            )
            last_summarized_index = (
                await self.conversation_manager.get_session_metadata(
                    session_id, "last_summarized_index", 0
                )
            )
            try:
                last_summarized_index = int(last_summarized_index)
            except (TypeError, ValueError):
                last_summarized_index = 0

            unsummarized = actual_count - last_summarized_index

            if unsummarized < 2:
                yield event.plain_result(
                    t(
                        "summarize.no_new",
                        total=actual_count,
                        index=last_summarized_index,
                    )
                )
                return

            yield event.plain_result(
                t(
                    "summarize.starting",
                    start=last_summarized_index,
                    end=actual_count,
                    count=unsummarized,
                )
            )

            history_messages = await self.conversation_manager.get_messages_range(
                session_id=session_id,
                start_index=last_summarized_index,
                end_index=actual_count,
            )

            if not history_messages:
                yield event.plain_result(t("summarize.fetch_failed"))
                return

            # 获取 persona_id
            from .utils import get_persona_id

            persona_id = await get_persona_id(self.context, event)

            # 判断是否群聊
            is_group_chat = bool(
                history_messages[0].group_id if history_messages else False
            )
            if not is_group_chat and "GroupMessage" in session_id:
                is_group_chat = True

            if not self._memory_processor:
                yield event.plain_result(
                    self._component_not_ready_message("记忆处理器", "/lmem summarize")
                )
                return

            memories = await self._memory_processor.process_conversation(
                messages=history_messages,
                is_group_chat=is_group_chat,
                persona_id=persona_id,
            )

            all_topics: list[str] = []
            stored_count = 0
            for mem in memories:
                metadata = mem.setdefault("metadata", {})
                metadata["source_window"] = {
                    "session_id": session_id,
                    "start_index": last_summarized_index,
                    "end_index": actual_count,
                    "message_count": actual_count - last_summarized_index,
                    "triggered_by": "manual",
                }
                try:
                    await self.memory_engine.add_memory(
                        content=mem["content"],
                        session_id=session_id,
                        persona_id=persona_id,
                        importance=mem["importance"],
                        metadata=metadata,
                        atoms=mem.get("atoms", []),
                    )
                    stored_count += 1
                    all_topics.extend(metadata.get("topics", []))
                except Exception as write_err:
                    logger.error(
                        f"[{session_id}] 手动总结记忆写入失败: {write_err}",
                        exc_info=True,
                    )

            if stored_count < len(memories):
                await self.conversation_manager.update_session_metadata(
                    session_id,
                    "pending_summary",
                    {
                        "start_index": last_summarized_index,
                        "end_index": actual_count,
                        "retry_count": 1,
                        "failed_stage": "manual_memory_write",
                        "failed_count": len(memories) - stored_count,
                    },
                )
                raise RuntimeError(
                    f"仅成功写入 {stored_count}/{len(memories)} 条记忆，窗口未推进"
                )

            await self.conversation_manager.update_session_metadata(
                session_id, "last_summarized_index", actual_count
            )
            await self.conversation_manager.update_session_metadata(
                session_id, "pending_summary", None
            )

            avg_importance = (
                sum(m.get("importance", 0) for m in memories) / len(memories)
                if memories
                else 0.0
            )
            yield event.plain_result(
                t(
                    "summarize.success",
                    importance=round(avg_importance, 2),
                    topics=", ".join(all_topics) or t("common.none"),
                    count=len(memories),
                )
            )

        except Exception as e:
            logger.error(f"手动触发记忆总结失败: {e}", exc_info=True)
            yield event.plain_result(
                self._format_error_message(
                    t("summarize.action_name"),
                    e,
                    t_list("error.suggestions.summarize"),
                )
            )
        finally:
            if window_reserved and self._summary_window_locker is not None:
                self._summary_window_locker.finish_summary_window(session_id)

    @staticmethod
    async def handle_help(
            event: AstrMessageEvent
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /lmem help 命令"""
        message = t("help.text")
        yield event.plain_result(message)
