"""准备可由消息捕获或 LLM 响应共同触发的反思窗口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from astrbot.api import logger

from ....utils import get_persona_id
from ...observability.infrastructure.debug_reporter import report_debug_event

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from ....managers.conversation_manager import ConversationManager
    from ....platform.config.manager import ConfigManager


@dataclass(frozen=True, slots=True)
class ReflectionWindowRequest:
    """描述一次已经通过阈值检查、等待后台调度的反思窗口。"""

    session_id: str
    history_messages: list[Any]
    persona_id: str | None
    start_index: int
    end_index: int
    drain_end_index: int
    retry_count: int


@dataclass(frozen=True, slots=True)
class _ReflectionProgress:
    """保存一次阈值检查所需的会话进度快照。"""

    total_messages: int
    last_summarized_index: int
    pending_summary: Any
    trigger_rounds: int


class ReflectionTrigger:
    """统一计算未总结进度并准备反思窗口。"""

    def __init__(
        self,
        context: Any,
        config_manager: ConfigManager,
        conversation_manager: ConversationManager,
    ) -> None:
        """保存窗口计算所需的上下文、配置与会话管理器。"""

        self._context = context
        self._config_manager = config_manager
        self._conversation_manager = conversation_manager

    async def prepare(
        self,
        event: AstrMessageEvent,
        session_id: str,
    ) -> ReflectionWindowRequest | None:
        """在达到总结阈值时读取稳定窗口并返回调度参数。

        Args:
            event: 触发检查的 AstrBot 消息事件，用于解析当前人格。
            session_id: 已校验为非空的统一会话标识。

        Returns:
            可调度的反思窗口；会话不可用、阈值不足或重试耗尽时返回
            ``None``。

        Side Effects:
            发现总结索引越界时修正会话元数据；旧窗口连续失败达到上限时
            按现有兼容语义清除待处理状态并推进索引。
        """

        progress = await self._read_ready_progress(session_id)
        if progress is None:
            return None

        persona_id = await get_persona_id(self._context, event)
        return await self._prepare_request(
            session_id,
            persona_id,
            progress,
            drain_end_index=progress.total_messages,
        )

    async def prepare_for_persona(
        self,
        session_id: str,
        persona_id: str | None,
        *,
        drain_end_index: int | None = None,
    ) -> ReflectionWindowRequest | None:
        """复用已解析人格准备正常触发或指定高水位内的后续窗口。

        Args:
            session_id: 当前统一会话标识。
            persona_id: 首个窗口已经解析的人格标识。
            drain_end_index: 需要连续处理的固定消息高水位；为 ``None`` 时
                重新执行正常阈值检查。

        Returns:
            下一段有界窗口；没有达到阈值或高水位内不足一轮时返回
            ``None``。
        """

        if drain_end_index is None:
            progress = await self._read_ready_progress(session_id)
        else:
            progress = await self._read_progress(session_id)
            if progress is not None:
                progress = _ReflectionProgress(
                    total_messages=min(progress.total_messages, drain_end_index),
                    last_summarized_index=progress.last_summarized_index,
                    pending_summary=progress.pending_summary,
                    trigger_rounds=progress.trigger_rounds,
                )
        if progress is None:
            return None

        return await self._prepare_request(
            session_id,
            persona_id,
            progress,
            drain_end_index=(
                progress.total_messages if drain_end_index is None else drain_end_index
            ),
        )

    async def _prepare_request(
        self,
        session_id: str,
        persona_id: str | None,
        progress: _ReflectionProgress,
        *,
        drain_end_index: int,
    ) -> ReflectionWindowRequest | None:
        """从进度快照读取消息并构造一个有界反思请求。

        Args:
            session_id: 当前统一会话标识。
            persona_id: 当前人格标识。
            progress: 已读取的会话进度快照。
            drain_end_index: 本轮连续处理不得越过的消息高水位。

        Returns:
            可调度的反思窗口；窗口不足一轮或重试耗尽时返回 ``None``。
        """

        window = await self._resolve_window(session_id, progress)
        if window is None:
            return None
        start_index, end_index, retry_count = window

        if end_index - start_index < 2:
            report_debug_event(
                "reflection_state",
                component="reflection",
                stage="summary_window",
                status="skipped",
                reason_code="insufficient_summary_window",
                count=max(0, int(end_index - start_index)),
            )
            logger.debug(f"[{session_id}] 消息数不足一轮对话，跳过总结")
            return None

        rounds_to_summarize = (end_index - start_index) // 2
        logger.info(
            f"[{session_id}] 滑动窗口总结："
            f"消息范围 [{start_index}:{end_index}]/{progress.total_messages}，"
            f"本次总结 {rounds_to_summarize} 轮"
        )

        history_messages = await self._conversation_manager.get_messages_range(
            session_id=session_id,
            start_index=start_index,
            end_index=end_index,
        )
        logger.info(f"[{session_id}] 获取到 {len(history_messages)} 条消息用于总结")
        return ReflectionWindowRequest(
            session_id=session_id,
            history_messages=history_messages,
            persona_id=persona_id,
            start_index=start_index,
            end_index=end_index,
            drain_end_index=drain_end_index,
            retry_count=retry_count,
        )

    async def _read_ready_progress(
        self,
        session_id: str,
    ) -> _ReflectionProgress | None:
        """读取会话进度，并仅在达到配置阈值时返回快照。

        Args:
            session_id: 当前统一会话标识。

        Returns:
            达到阈值的进度快照；会话不可用或阈值不足时返回 ``None``。

        Side Effects:
            会话元数据中的总结索引超过实际消息数时，将其修正到实际末尾。
        """

        progress = await self._read_progress(session_id)
        if progress is None:
            return None

        unsummarized_messages = progress.total_messages - progress.last_summarized_index
        unsummarized_rounds = unsummarized_messages // 2

        logger.info(
            f"[反思处理] [{session_id}] 总消息数：{progress.total_messages}，"
            f"上次总结位置：{progress.last_summarized_index}，"
            f"未总结轮数：{unsummarized_rounds}，"
            f"触发阈值：{progress.trigger_rounds} 轮，"
            f"存在待处理失败总结：{progress.pending_summary is not None}"
        )
        report_debug_event(
            "reflection_state",
            component="reflection",
            stage="summary_gate",
            status=(
                "completed"
                if unsummarized_rounds >= progress.trigger_rounds
                else "skipped"
            ),
            reason_code=(
                "summary_trigger_reached"
                if unsummarized_rounds >= progress.trigger_rounds
                else "summary_threshold_not_reached"
            ),
            count=max(0, int(unsummarized_rounds)),
            threshold_rounds=progress.trigger_rounds,
        )

        if unsummarized_rounds < progress.trigger_rounds:
            return None

        logger.info(
            f"[{session_id}] 未总结轮数达到 {unsummarized_rounds} 轮，启动记忆反思任务"
        )
        return progress

    async def _read_progress(
        self,
        session_id: str,
    ) -> _ReflectionProgress | None:
        """读取会话实际消息数、总结位置、待重试状态和窗口上限。

        Args:
            session_id: 当前统一会话标识。

        Returns:
            可用于窗口计算的进度快照；会话不存在时返回 ``None``。

        Side Effects:
            总结索引超过实际消息数时将其修正到实际末尾。
        """

        session_info = await self._conversation_manager.get_session_info(session_id)
        if not session_info:
            report_debug_event(
                "reflection_state",
                component="reflection",
                stage="session",
                status="skipped",
                reason_code="session_info_unavailable",
            )
            logger.warning(f"[反思处理] [{session_id}] session_info 为空，跳过反思")
            return None

        # sessions 表的计数可能延迟，窗口边界始终以 messages 表实际行数为准。
        actual_message_count = await self._conversation_manager.store.get_message_count(
            session_id
        )
        if session_info.message_count != actual_message_count:
            logger.warning(
                f"[反思处理] [{session_id}] 数据不一致！"
                f"sessions表记录={session_info.message_count}, "
                f"实际消息数={actual_message_count}"
            )

        total_messages = actual_message_count
        trigger_rounds = self._config_manager.get(
            "reflection_engine.summary_trigger_rounds", 10
        )
        last_summarized_index = await self._conversation_manager.get_session_metadata(
            session_id,
            "last_summarized_index",
            0,
        )

        if last_summarized_index > total_messages:
            logger.warning(
                f"[反思处理] [{session_id}] "
                f"last_summarized_index({last_summarized_index}) "
                f"> 实际消息数({total_messages})，调整为当前消息总数"
            )
            last_summarized_index = total_messages
            persisted = await self._conversation_manager.update_session_metadata(
                session_id,
                "last_summarized_index",
                total_messages,
            )
            if persisted is not True:
                report_debug_event(
                    "reflection_state",
                    component="reflection",
                    stage="session",
                    status="degraded",
                    reason_code="summary_cursor_repair_failed",
                )
                logger.error(
                    f"[反思处理] [{session_id}] 修正总结游标未持久化，"
                    "本次仅使用内存修正值"
                )

        pending_summary = await self._conversation_manager.get_session_metadata(
            session_id,
            "pending_summary",
            None,
        )

        return _ReflectionProgress(
            total_messages=total_messages,
            last_summarized_index=last_summarized_index,
            pending_summary=pending_summary,
            trigger_rounds=max(1, int(trigger_rounds)),
        )

    async def _resolve_window(
        self,
        session_id: str,
        progress: _ReflectionProgress,
    ) -> tuple[int, int, int] | None:
        """合并待重试范围并返回本次固定的 index 窗口。

        Args:
            session_id: 当前统一会话标识。
            progress: 已达到阈值的会话进度快照。

        Returns:
            ``(start_index, end_index, retry_count)``；旧窗口重试耗尽并按
            兼容规则处理后返回 ``None``。

        Side Effects:
            已被总结游标覆盖的旧待重试窗口只清理、不重放；重试耗尽时
            原子推进总结索引并清除 ``pending_summary``。必要的持久化失败
            会停止当前续跑并返回 ``None``。
        """

        start_index = progress.last_summarized_index
        end_index = min(
            progress.total_messages,
            start_index + progress.trigger_rounds * 2,
        )
        retry_count = 0

        if progress.pending_summary:
            pending_start = progress.pending_summary.get("start_index", start_index)
            pending_end = progress.pending_summary.get("end_index", end_index)
            retry_count = progress.pending_summary.get("retry_count", 0)
            if pending_end <= progress.last_summarized_index:
                pending_cleared = (
                    await self._conversation_manager.update_session_metadata(
                        session_id,
                        "pending_summary",
                        None,
                    )
                )
                if pending_cleared is not True:
                    report_debug_event(
                        "reflection_state",
                        component="reflection",
                        stage="summary_gate",
                        status="failed",
                        reason_code="stale_pending_clear_failed",
                    )
                    logger.error(
                        f"[{session_id}] 待重试窗口 [{pending_start}:{pending_end}] "
                        "已被总结游标覆盖，但清理状态未持久化；停止本次续跑"
                    )
                    return None

                report_debug_event(
                    "reflection_state",
                    component="reflection",
                    stage="summary_gate",
                    status="completed",
                    reason_code="stale_pending_cleared",
                )
                logger.info(
                    f"[{session_id}] 已清理总结游标覆盖的旧待重试窗口 "
                    f"[{pending_start}:{pending_end}]"
                )
                retry_count = 0
            elif retry_count >= 3:
                report_debug_event(
                    "reflection_state",
                    component="reflection",
                    stage="summary_gate",
                    status="skipped",
                    reason_code="pending_retry_exhausted",
                    count=max(0, int(retry_count)),
                    threshold_rounds=progress.trigger_rounds,
                )
                logger.warning(
                    f"[{session_id}] 待处理总结已连续失败 {retry_count} 次，放弃该范围 "
                    f"[{pending_start}:{pending_end}]"
                )
                metadata_persisted = (
                    await self._conversation_manager.update_session_metadata_fields(
                        session_id,
                        {
                            "last_summarized_index": pending_end,
                            "pending_summary": None,
                        },
                    )
                )
                if metadata_persisted is not True:
                    report_debug_event(
                        "reflection_state",
                        component="reflection",
                        stage="summary_gate",
                        status="failed",
                        reason_code="summary_exhaustion_metadata_failed",
                    )
                    logger.error(
                        f"[{session_id}] 放弃失败窗口时游标与待重试状态未能"
                        "原子提交，保留原窗口"
                    )
                return None
            else:
                start_index = pending_start
                end_index = min(progress.total_messages, pending_end)
                logger.info(
                    f"[{session_id}] 重试原有失败总结范围 "
                    f"[{start_index}:{end_index}]，重试次数：{retry_count + 1}/3"
                )

        return start_index, end_index, retry_count


__all__ = ["ReflectionTrigger", "ReflectionWindowRequest"]
