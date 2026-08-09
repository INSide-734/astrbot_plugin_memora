"""知识库生命周期 — 提取、去重、合并、过期清理。"""

from __future__ import annotations

import time as _time

from astrbot.api import logger

from core.base.list_sorting import SortQuery
from core.features.knowledge.contracts import KnowledgeStorePort
from core.features.knowledge.domain.models import KnowledgeEntry
from core.models.domain_provenance import (
    DomainObjectOrigin,
    DomainProvenance,
    merge_domain_provenance,
)


class KnowledgeManager:
    """管理结构化知识：提取、去重、合并、清理。"""

    def __init__(
        self,
        store: KnowledgeStorePort,
        dedup_threshold: float = 0.85,
        expire_days: int = 365,
    ) -> None:
        """保存 Store、去重阈值和派生知识过期策略。"""

        self._store = store
        self._dedup_threshold = max(0.0, min(1.0, float(dedup_threshold)))
        self._expire_days = max(0, int(expire_days))

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

        if (
            entry.origin is DomainObjectOrigin.DERIVED
            and self._expire_days > 0
            and entry.expires_at <= 0
        ):
            entry.expires_at = _time.time() + self._expire_days * 86400.0
        return await self._store.insert(entry)

    async def _merge_duplicate(
        self,
        entry: KnowledgeEntry,
        candidates: list[KnowledgeEntry],
    ) -> int | None:
        """把相似候选合并到既有条目，并返回其内部 ID。"""

        for ex in candidates:
            if not self._merge_compatible(ex, entry):
                continue
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

    @staticmethod
    def _merge_compatible(
        existing: KnowledgeEntry,
        incoming: KnowledgeEntry,
    ) -> bool:
        """判断两个条目是否拥有可合并的人工/来源权威。"""

        if (
            existing.origin is DomainObjectOrigin.MANUAL
            and incoming.origin is DomainObjectOrigin.DERIVED
        ):
            return False
        if (
            existing.origin is DomainObjectOrigin.DERIVED
            and incoming.origin is DomainObjectOrigin.DERIVED
        ):
            if existing.provenance is None or incoming.provenance is None:
                return False
            return (
                existing.provenance.scope_key == incoming.provenance.scope_key
                and existing.provenance.privacy_level
                == incoming.provenance.privacy_level
            )
        return True

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

    async def get_entry_for_scope(
        self,
        entry_id: int,
        *,
        scope_key: str,
    ) -> KnowledgeEntry | None:
        """按来源作用域读取知识，拒绝其他会话的派生正文。"""

        entry = await self._store.get(entry_id)
        return entry if self._entry_visible_in_scope(entry, scope_key) else None

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

    async def search_for_scope(
        self,
        query: str,
        *,
        scope_key: str,
        limit: int = 20,
        category: str = "",
        sort: SortQuery = SortQuery("updated_at", "desc"),
    ) -> tuple[list[KnowledgeEntry], int]:
        """按来源作用域搜索知识，拒绝其他会话的派生正文。"""

        entries, _ = await self._store.search(
            query,
            limit=limit,
            category=category,
            sort=sort,
        )
        visible = [
            entry for entry in entries if self._entry_visible_in_scope(entry, scope_key)
        ]
        return visible, len(visible)

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
    def _entry_visible_in_scope(
        entry: KnowledgeEntry | None,
        scope_key: str,
    ) -> bool:
        """判断知识是否可在当前会话读取；人工知识作为管理员维护的全局条目保留。"""

        if entry is None:
            return False
        if entry.origin is DomainObjectOrigin.MANUAL:
            return True
        return bool(
            scope_key
            and entry.provenance is not None
            and entry.provenance.scope_key == scope_key
        )

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """计算两个文本词集合的 Jaccard 相似度。"""

        set_a = set(a.lower().split())
        set_b = set(b.lower().split())
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)


__all__ = ["KnowledgeManager"]
