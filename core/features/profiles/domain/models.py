"""用于个性化记忆检索的用户画像与标签模型。"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ....shared.domain_provenance import DomainProvenance


class TagCategory(str, Enum):
    """用户标签分类。"""

    INTEREST = "interest"  # 兴趣爱好
    PERSONALITY = "personality"  # 性格特征
    HABIT = "habit"  # 行为习惯
    RELATION = "relation"  # 关系标签（如“女儿”“同事”）
    KNOWLEDGE = "knowledge"  # 知识领域
    PREFERENCE = "preference"  # 偏好（回复风格、话题偏好等）
    CUSTOM = "custom"  # 自定义


@dataclass(slots=True)
class UserTag:
    """附加在用户画像上的单个标签，带有置信度评分。"""

    category: TagCategory = TagCategory.CUSTOM
    value: str = ""
    confidence: float = 0.5
    source: str = "auto"
    created_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    occurrence_count: int = 1
    provenance: DomainProvenance | None = None

    def to_dict(self) -> dict[str, Any]:
        """把标签转换为 JSON 安全映射。"""

        data = {
            "category": self.category.value,
            "value": self.value,
            "confidence": self.confidence,
            "source": self.source,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "occurrence_count": self.occurrence_count,
        }
        if self.provenance is not None:
            data["provenance"] = self.provenance.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserTag:
        """从标签映射恢复模型，并兼容没有 provenance 的旧数据。"""

        provenance_data = data.get("provenance")
        return cls(
            category=TagCategory(data.get("category", "custom")),
            value=str(data.get("value", "")),
            confidence=float(data.get("confidence", 0.5)),
            source=str(data.get("source", "auto")),
            created_at=float(data.get("created_at", time.time())),
            last_seen_at=float(data.get("last_seen_at", time.time())),
            occurrence_count=int(data.get("occurrence_count", 1)),
            provenance=(
                DomainProvenance.from_dict(provenance_data)
                if isinstance(provenance_data, dict)
                else None
            ),
        )


@dataclass(slots=True)
class UserPreferences:
    """学习到的用户偏好，影响检索与回复。"""

    reply_style: str = "casual"
    preferred_topics: list[str] = field(default_factory=list)
    avoided_topics: list[str] = field(default_factory=list)
    active_hours: list[int] = field(default_factory=list)
    avg_reply_length: int = 0
    interaction_frequency: float = 0.0
    provenance: DomainProvenance | None = None

    def to_dict(self) -> dict[str, Any]:
        """把偏好转换为 JSON 安全映射。"""

        data = {
            "reply_style": self.reply_style,
            "preferred_topics": self.preferred_topics,
            "avoided_topics": self.avoided_topics,
            "active_hours": self.active_hours,
            "avg_reply_length": self.avg_reply_length,
            "interaction_frequency": self.interaction_frequency,
        }
        if self.provenance is not None:
            data["provenance"] = self.provenance.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> UserPreferences:
        """从偏好映射恢复模型，并兼容没有 provenance 的旧数据。"""

        if not data:
            return cls()
        provenance_data = data.get("provenance")
        return cls(
            reply_style=str(data.get("reply_style", "casual")),
            preferred_topics=list(data.get("preferred_topics", []) or []),
            avoided_topics=list(data.get("avoided_topics", []) or []),
            active_hours=list(data.get("active_hours", []) or []),
            avg_reply_length=int(data.get("avg_reply_length", 0)),
            interaction_frequency=float(data.get("interaction_frequency", 0.0)),
            provenance=(
                DomainProvenance.from_dict(provenance_data)
                if isinstance(provenance_data, dict)
                else None
            ),
        )


@dataclass(slots=True)
class UserProfile:
    """基于对话分析构建的用户动态画像。"""

    user_id: str = ""
    display_name: str = ""
    tags: list[UserTag] = field(default_factory=list)
    preferences: UserPreferences = field(default_factory=UserPreferences)
    total_messages: int = 0
    total_sessions: int = 0
    first_seen_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def get_tags_by_category(self, category: TagCategory) -> list[UserTag]:
        """按置信度降序返回指定分类标签。"""

        matching = [t for t in self.tags if t.category == category]
        matching.sort(key=lambda t: t.confidence, reverse=True)
        return matching

    def get_top_tags(self, limit: int = 10) -> list[UserTag]:
        """返回置信度最高的有限标签列表。"""

        sorted_tags = sorted(self.tags, key=lambda t: t.confidence, reverse=True)
        return sorted_tags[:limit]

    def get_tag_values(self) -> list[str]:
        """返回达到最低置信度的标签文本。"""

        return [t.value for t in self.tags if t.confidence >= 0.3]

    def get_weight_vector(self) -> dict[str, float]:
        """根据置信度和出现次数生成个性化权重。"""

        weights: dict[str, float] = {}
        for tag in self.tags:
            if tag.confidence < 0.2:
                continue
            weight = tag.confidence * min(1.0, tag.occurrence_count / 10.0)
            weights[tag.value] = round(weight, 4)
        return weights

    def upsert_tag(self, new_tag: UserTag) -> bool:
        """按分类和值合并标签；新增时返回 ``True``。"""

        for existing in self.tags:
            if (
                existing.category == new_tag.category
                and existing.value == new_tag.value
            ):
                existing.confidence = max(existing.confidence, new_tag.confidence)
                existing.last_seen_at = new_tag.last_seen_at
                existing.occurrence_count += 1
                return False
        self.tags.append(new_tag)
        return True

    def decay_tags(self, reference_time: float | None = None) -> None:
        """按照最近观察时间衰减全部标签置信度。"""

        ref = reference_time or time.time()
        for tag in self.tags:
            days_since = max(0.0, (ref - tag.last_seen_at) / 86400.0)
            decay = math.exp(-math.log(2) * days_since / 30.0)
            tag.confidence = round(tag.confidence * decay, 4)

    def remove_stale_tags(self, min_confidence: float = 0.1) -> int:
        """删除低于阈值的标签并返回删除数量。"""

        before = len(self.tags)
        self.tags = [t for t in self.tags if t.confidence >= min_confidence]
        return before - len(self.tags)

    def to_dict(self) -> dict[str, Any]:
        """把完整画像转换为 JSON 安全映射。"""

        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "tags": [t.to_dict() for t in self.tags],
            "preferences": self.preferences.to_dict(),
            "total_messages": self.total_messages,
            "total_sessions": self.total_sessions,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserProfile:
        """从画像映射恢复模型。"""

        tags_data = data.get("tags", []) or []
        return cls(
            user_id=str(data.get("user_id", "")),
            display_name=str(data.get("display_name", "")),
            tags=[UserTag.from_dict(t) for t in tags_data],
            preferences=UserPreferences.from_dict(data.get("preferences")),
            total_messages=int(data.get("total_messages", 0)),
            total_sessions=int(data.get("total_sessions", 0)),
            first_seen_at=float(data.get("first_seen_at", time.time())),
            last_seen_at=float(data.get("last_seen_at", time.time())),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
        )


__all__ = [
    "UserProfile",
    "UserTag",
    "UserPreferences",
    "TagCategory",
]
