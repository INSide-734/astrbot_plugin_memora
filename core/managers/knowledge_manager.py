"""知识库生命周期 — 提取、去重、合并、过期清理。"""

from __future__ import annotations

import time as _time

from astrbot.api import logger

from ..models.knowledge_models import KnowledgeEntry
from ..storage.knowledge_store import KnowledgeStore


class KnowledgeManager:
    """管理结构化知识：提取、去重、合并、清理。"""

    def __init__(self, store: KnowledgeStore, dedup_threshold: float = 0.85) -> None:
        self._store = store
        self._dedup_threshold = dedup_threshold

    async def add_entry(self, entry: KnowledgeEntry) -> int | None:
        existing, _ = await self._store.search(entry.title, limit=5)
        for ex in existing:
            if (
                self._text_similarity(entry.content, ex.content)
                >= self._dedup_threshold
            ):
                ex.confidence = max(ex.confidence, entry.confidence)
                ex.access_count += 1
                ex.tags = list(set(ex.tags + entry.tags))
                await self._store.update(ex)
                return ex.entry_id
        return await self._store.insert(entry)

    async def get_entry(self, entry_id: int) -> KnowledgeEntry | None:
        return await self._store.get(entry_id)

    async def search(
        self, query: str, limit: int = 20, category: str = ""
    ) -> tuple[list[KnowledgeEntry], int]:
        return await self._store.search(query, limit=limit, category=category)

    async def list_entries(
        self, limit: int = 50, offset: int = 0, category: str = ""
    ) -> tuple[list[KnowledgeEntry], int]:
        return await self._store.list_entries(
            limit=limit, offset=offset, category=category
        )

    async def update_entry(self, entry: KnowledgeEntry) -> bool:
        if not entry.entry_id:
            return False
        existing = await self._store.get(entry.entry_id)
        if existing is None:
            return False
        await self._store.update(entry)
        return True

    async def delete_entry(self, entry_id: int) -> bool:
        return await self._store.delete(entry_id)

    async def count(self) -> int:
        return await self._store.count()

    async def cleanup_expired(self) -> int:
        now = _time.time()
        removed = 0
        offset = 0
        page_size = 100
        while True:
            entries, _ = await self._store.list_entries(limit=page_size, offset=offset)
            if not entries:
                break
            for entry in entries:
                if 0 < entry.expires_at < now:
                    await self._store.delete(entry.entry_id)
                    removed += 1
            offset += page_size
        if removed:
            logger.info(f"[KB] cleaned {removed} expired entries")
        return removed

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        set_a = set(a.lower().split())
        set_b = set(b.lower().split())
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)


__all__ = ["KnowledgeManager"]
