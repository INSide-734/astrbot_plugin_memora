"""Memora 社交关系类型化模块。

在现有图记忆系统 (GraphMemoryManager)
之上叠加显式的关系类型标注层。
"""

from __future__ import annotations

from .models import (
    RELATION_CATEGORIES,
    RELATION_DIFFICULTY,
    RelationChange,
    SocialRelation,
)
from .relation_manager import RelationManager
from .relation_store import RelationStore

__all__ = [
    "RELATION_CATEGORIES",
    "RELATION_DIFFICULTY",
    "RelationChange",
    "RelationManager",
    "RelationStore",
    "SocialRelation",
]
