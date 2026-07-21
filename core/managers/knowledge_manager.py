"""知识库生命周期 — 提取、去重、合并、过期清理。"""

from __future__ import annotations

import time as _time

from astrbot.api import logger

from ..base.list_sorting import SortQuery
from ..models.domain_provenance import (
    DomainObjectOrigin,
    DomainProvenance,
    merge_domain_provenance,
)
from ..models.knowledge_models import KnowledgeEntry
from ..storage.knowledge_store import KnowledgeStore


class KnowledgeManager:
    """管理结构化知识：提取、去重、合并、清理。"""

    def __init__(self, store: KnowledgeStore, dedup_threshold: float = 0.85) -> None:
        """保存 Store 与文本去重阈值。"""

        self._store = store
        self._dedup_threshold = dedup_threshold

    async def add_entry(self, entry: KnowledgeEntry) -> int | None:
        """去重合并或插入知识条目。"""

        existing, _ = await self._store.search(entry.title, limit=5)
        merged_id = await self._merge_duplicate(entry, existing)
        if merged_id is not None:
            return merged_id

        if entry.origin is DomainObjectOrigin.DERIVED:
            candidates = await self._store.search_merge_candidates(
                entry.title,
                limit=5,
            )
            visible_ids = {candidate.entry_id for candidate in existing}
            source_ids = set(entry.source_ids)
            stale_same_source = [
                candidate
                for candidate in candidates
                if candidate.entry_id not in visible_ids
                and candidate.origin is DomainObjectOrigin.DERIVED
                and bool(source_ids.intersection(candidate.source_ids))
            ]
            merged_id = await self._merge_duplicate(entry, stale_same_source)
            if merged_id is not None:
                return merged_id

        return await self._store.insert(entry)

    async def _merge_duplicate(
        self,
        entry: KnowledgeEntry,
        candidates: list[KnowledgeEntry],
    ) -> int | None:
        """把相似候选合并到既有条目，并返回其内部 ID。"""

        for ex in candidates:
            if (
                self._text_similarity(entry.content, ex.content)
                >= self._dedup_threshold
            ):
                ex.confidence = max(ex.confidence, entry.confidence)
                ex.access_count += 1
                ex.tags = list(set(ex.tags + entry.tags))
                if (
                    ex.origin is DomainObjectOrigin.DERIVED
                    and entry.origin is DomainObjectOrigin.DERIVED
                ):
                    if ex.provenance is None or entry.provenance is None:
                        raise ValueError("source_provenance_required")
                    ex.provenance = merge_domain_provenance(
                        ex.provenance,
                        entry.provenance,
                    )
                    ex.source_ids = [
                        source.memory_id for source in ex.provenance.sources
                    ]
                elif (
                    ex.origin is DomainObjectOrigin.DERIVED
                    and entry.origin is DomainObjectOrigin.MANUAL
                ):
                    ex.origin = DomainObjectOrigin.MANUAL
                    ex.provenance = None
                    ex.source_ids = list(entry.source_ids)
                await self._store.update(ex)
                return ex.entry_id
        return None

    async def add_derived_entry(
        self,
        entry: KnowledgeEntry,
        provenance: DomainProvenance,
    ) -> int | None:
        """以受控 proposal 语义写入带 canonical 证据的知识条目。"""

        entry.origin = DomainObjectOrigin.DERIVED
        entry.provenance = provenance
        entry.__post_init__()
        return await self.add_entry(entry)

    async def get_entry(self, entry_id: int) -> KnowledgeEntry | None:
        """读取单条可见知识。"""

        return await self._store.get(entry_id)

    async def search(
        self,
        query: str,
        limit: int = 20,
        category: str = "",
        sort: SortQuery = SortQuery("updated_at", "desc"),
    ) -> tuple[list[KnowledgeEntry], int]:
        """委托 Store 搜索可见知识。"""

        return await self._store.search(
            query,
            limit=limit,
            category=category,
            sort=sort,
        )

    async def list_entries(
        self,
        limit: int = 50,
        offset: int = 0,
        category: str = "",
        sort: SortQuery = SortQuery("updated_at", "desc"),
    ) -> tuple[list[KnowledgeEntry], int]:
        """委托 Store 分页列出可见知识。"""

        return await self._store.list_entries(
            limit=limit,
            offset=offset,
            category=category,
            sort=sort,
        )

    async def update_entry(self, entry: KnowledgeEntry) -> bool:
        """存在性校验通过后更新知识条目。"""

        if not entry.entry_id:
            return False
        existing = await self._store.get(entry.entry_id)
        if existing is None:
            return False
        await self._store.update(entry)
        return True

    async def delete_entry(self, entry_id: int) -> bool:
        """删除指定知识条目。"""

        return await self._store.delete(entry_id)

    async def count(self) -> int:
        """返回知识条目总数。"""

        return await self._store.count()

    async def cleanup_expired(self) -> int:
        """分页删除已超过过期时间的知识条目。"""

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
            logger.info("[知识库] 已清理过期条目，数量=%s", removed)
        return removed

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """计算两个文本词集合的 Jaccard 相似度。"""

        set_a = set(a.lower().split())
        set_b = set(b.lower().split())
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)


__all__ = ["KnowledgeManager"]
