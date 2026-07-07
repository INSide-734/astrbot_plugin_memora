"""好感度系统 -- 用户好感度评分、Bot 情绪动态与交互分类。"""

from .models import (
    AffectionLevel,
    BotMood,
    INTERACTION_RULES,
    InteractionType,
    MoodType,
    UserAffection,
    classify_by_keywords,
    KEYWORD_INTERACTION_MAP,
)
from .affection_store import AffectionStore
from .affection_manager import AffectionManager, LLMAdapter

__all__ = [
    "AffectionLevel",
    "AffectionManager",
    "AffectionStore",
    "BotMood",
    "INTERACTION_RULES",
    "InteractionType",
    "LLMAdapter",
    "MoodType",
    "UserAffection",
    "classify_by_keywords",
    "KEYWORD_INTERACTION_MAP",
]
