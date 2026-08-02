"""在单个后台任务中按有界窗口连续处理反思积压。"""

from __future__ import annotations

from astrbot.api import logger

from .reflection_trigger import ReflectionWindowRequest


class ReflectionBacklogMixin:
    """为反思处理器提供同会话串行的有界积压续跑能力。"""

    async def _drain_summary_backlog(
        self,
        request: ReflectionWindowRequest,
    ) -> None:
        """依次处理有界窗口，并在提交成功后继续下一段积压。

        Args:
            request: 已通过阈值检查的首个反思窗口。

        Returns:
            无返回值。当前窗口未推进总结索引、固定高水位内不足一轮，
            或正常阈值不再满足时结束后台任务。

        Raises:
            asyncio.CancelledError: 存储或窗口准备被取消时原样向上传播。
        """

        current_request: ReflectionWindowRequest | None = request
        while current_request is not None:
            await self._storage_task(
                current_request.session_id,
                current_request.history_messages,
                current_request.persona_id,
                current_request.start_index,
                current_request.end_index,
                current_request.retry_count,
            )

            summarized_index = await self._conversation_manager.get_session_metadata(
                current_request.session_id,
                "last_summarized_index",
                0,
            )
            try:
                summarized_index = int(summarized_index)
            except (TypeError, ValueError):
                summarized_index = 0

            if summarized_index < current_request.end_index:
                logger.info(
                    f"[{current_request.session_id}] 当前总结窗口未提交，"
                    "停止后台积压续跑并等待后续重试"
                )
                return

            if self._shutting_down:
                logger.info(
                    f"[{current_request.session_id}] 当前总结窗口已提交，"
                    "插件正在关闭，不再继续处理积压"
                )
                return

            if summarized_index < current_request.drain_end_index:
                current_request = await self._summary_trigger.prepare_for_persona(
                    current_request.session_id,
                    current_request.persona_id,
                    drain_end_index=current_request.drain_end_index,
                )
                continue

            return


__all__ = ["ReflectionBacklogMixin"]
