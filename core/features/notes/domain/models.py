"""支持版本历史的笔记模型。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ....shared.domain_provenance import DomainObjectOrigin, DomainProvenance


class NoteStatus(str, Enum):
    """笔记生命周期状态。"""

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


@dataclass(slots=True)
class NoteVersion:
    """笔记正文的历史版本快照。"""

    version: int = 1
    content: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class Note:
    """保存当前笔记、版本和 canonical 来源证据。"""

    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    status: NoteStatus = NoteStatus.ACTIVE
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    note_id: int = 0
    user_id: str = ""
    source_memory_ids: list[int] = field(default_factory=list)
    origin: DomainObjectOrigin = DomainObjectOrigin.MANUAL
    provenance: DomainProvenance | None = None

    def __post_init__(self) -> None:
        """规范化来源类型，并要求派生笔记具备 canonical 证据。"""

        if not isinstance(self.origin, DomainObjectOrigin):
            self.origin = DomainObjectOrigin(self.origin)
        if self.provenance is not None and self.provenance.origin is not self.origin:
            raise ValueError("domain_origin_mismatch")
        if self.origin is DomainObjectOrigin.DERIVED:
            if self.provenance is None:
                raise ValueError("source_provenance_required")
            source_ids = [source.memory_id for source in self.provenance.sources]
            if self.source_memory_ids and self.source_memory_ids != source_ids:
                raise ValueError("source_ids_provenance_mismatch")
            self.source_memory_ids = source_ids

    def to_dict(self) -> dict[str, Any]:
        """序列化笔记，并仅为 derived 对象输出内部来源。"""

        result = {
            "note_id": self.note_id,
            "title": self.title,
            "content": self.content,
            "tags": self.tags,
            "status": self.status.value,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "user_id": self.user_id,
            "source_memory_ids": self.source_memory_ids,
        }
        if self.origin is DomainObjectOrigin.DERIVED:
            result["origin"] = self.origin.value
            result["provenance"] = self.provenance.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Note:
        """从兼容旧记录的字典恢复笔记。"""

        raw_provenance = data.get("provenance")
        provenance = (
            DomainProvenance.from_dict(raw_provenance)
            if isinstance(raw_provenance, dict)
            else None
        )
        return cls(
            title=str(data.get("title", "")),
            content=str(data.get("content", "")),
            tags=list(data.get("tags", []) or []),
            status=NoteStatus(data.get("status", "active")),
            version=int(data.get("version", 1)),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            note_id=int(data.get("note_id", 0)),
            user_id=str(data.get("user_id", "")),
            source_memory_ids=list(data.get("source_memory_ids", []) or []),
            origin=DomainObjectOrigin(data.get("origin", "manual")),
            provenance=provenance,
        )


__all__ = ["Note", "NoteVersion", "NoteStatus"]
