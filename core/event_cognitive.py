"""事件处理器的可选认知组件投喂辅助。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from astrbot.api import logger

from .features.identity.domain.models import ResolvedIdentity

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent


class CognitiveComponentsMixin:
    """向可选认知组件投喂群聊消息。"""

    _jargon_filter: Any | None
    _jargon_miner: Any | None
    _expression_learner: Any | None
    _relation_manager: Any | None

    @staticmethod
    def _user_id_for_identity(
        event: AstrMessageEvent,
        identity: ResolvedIdentity,
    ) -> str | None:
        """由宿主选择可用于认知状态的用户标识。"""
        raise NotImplementedError

    async def _feed_cognitive_components(
        self,
        event: AstrMessageEvent,
        content: str,
        identity: ResolvedIdentity,
    ) -> None:
        """尽力向可选的认知模块投喂输入数据。"""
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
