"""笔记生命周期 — CRUD、自动创建触发器、索引。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from astrbot.api import logger

from ....models.domain_provenance import DomainObjectOrigin, DomainProvenance
from ..contracts import NoteStorePort
from ..domain.models import Note, NoteStatus, NoteVersion


class NoteManager:
    """管理人工笔记与带 canonical 证据的派生笔记。"""

    def __init__(
        self,
        store: NoteStorePort,
        max_versions: int = 20,
        auto_create_min_length: int = 50,
        max_tags: int = 10,
    ) -> None:
        """保存 Store，并冻结版本、自动长度和标签上限。"""

        self._store = store
        self._max_versions = max(1, int(max_versions))
        self._auto_create_min_length = max(1, int(auto_create_min_length))
        self._max_tags = max(0, int(max_tags))

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

    async def get_note_for_scope(
        self,
        note_id: int,
        *,
        scope_key: str,
        user_id: str,
    ) -> Note | None:
        """按会话来源或人工所有者读取笔记，拒绝跨作用域正文。"""

        note = await self._store.get(note_id)
        return note if self._note_visible_in_scope(note, scope_key, user_id) else None

    async def update_note(self, note_id: int, **fields: Any) -> Note | None:
        """更新人工或来源仍有效的笔记。"""

        note = await self._store.get(note_id)
        if note is None:
            return None
        return await self._update_loaded_note(note, **fields)

    async def _update_loaded_note(self, note: Note, **fields: Any) -> Note | None:
        """持久化已完成可见性校验的笔记修改并裁剪旧版本。"""

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

    async def update_note_for_scope(
        self,
        note_id: int,
        *,
        scope_key: str,
        user_id: str,
        **fields: Any,
    ) -> Note | None:
        """只更新当前作用域可见笔记，避免按 ID 修改其他用户记录。"""

        note = await self.get_note_for_scope(
            note_id,
            scope_key=scope_key,
            user_id=user_id,
        )
        if note is None:
            return None
        return await self._update_loaded_note(note, **fields)

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

    async def search_for_scope(
        self,
        query: str,
        *,
        scope_key: str,
        user_id: str,
        limit: int = 20,
    ) -> tuple[list[Note], int]:
        """按会话来源或人工所有者搜索笔记，拒绝跨作用域正文。"""

        notes, _ = await self._store.search(query, limit=limit)
        visible = [
            note
            for note in notes
            if self._note_visible_in_scope(note, scope_key, user_id)
        ]
        return visible, len(visible)

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
        *,
        title: str | None = None,
        note_content: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> int | None:
        """把足够长的 canonical 内容转换为显式派生笔记。

        自动入口必须携带 canonical provenance；人工笔记继续只通过
        ``create_note`` 创建。生成器提供的标题、正文和标签会在 Manager
        边界再次执行长度、类型和数量限制。
        """

        if len(content) < self._auto_create_min_length:
            return None
        if provenance is None:
            raise ValueError("source_provenance_required")
        provenance_ids = [source.memory_id for source in provenance.sources]
        if source_memory_ids and list(source_memory_ids) != provenance_ids:
            raise ValueError("source_ids_provenance_mismatch")
        fallback_title, fallback_body = _fallback_note(content)
        normalized_title = str(title or fallback_title).strip()[:80]
        normalized_body = str(note_content or fallback_body).strip()[:2000]
        normalized_tags = _normalize_tags(
            tags if tags is not None else ["auto-generated"],
            max_tags=self._max_tags,
        )
        if not normalized_title or not normalized_body:
            return None
        return await self.create_derived_note(
            title=normalized_title,
            content=normalized_body,
            tags=normalized_tags,
            user_id=user_id,
            provenance=provenance,
        )

    @staticmethod
    def _note_visible_in_scope(
        note: Note | None,
        scope_key: str,
        user_id: str,
    ) -> bool:
        """判断笔记是否属于当前来源会话或人工所有者；空所有者的旧记录拒绝读取。"""

        if note is None:
            return False
        if note.origin is DomainObjectOrigin.DERIVED:
            return bool(
                scope_key
                and note.provenance is not None
                and note.provenance.scope_key == scope_key
            )
        return bool(user_id and note.user_id and note.user_id == user_id)


def _fallback_note(content: str) -> tuple[str, str]:
    """从 canonical 正文生成稳定标题和正文 fallback。"""

    normalized = str(content or "").strip()
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return "", ""
    title = lines[0][:80]
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else normalized
    return title, body or normalized


def _normalize_tags(tags: Sequence[str], *, max_tags: int) -> list[str]:
    """按类型、长度、顺序去重和配置数量限制规范自动标签。"""

    normalized: list[str] = []
    if max_tags <= 0:
        return normalized
    for item in tags:
        if not isinstance(item, str):
            continue
        tag = item.strip()
        if not tag or len(tag) > 64 or tag in normalized:
            continue
        normalized.append(tag)
        if len(normalized) >= max_tags:
            break
    return normalized


__all__ = ["NoteManager"]
