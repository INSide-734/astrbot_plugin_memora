"""Memory Evolution 候选 source 的有界只读选择。"""

from __future__ import annotations

from ..domain.models import MemorySourceRef


class MemoryEvolutionCandidateSourceMixin:
    """为派生候选生成器提供同 scope 的近期 canonical 快照。"""

    async def load_candidate_sources(
        self,
        primary: MemorySourceRef,
        *,
        limit: int = 6,
        max_content_chars: int = 4_000,
    ) -> list[MemorySourceRef]:
        """读取有界候选并保持 primary 位于首项。"""

        bounded_limit = max(1, min(int(limit), 32))
        if bounded_limit == 1:
            return [primary]
        rows = await self._fetch_all(
            """SELECT id FROM documents
               WHERE id != ?
                 AND CASE
                       WHEN json_valid(metadata) THEN COALESCE(
                         NULLIF(json_extract(metadata, '$.scope_key'), ''),
                         NULLIF(json_extract(metadata, '$.session_id'), ''),
                         NULLIF(json_extract(metadata, '$.persona_id'), ''),
                         'private:default'
                       )
                       ELSE 'private:default'
                     END = ?
               ORDER BY id DESC LIMIT ?""",
            (primary.memory_id, primary.scope_key, bounded_limit - 1),
        )
        candidate_ids = (primary.memory_id, *(int(row["id"]) for row in rows))
        loaded = await self.load_sources(
            candidate_ids,
            max_content_chars=max_content_chars,
        )
        selected = [primary]
        for source in loaded:
            if source.memory_id == primary.memory_id:
                continue
            if source.scope_key != primary.scope_key:
                continue
            selected.append(source)
            if len(selected) >= bounded_limit:
                break
        return selected


__all__ = ["MemoryEvolutionCandidateSourceMixin"]
