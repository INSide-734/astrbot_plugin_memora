"""笔记生命周期 — CRUD、自动创建触发器、索引。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ..models.note_models import Note, NoteStatus, NoteVersion
from ..storage.note_store import NoteStore


class NoteManager:
    def __init__(self, store: NoteStore, max_versions: int = 20) -> None:
        self._store = store
        self._max_versions = max_versions

    async def create_note(
        self, title: str, content: str, tags: list[str] | None = None, user_id: str = ""
    ) -> int:
        note = Note(title=title, content=content, tags=tags or [], user_id=user_id)
        note_id = await self._store.create(note)
        logger.debug(f"[Note] created id={note_id}: {title[:50]}")
        return note_id

    async def get_note(self, note_id: int) -> Note | None:
        return await self._store.get(note_id)

    async def update_note(self, note_id: int, **fields: Any) -> Note | None:
        note = await self._store.get(note_id)
        if note is None:
            return None
        if "title" in fields:
            note.title = str(fields["title"])
        if "content" in fields:
            note.content = str(fields["content"])
        if "tags" in fields:
            note.tags = list(fields["tags"] or [])
        if "status" in fields:
            note.status = NoteStatus(str(fields["status"]))
        updated = await self._store.update(note)
        if updated is False:
            return None
        await self._store.prune_versions(self._max_versions)
        return note

    async def delete_note(self, note_id: int) -> bool:
        soft_delete = (
            getattr(self._store, "soft_delete", None)
            if hasattr(type(self._store), "soft_delete")
            else None
        )
        if soft_delete is not None:
            return await soft_delete(note_id)
        return await self._store.delete(note_id)

    async def search(self, query: str, limit: int = 20) -> tuple[list[Note], int]:
        return await self._store.search(query, limit=limit)

    async def list_notes(
        self, limit: int = 50, offset: int = 0, status: str = ""
    ) -> tuple[list[Note], int]:
        return await self._store.list_notes(limit=limit, offset=offset, status=status)

    async def get_versions(self, note_id: int) -> list[NoteVersion]:
        return await self._store.get_versions(note_id)

    async def count(self) -> int:
        return await self._store.count()

    async def prune_versions(self, max_versions: int = 20) -> int:
        return await self._store.prune_versions(max_versions)

    async def auto_create_from_memory(
        self,
        content: str,
        source_memory_ids: list[int] | None = None,
        user_id: str = "",
    ) -> int | None:
        if len(content) < 50:
            return None
        lines = content.strip().split("\n")
        title = lines[0][:80] if lines else content[:80]
        body = content if len(lines) <= 1 else "\n".join(lines[1:])
        return await self.create_note(
            title=title, content=body, tags=["auto-generated"], user_id=user_id
        )


__all__ = ["NoteManager"]
