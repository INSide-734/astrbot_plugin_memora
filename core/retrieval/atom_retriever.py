"""时间感知的记忆原子检索器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models.memory_atom import MemoryAtom
from ..storage.atom_store import AtomStore


@dataclass(slots=True)
class AtomRetrievalResult:
    """带时间衰减评分的单条原子检索结果。"""

    atom_id: int
    parent_memory_id: int
    content: str
    base_score: float  # BM25 or vector similarity
    temporal_score: float  # decay multiplier
    final_score: float  # base_score * temporal_score
    atom_type: str
    importance: float
    confidence: float
    ttl_days: float
    decay_type: str
    metadata: dict[str, Any]


class AtomRetriever:
    """带时间感知评分的记忆原子检索。

    原子按 base_score * temporal_score 排序，使语义相关性和时间新鲜度均参与排名。
    """

    def __init__(
        self,
        atom_store: AtomStore,
        config: dict[str, Any] | None = None,
    ):
        self.atom_store = atom_store
        self.config = config or {}

    async def search(
        self,
        query: str,
        k: int = 10,
        session_id: str | None = None,
        persona_id: str | None = None,
    ) -> list[AtomRetrievalResult]:
        """通过全文检索搜索原子，按相关性和时间衰减评分。"""
        atoms = await self.atom_store.search_fts(
            query=query,
            limit=max(k * 2, k),
            session_id=session_id,
            persona_id=persona_id,
        )

        results: list[AtomRetrievalResult] = []
        for atom in atoms:
            base_score = float(atom.metadata.get("bm25_score", 0.5))
            temporal_score = float(atom.metadata.get("temporal_score", 1.0))
            final_score = base_score * temporal_score
            results.append(
                AtomRetrievalResult(
                    atom_id=atom.atom_id,
                    parent_memory_id=atom.parent_memory_id,
                    content=atom.content,
                    base_score=round(base_score, 4),
                    temporal_score=round(temporal_score, 4),
                    final_score=round(final_score, 4),
                    atom_type=atom.atom_type.value,
                    importance=round(atom.importance, 4),
                    confidence=round(atom.confidence, 4),
                    ttl_days=round(atom.ttl_days, 2),
                    decay_type=atom.decay_type.value,
                    metadata=dict(atom.metadata),
                )
            )

        results.sort(key=lambda r: r.final_score, reverse=True)
        return results[:k]

    async def get_atoms_for_memory(self, parent_memory_id: int) -> list[MemoryAtom]:
        """返回属于某条父记忆的所有原子。"""
        return await self.atom_store.get_by_parent(parent_memory_id)

    async def touch(self, atom_id: int) -> None:
        """更新原子的访问时间。"""
        await self.atom_store.touch(atom_id)


__all__ = ["AtomRetriever", "AtomRetrievalResult"]
