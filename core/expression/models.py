"""表达模式子系统的数据模型。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class ExpressionPattern:
    """一条已学习到的表达模式：由触发情境与 Bot 回复构成。"""

    situation: str          # 触发情境（用户消息，截断到 50 字符）
    expression: str          # Bot 回复表达（截断到 100 字符）
    group_id: str            # 来源群组 ID
    persona_id: str          # Bot 人设 ID
    user_id: str | None = None  # 可选用户级作用域；None 表示群组级模式
    weight: float = 1.0      # 权重（重复出现时递增）
    usage_count: int = 0     # 该模式被使用的次数
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    decayed_at: float = field(default_factory=time.time)

    # 内部主键，插入后回填
    pattern_id: int = 0


@dataclass(slots=True, frozen=True)
class PatternScope:
    """表达模式的三维作用域键：``(group_id, persona_id, user_id)``。"""

    group_id: str
    persona_id: str
    user_id: str | None = None

    def to_key(self) -> str:
        """将作用域渲染为字典查找使用的字符串键。"""
        return f"{self.group_id}:{self.persona_id}:{self.user_id or 'group-level'}"


@dataclass
class GroupState:
    """群组级学习状态：缓存消息与最近一次学习时间。"""

    group_id: str
    message_buffer: list[dict] = field(default_factory=list)
    last_learning_at: float = 0.0
    message_count_since_last_learn: int = 0


__all__ = ["ExpressionPattern", "PatternScope", "GroupState"]
