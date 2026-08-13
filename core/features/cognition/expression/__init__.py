"""表达模式学习子系统。

基于规则、零 LLM 成本地提取用户→Bot 消息序列中的
(situation, expression) 对话对。

公共 API
--------
- :class:`ExpressionPattern` — 已学习模式的 dataclass
- :class:`PatternScope` — 三维作用域键
- :class:`GroupState` — 按群组维护的学习状态
- :class:`ExpressionPatternLearner` — 核心学习引擎
- :class:`ExpressionPatternStore` — aiosqlite 持久化层
"""

from .models import ExpressionPattern, GroupState, PatternScope
from .pattern_learner import ExpressionPatternLearner
from .pattern_store import ExpressionPatternStore

__all__ = [
    "ExpressionPattern",
    "ExpressionPatternLearner",
    "ExpressionPatternStore",
    "GroupState",
    "PatternScope",
]
