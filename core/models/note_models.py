"""支持版本历史的笔记模型。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NoteStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


@dataclass(slots=True)
class NoteVersion:
    version: int = 1
    content: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class Note:
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

    def to_dict(self) -> dict[str, Any]:
        return {
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Note:
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
        )


__all__ = ["Note", "NoteVersion", "NoteStatus"]
