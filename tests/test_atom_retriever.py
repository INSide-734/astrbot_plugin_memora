"""AtomRetriever 测试 — 时间感知的原子级检索。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeAtomType(Enum):
    EPISODIC = "episodic"
    FACTUAL = "factual"
    PREFERENCE = "preference"


class _FakeDecayType(Enum):
    EXPONENTIAL = "exponential"
    LINEAR = "linear"


@dataclass
class _FakeAtom:
    atom_id: int
    parent_memory_id: int
    content: str
    importance: float
    confidence: float
    ttl_days: float
    atom_type: _FakeAtomType
    decay_type: _FakeDecayType
    metadata: dict[str, Any]


class TestAtomRetriever:

    @pytest.fixture
    def atom_store(self) -> AsyncMock:
        store = AsyncMock()
        store.search_fts = AsyncMock()
        store.get_by_parent = AsyncMock()
        store.touch = AsyncMock()
        return store

    @pytest.fixture
    def retriever(self, atom_store: AsyncMock) -> Any:
        from core.retrieval.atom_retriever import AtomRetriever
        return AtomRetriever(atom_store=atom_store)

    @pytest.mark.asyncio
    async def test_search_returns_scored_atoms(self, retriever: Any, atom_store: AsyncMock) -> None:
        """Happy path: search returns atoms with base * temporal scoring."""
        atom_store.search_fts.return_value = [
            _FakeAtom(
                atom_id=1, parent_memory_id=100, content="西湖很美",
                importance=0.8, confidence=0.7, ttl_days=30.0,
                atom_type=_FakeAtomType.EPISODIC,
                decay_type=_FakeDecayType.EXPONENTIAL,
                metadata={"bm25_score": 0.9, "temporal_score": 0.85},
            ),
            _FakeAtom(
                atom_id=2, parent_memory_id=100, content="西湖在杭州",
                importance=0.6, confidence=0.5, ttl_days=180.0,
                atom_type=_FakeAtomType.FACTUAL,
                decay_type=_FakeDecayType.LINEAR,
                metadata={"bm25_score": 0.7, "temporal_score": 1.0},
            ),
        ]
        results = await retriever.search("西湖", k=5)
        assert len(results) == 2
        assert results[0].atom_id == 1  # higher final_score (0.9*0.85=0.765 > 0.7*1.0=0.7)
        assert isinstance(results[0].final_score, float)

    @pytest.mark.asyncio
    async def test_search_default_metadata_scores(self, retriever: Any, atom_store: AsyncMock) -> None:
        """Edge case: atoms missing bm25_score or temporal_score use defaults."""
        atom_store.search_fts.return_value = [
            _FakeAtom(
                atom_id=1, parent_memory_id=100, content="test",
                importance=0.5, confidence=0.5, ttl_days=10.0,
                atom_type=_FakeAtomType.EPISODIC,
                decay_type=_FakeDecayType.EXPONENTIAL,
                metadata={},  # no bm25_score or temporal_score
            ),
        ]
        results = await retriever.search("test", k=3)
        assert len(results) == 1
        # defaults: base_score=0.5, temporal_score=1.0
        assert results[0].base_score == 0.5
        assert results[0].temporal_score == 1.0
        assert results[0].final_score == 0.5 * 1.0

    @pytest.mark.asyncio
    async def test_search_empty_results(self, retriever: Any, atom_store: AsyncMock) -> None:
        """Edge case: no atoms returned from store."""
        atom_store.search_fts.return_value = []
        results = await retriever.search("nonexistent", k=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_limit_respected(self, retriever: Any, atom_store: AsyncMock) -> None:
        """Results are limited to k even if more available."""
        atoms = [
            _FakeAtom(
                atom_id=i, parent_memory_id=100, content=f"fact {i}",
                importance=0.7, confidence=0.6, ttl_days=30.0,
                atom_type=_FakeAtomType.FACTUAL,
                decay_type=_FakeDecayType.EXPONENTIAL,
                metadata={"bm25_score": 0.9 - i * 0.05, "temporal_score": 0.9},
            )
            for i in range(10)
        ]
        atom_store.search_fts.return_value = atoms
        results = await retriever.search("test", k=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_search_passes_session_and_persona(self, retriever: Any, atom_store: AsyncMock) -> None:
        """Filters (session_id, persona_id) are forwarded to the store."""
        atom_store.search_fts.return_value = []
        await retriever.search("query", k=5, session_id="s1", persona_id="p1")
        call_kwargs = atom_store.search_fts.call_args.kwargs
        assert call_kwargs["session_id"] == "s1"
        assert call_kwargs["persona_id"] == "p1"

    @pytest.mark.asyncio
    async def test_get_atoms_for_memory(self, retriever: Any, atom_store: AsyncMock) -> None:
        """get_atoms_for_memory delegates to atom_store.get_by_parent."""
        expected = [
            _FakeAtom(atom_id=1, parent_memory_id=42, content="a", importance=0.5,
                      confidence=0.5, ttl_days=10.0, atom_type=_FakeAtomType.EPISODIC,
                      decay_type=_FakeDecayType.EXPONENTIAL, metadata={}),
        ]
        atom_store.get_by_parent.return_value = expected
        result = await retriever.get_atoms_for_memory(42)
        assert result == expected
        atom_store.get_by_parent.assert_called_once_with(42)

    @pytest.mark.asyncio
    async def test_touch(self, retriever: Any, atom_store: AsyncMock) -> None:
        """touch delegates to atom_store.touch."""
        await retriever.touch(7)
        atom_store.touch.assert_called_once_with(7)
