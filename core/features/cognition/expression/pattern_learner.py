"""表达模式学习器：基于规则、零 LLM 的对话对抽取器。"""

from __future__ import annotations

import time
from typing import Any

from .models import ExpressionPattern, GroupState, PatternScope
from .pattern_store import ExpressionPatternStore


class ExpressionPatternLearner:
    """从用户→Bot 邻接消息对中提取表达模式。

    该学习器完全不依赖 LLM，全部抽取逻辑均基于确定性规则：

    1. 按时间顺序遍历消息序列。
    2. 找出相邻的“用户消息 → Bot 回复”配对。
    3. 过滤过短消息、系统消息、链接和 @ 提及。
    4. 创建或更新 :class:`ExpressionPattern`，并递增权重。
    5. 在 15 天窗口内应用二次衰减。
    6. 当单个作用域超出容量上限时，淘汰最低权重模式。
    """

    MAX_PATTERNS_PER_SCOPE = 300
    DECAY_DAYS = 15
    MIN_MESSAGE_LENGTH = 3
    DECAY_MIN = 0.01

    def __init__(
        self,
        store: ExpressionPatternStore,
        bot_id: str = "bot",
        max_patterns_per_scope: int = 300,
        decay_days: int = 15,
        min_message_length: int = 3,
    ) -> None:
        self._store = store
        self._bot_id = bot_id
        self._max_patterns = max_patterns_per_scope
        self._decay_days = decay_days
        self._min_message_length = min_message_length

        # 每个群组各自维护学习状态
        self._group_states: dict[str, GroupState] = {}

    @property
    def bot_id(self) -> str:
        return self._bot_id

    # ---- 对外 API ----------------------------------------------------------

    async def process_messages(
        self,
        messages: list[dict[str, Any]],
        group_id: str,
        persona_id: str = "default",
        user_id: str | None = None,
    ) -> list[ExpressionPattern]:
        """处理一批消息并学习表达模式。"""
        scope = PatternScope(group_id=group_id, persona_id=persona_id, user_id=user_id)

        # 抽取对话对
        pairs = self._extract_dialog_pairs(messages, scope)

        # 将每个对话对写入或更新到存储层
        results: list[ExpressionPattern] = []
        for pair in pairs:
            updated = await self._store.upsert(pair)
            results.append(updated)

        # 对该作用域下的所有模式执行衰减
        await self._apply_decay(scope)

        # 若超出容量上限则执行淘汰
        await self._evict_if_needed(scope)

        return results

    async def get_patterns_for_injection(
        self,
        group_id: str,
        persona_id: str = "default",
        user_id: str | None = None,
        limit: int = 10,
    ) -> list[ExpressionPattern]:
        """获取用于上下文注入的高权重模式列表，按权重降序返回。"""
        scope = PatternScope(group_id=group_id, persona_id=persona_id, user_id=user_id)
        return await self._store.get_top_by_weight(scope, limit=limit)

    async def format_patterns_for_prompt(
        self,
        group_id: str,
        persona_id: str = "default",
        user_id: str | None = None,
        limit: int = 5,
    ) -> str:
        """将已学习模式格式化为可插入 Prompt 的字符串。"""
        patterns = await self.get_patterns_for_injection(
            group_id, persona_id=persona_id, user_id=user_id, limit=limit
        )
        if not patterns:
            return ""

        lines = ["[学习到的表达习惯]"]
        for p in patterns:
            lines.append(
                f"- 当遇到类似「{p.situation}」的情境时，可以回复「{p.expression}」"
            )
        return "\n".join(lines)

    # ---- 内部：对话对抽取 ------------------------------------

    def _extract_dialog_pairs(
        self,
        messages: list[dict[str, Any]],
        scope: PatternScope,
    ) -> list[ExpressionPattern]:
        """遍历消息序列并抽取相邻的用户→Bot 对话对。"""
        pairs: list[ExpressionPattern] = []
        now = time.time()

        for i in range(len(messages) - 1):
            msg = messages[i]
            next_msg = messages[i + 1]

            sender = msg.get("sender_id", "")
            content = (msg.get("content", "") or "").strip()
            next_sender = next_msg.get("sender_id", "")
            next_content = (next_msg.get("content", "") or "").strip()

            # 必须是“用户 → Bot”的相邻配对
            if sender == self._bot_id or next_sender != self._bot_id:
                continue
            if not content or not next_content:
                continue

            # 过滤过短消息
            if len(content) < self._min_message_length:
                continue
            if len(next_content) < self._min_message_length:
                continue

            # 过滤系统风格消息
            if self._is_system_message(content) or self._is_system_message(
                next_content
            ):
                continue

            pairs.append(
                ExpressionPattern(
                    situation=content[:50],
                    expression=next_content[:100],
                    group_id=scope.group_id,
                    persona_id=scope.persona_id,
                    user_id=scope.user_id,
                    weight=1.0,
                    usage_count=0,
                    created_at=now,
                    last_used_at=now,
                    decayed_at=now,
                )
            )

        return pairs

    @staticmethod
    def _is_system_message(content: str) -> bool:
        """判断消息是否像系统消息或自动消息。"""
        if not content:
            return True
        if content.startswith(("[")) or content.startswith("http"):
            return True
        if content.startswith("@"):
            return True
        return False

    # ---- 内部：衰减逻辑 -----------------------------------------------------

    async def _apply_decay(self, scope: PatternScope) -> None:
        """对指定作用域中的所有模式应用二次衰减。"""
        patterns = await self._store.get_all_for_decay(scope)
        if not patterns:
            return

        now = time.time()
        days_window = float(self._decay_days)

        for p in patterns:
            days_elapsed = (now - p.decayed_at) / 86400.0
            if days_elapsed <= 0:
                continue

            decay_factor = (days_elapsed / days_window) ** 2
            decay_factor = min(decay_factor, 1.0)
            new_weight = p.weight * (1.0 - decay_factor)

            if new_weight <= self.DECAY_MIN:
                await self._store.delete_below_weight(scope, self.DECAY_MIN)
            elif abs(new_weight - p.weight) > 0.001:
                await self._store.update_weight(p.pattern_id, new_weight)

    def _calculate_decay_factor(self, days_elapsed: float) -> float:
        """根据经过天数计算衰减系数。"""
        if days_elapsed <= 0:
            return 0.0
        if days_elapsed >= self._decay_days:
            return 1.0
        factor = (days_elapsed / float(self._decay_days)) ** 2
        return min(factor, 1.0)

    # ---- 内部：容量管理 ---------------------------------------

    async def _evict_if_needed(self, scope: PatternScope) -> None:
        """当作用域超出容量上限时，淘汰最低权重的模式。"""
        count = await self._store.count_by_scope(scope)
        if count <= self._max_patterns:
            return

        excess = count - self._max_patterns
        deleted = await self._store.delete_lowest_weight(scope, excess)
        if deleted > 0:
            from astrbot.api import logger

            logger.info(
                f"[ExpressionPatternLearner] 已从作用域 {scope.to_key()} "
                f"淘汰 {deleted} 条模式（上限={self._max_patterns}）"
            )

    # ---- 群组级状态管理 ------------------------------------------

    def get_or_create_state(self, group_id: str) -> GroupState:
        """获取或初始化指定群组的学习状态。"""
        if group_id not in self._group_states:
            self._group_states[group_id] = GroupState(group_id=group_id)
        return self._group_states[group_id]

    def buffer_message(
        self,
        group_id: str,
        sender_id: str,
        content: str,
        timestamp: float | None = None,
    ) -> None:
        """将消息加入群组缓冲区，供后续批量学习使用。"""
        state = self.get_or_create_state(group_id)
        state.message_buffer.append(
            {
                "sender_id": sender_id,
                "content": content,
                "timestamp": timestamp or time.time(),
            }
        )
        state.message_count_since_last_learn += 1

    async def maybe_learn(
        self,
        group_id: str,
        persona_id: str = "default",
        user_id: str | None = None,
        min_messages: int = 5,
    ) -> list[ExpressionPattern]:
        """当缓冲区消息数达到阈值时触发学习。"""
        state = self.get_or_create_state(group_id)
        if state.message_count_since_last_learn < min_messages:
            return []

        messages = list(state.message_buffer)
        # 清空缓冲区
        state.message_buffer.clear()
        state.message_count_since_last_learn = 0
        state.last_learning_at = time.time()

        return await self.process_messages(
            messages, group_id, persona_id=persona_id, user_id=user_id
        )


__all__ = ["ExpressionPatternLearner"]
