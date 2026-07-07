"""结构化知识存储的知识条目模型。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class KnowledgeType(str, Enum):
    FACT = "fact"
    CONCEPT = "concept"
    RULE = "rule"
    EVENT = "event"
    PROCEDURE = "procedure"


@dataclass(slots=True)
class KnowledgeEntry:
    """从记忆原子中提炼的结构化知识条目。"""

    title: str = ""
    content: str = ""
    category: KnowledgeType = KnowledgeType.FACT
    confidence: float = 0.5
    source_ids: list[int] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    access_count: int = 0
    entry_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "title": self.title,
            "content": self.content,
            "category": self.category.value,
            "confidence": self.confidence,
            "source_ids": self.source_ids,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeEntry:
        return cls(
            title=str(data.get("title", "")),
            content=str(data.get("content", "")),
            category=KnowledgeType(data.get("category", "fact")),
            confidence=float(data.get("confidence", 0.5)),
            source_ids=list(data.get("source_ids", []) or []),
            tags=list(data.get("tags", []) or []),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            expires_at=float(data.get("expires_at", 0.0)),
            access_count=int(data.get("access_count", 0)),
            entry_id=int(data.get("entry_id", 0)),
        )


__all__ = ["KnowledgeEntry", "KnowledgeType"]
