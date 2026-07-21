"""笔记生命周期 — CRUD、自动创建触发器、索引。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ..models.domain_provenance import DomainObjectOrigin, DomainProvenance
from ..models.note_models import Note, NoteStatus, NoteVersion
from ..storage.note_store import NoteStore


class NoteManager:
    """管理人工笔记与带 canonical 证据的派生笔记。"""

    def __init__(self, store: NoteStore, max_versions: int = 20) -> None:
        """保存 Store 与每条笔记的版本保留上限。"""

        self._store = store
        self._max_versions = max_versions

    async def create_note(
        self, title: str, content: str, tags: list[str] | None = None, user_id: str = ""
    ) -> int:
        """创建人工笔记。"""

        note = Note(title=title, content=content, tags=tags or [], user_id=user_id)
        note_id = await self._store.create(note)
        logger.debug("[笔记] 人工笔记创建完成")
        return note_id

    async def create_derived_note(
        self,
        title: str,
        content: str,
        provenance: DomainProvenance,
        *,
        tags: list[str] | None = None,
        user_id: str = "",
    ) -> int:
        """通过受控来源证据创建自动派生笔记。"""

        note = Note(
            title=title,
            content=content,
            tags=tags or [],
            user_id=user_id,
            origin=DomainObjectOrigin.DERIVED,
            provenance=provenance,
        )
        return await self._store.create(note)

    async def get_note(self, note_id: int) -> Note | None:
        """读取单条可见笔记。"""

        return await self._store.get(note_id)

    async def update_note(self, note_id: int, **fields: Any) -> Note | None:
        """更新人工或来源仍有效的笔记。"""

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
        """优先软删除笔记，兼容不支持软删除的 Store。"""

        soft_delete = (
            getattr(self._store, "soft_delete", None)
            if hasattr(type(self._store), "soft_delete")
            else None
        )
        if soft_delete is not None:
            return await soft_delete(note_id)
        return await self._store.delete(note_id)

    async def search(self, query: str, limit: int = 20) -> tuple[list[Note], int]:
        """搜索可见笔记。"""

        return await self._store.search(query, limit=limit)

    async def list_notes(
        self, limit: int = 50, offset: int = 0, status: str = ""
    ) -> tuple[list[Note], int]:
        """分页列出可见笔记。"""

        return await self._store.list_notes(limit=limit, offset=offset, status=status)

    async def get_versions(self, note_id: int) -> list[NoteVersion]:
        """返回笔记版本历史。"""

        return await self._store.get_versions(note_id)

    async def count(self) -> int:
        """返回未删除笔记总数。"""

        return await self._store.count()

    async def prune_versions(self, max_versions: int = 20) -> int:
        """裁剪过旧的笔记版本。"""

        return await self._store.prune_versions(max_versions)

    async def auto_create_from_memory(
        self,
        content: str,
        source_memory_ids: list[int] | None = None,
        user_id: str = "",
        provenance: DomainProvenance | None = None,
    ) -> int | None:
        """将足够长的内容转换为人工兼容或显式派生笔记。"""

        if len(content) < 50:
            return None
        if source_memory_ids and provenance is None:
            raise ValueError("source_provenance_required")
        if source_memory_ids and provenance is not None:
            provenance_ids = [source.memory_id for source in provenance.sources]
            if list(source_memory_ids) != provenance_ids:
                raise ValueError("source_ids_provenance_mismatch")
        lines = content.strip().split("\n")
        title = lines[0][:80] if lines else content[:80]
        body = content if len(lines) <= 1 else "\n".join(lines[1:])
        if provenance is not None:
            return await self.create_derived_note(
                title=title,
                content=body,
                tags=["auto-generated"],
                user_id=user_id,
                provenance=provenance,
            )
        return await self.create_note(
            title=title, content=body, tags=["auto-generated"], user_id=user_id
        )


__all__ = ["NoteManager"]
