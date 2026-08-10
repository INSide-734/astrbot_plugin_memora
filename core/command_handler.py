"""
命令处理器
负责处理插件命令
"""

import asyncio
from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult

from .base.config_manager import ConfigManager
from .commands.diagnostic_commands import DiagnosticCommandMixin, DiagnosticProvider
from .commands.maintenance_commands import MaintenanceCommandMixin
from .commands.query_commands import QueryCommandMixin
from .commands.update_commands import UpdateCommandMixin
from .features.memory.infrastructure.validators import IndexValidator
from .features.reflection.application.candidate_writer import (
    build_reflection_idempotency_key,
)
from .i18n_backend import t, t_list
from .managers.conversation_manager import ConversationManager
from .managers.memory_engine import MemoryEngine
from .shared.contracts import IdentityConversationPort


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
        memory_processor=None,
        memory_quality_gate=None,
        initialization_status_callback=None,
        summary_window_locker=None,
        write_guard_cb=None,
        diagnostics_health_provider: DiagnosticProvider | None = None,
        diagnostics_metrics_provider: DiagnosticProvider | None = None,
        recall_trace_provider: DiagnosticProvider | None = None,
        update_manager=None,
        update_installer=None,
        identity_runtime: IdentityConversationPort | None = None,
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
        self._memory_processor = memory_processor
        self._memory_quality_gate = memory_quality_gate
        self.get_initialization_status = initialization_status_callback
        self._summary_window_locker = summary_window_locker
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
        except Exception as exc:
            logger.error(
                "[CommandHandler] 写入维护状态检查失败: %s", exc, exc_info=True
            )
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
        """立即总结当前会话，并分别反馈 canonical 写入与隔离结果。

        参数:
            event: 提供当前会话来源、身份上下文和命令结果构造能力的事件。

        生成:
            总结开始、成功、隔离或失败阶段的 AstrBot 消息结果。

        副作用:
            通过质量门写入 canonical 记忆；全部候选安全处理后推进会话总结进度，
            真实写入失败时保留 ``pending_summary``，并始终释放已取得的窗口锁。
        """
        blocked_message = self._maintenance_write_guard_message()
        if blocked_message:
            yield event.plain_result(blocked_message)
            return
        if not self.conversation_manager or not self.memory_engine:
            yield event.plain_result(
                self._component_not_ready_message(
                    "会话管理器或记忆引擎", "/memora summarize"
                )
            )
            return

        session_id = event.unified_msg_origin
        window_reserved = False
        try:
            if self._summary_window_locker is not None:
                window_reserved = (
                    await self._summary_window_locker.try_begin_summary_window(
                        session_id
                    )
                )
                if not window_reserved:
                    yield event.plain_result(
                        "该会话已有记忆总结任务正在执行，请稍后再试。"
                    )
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
                    self._component_not_ready_message("记忆处理器", "/memora summarize")
                )
                return

            memories = await self._memory_processor.process_conversation(
                messages=history_messages,
                is_group_chat=is_group_chat,
                persona_id=persona_id,
            )

            all_topics: list[str] = []
            canonical_count = 0
            canonical_importance_total = 0.0
            quarantined_count = 0
            completed_idempotency_keys: set[str] = set()
            for memory_index, mem in enumerate(memories):
                metadata = mem.setdefault("metadata", {})
                idempotency_key = build_reflection_idempotency_key(
                    session_id=session_id,
                    start_index=last_summarized_index,
                    end_index=actual_count,
                    batch_index=int(metadata.get("batch_index", 0) or 0),
                    memory_index=memory_index,
                    content=str(mem.get("content", "") or ""),
                )
                metadata["idempotency_key"] = idempotency_key
                metadata["source_window"] = {
                    "session_id": session_id,
                    "start_index": last_summarized_index,
                    "end_index": actual_count,
                    "message_count": actual_count - last_summarized_index,
                    "triggered_by": "manual",
                }
                try:
                    if self._memory_quality_gate is not None:
                        gate_result = await self._memory_quality_gate.route_candidate(
                            mem,
                            session_id=session_id,
                            persona_id=persona_id,
                            source_window=metadata["source_window"],
                            is_group_chat=is_group_chat,
                        )
                        if gate_result.action == "quarantined":
                            quarantined_count += 1
                            completed_idempotency_keys.add(idempotency_key)
                            continue
                    await self.memory_engine.add_memory(
                        content=mem["content"],
                        session_id=session_id,
                        persona_id=persona_id,
                        importance=mem["importance"],
                        metadata=metadata,
                        atoms=mem.get("atoms", []),
                    )
                    canonical_count += 1
                    completed_idempotency_keys.add(idempotency_key)
                    canonical_importance_total += mem.get("importance", 0)
                    all_topics.extend(metadata.get("topics", []))
                except Exception as write_err:
                    logger.error(
                        f"[{session_id}] 手动总结记忆写入失败: {write_err}",
                        exc_info=True,
                    )

            processed_count = canonical_count + quarantined_count
            if processed_count < len(memories):
                pending_persisted = (
                    await self.conversation_manager.update_session_metadata(
                        session_id,
                        "pending_summary",
                        {
                            "start_index": last_summarized_index,
                            "end_index": actual_count,
                            "retry_count": 1,
                            "failed_stage": "manual_memory_write",
                            "failed_count": len(memories) - processed_count,
                            "completed_idempotency_keys": sorted(
                                completed_idempotency_keys
                            ),
                        },
                    )
                )
                if pending_persisted is not True:
                    logger.error(
                        f"[{session_id}] 手动总结失败窗口未能持久化，将拒绝报告为已处理"
                    )
                raise RuntimeError(
                    f"仅安全处理 {processed_count}/{len(memories)} 条候选，窗口未推进"
                )

            try:
                metadata_persisted = (
                    await self.conversation_manager.update_session_metadata_fields(
                        session_id,
                        {
                            "last_summarized_index": actual_count,
                            "pending_summary": None,
                        },
                    )
                )
                if metadata_persisted is not True:
                    raise RuntimeError("总结游标与待重试状态原子持久化失败")
            except asyncio.CancelledError:
                raise
            except Exception as metadata_error:
                pending_persisted = False
                try:
                    pending_persisted = (
                        await self.conversation_manager.update_session_metadata(
                            session_id,
                            "pending_summary",
                            {
                                "start_index": last_summarized_index,
                                "end_index": actual_count,
                                "retry_count": 1,
                                "failed_stage": "metadata_commit",
                                "failed_count": 0,
                                "completed_idempotency_keys": sorted(
                                    completed_idempotency_keys
                                ),
                            },
                        )
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as pending_error:
                    logger.error(
                        f"[{session_id}] 元数据失败窗口写入异常：{pending_error}",
                        exc_info=True,
                    )
                if pending_persisted is not True:
                    logger.error(
                        f"[{session_id}] 元数据失败窗口未能持久化，无法确认后续重试状态"
                    )
                raise RuntimeError("总结元数据持久化失败") from metadata_error

            avg_importance = (
                canonical_importance_total / canonical_count if canonical_count else 0.0
            )
            if canonical_count == 0 and quarantined_count:
                feedback = t(
                    "summarize.quarantined_only",
                    quarantined_count=quarantined_count,
                    count=actual_count,
                )
            elif quarantined_count:
                feedback = t(
                    "summarize.partial_quarantine",
                    canonical_count=canonical_count,
                    quarantined_count=quarantined_count,
                    importance=round(avg_importance, 2),
                    topics=", ".join(all_topics) or t("common.none"),
                    count=actual_count,
                )
            else:
                feedback = t(
                    "summarize.success",
                    canonical_count=canonical_count,
                    importance=round(avg_importance, 2),
                    topics=", ".join(all_topics) or t("common.none"),
                    count=actual_count,
                )
            yield event.plain_result(feedback)

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
        event: AstrMessageEvent,
    ) -> AsyncGenerator[MessageEventResult, None]:
        """处理 /memora help 命令"""
        message = t("help.text")
        yield event.plain_result(message)
