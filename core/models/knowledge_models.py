"""结构化知识存储的知识条目模型。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .domain_provenance import DomainObjectOrigin, DomainProvenance


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
    origin: DomainObjectOrigin = DomainObjectOrigin.MANUAL
    provenance: DomainProvenance | None = None

    def __post_init__(self) -> None:
        """规范化来源类型，并要求派生知识具备 canonical 证据。"""

        if not isinstance(self.origin, DomainObjectOrigin):
            self.origin = DomainObjectOrigin(self.origin)
        if self.provenance is not None and self.provenance.origin is not self.origin:
            raise ValueError("domain_origin_mismatch")
        if self.origin is DomainObjectOrigin.DERIVED:
            if self.provenance is None:
                raise ValueError("source_provenance_required")
            source_ids = [source.memory_id for source in self.provenance.sources]
            if self.source_ids and self.source_ids != source_ids:
                raise ValueError("source_ids_provenance_mismatch")
            self.source_ids = source_ids

    def to_dict(self) -> dict[str, Any]:
        """序列化知识条目，并仅为 derived 对象输出内部来源。"""

        result = {
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
        if self.origin is DomainObjectOrigin.DERIVED:
            result["origin"] = self.origin.value
            result["provenance"] = self.provenance.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeEntry:
        """从兼容旧记录的字典恢复知识条目。"""

        raw_provenance = data.get("provenance")
        provenance = (
            DomainProvenance.from_dict(raw_provenance)
            if isinstance(raw_provenance, dict)
            else None
        )
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
            origin=DomainObjectOrigin(data.get("origin", "manual")),
            provenance=provenance,
        )


__all__ = ["KnowledgeEntry", "KnowledgeType"]
