"""AtomFTSMixin 测试 — 基于FTS5的记忆原子全文搜索。"""

import time

import pytest

from core.features.memory.domain.memory_atom import AtomType, MemoryAtom
from core.features.memory.infrastructure.atom_store import AtomStore


def _make_atom(**overrides) -> MemoryAtom:
    defaults = dict(
        parent_memory_id=1,
        atom_type=AtomType.FACTUAL,
        content="测试记忆内容",
        importance=0.6,
        confidence=0.8,
        session_id="fts-sess",
        persona_id="p1",
    )
    defaults.update(overrides)
    return MemoryAtom(**defaults)  # type: ignore[arg-type]


class TestAtomFTS_search_fts:
    """Full-text search via search_fts method."""

    @pytest.mark.asyncio
    async def test_search_finds_matching_atoms(self, tmp_db_path):
        """FTS returns atoms whose content matches the query."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(_make_atom(content="西湖是杭州最著名的景点"))
        await store.insert(_make_atom(content="今天天气非常好"))
        await store.insert(_make_atom(content="西湖的水很清澈"))

        results = await store.search_fts("西湖", limit=10)
        assert len(results) >= 1
        contents = {r.content for r in results}
        assert "西湖是杭州最著名的景点" in contents

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_empty(self, tmp_db_path):
        """Empty or whitespace-only query returns empty list."""
        store = AtomStore(tmp_db_path)
        await store.initialize()
        await store.insert(_make_atom(content="something"))

        assert await store.search_fts("") == []
        assert await store.search_fts("   ") == []

    @pytest.mark.asyncio
    async def test_search_no_match_returns_empty(self, tmp_db_path):
        """No matching atoms returns empty list."""
        store = AtomStore(tmp_db_path)
        await store.initialize()
        await store.insert(_make_atom(content="西湖"))

        results = await store.search_fts("珠穆朗玛峰", limit=10)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_filters_by_session_id(self, tmp_db_path):
        """search_fts with session_id returns only that session's atoms."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(_make_atom(content="有关sessionA的记忆", session_id="A"))
        await store.insert(_make_atom(content="有关sessionB的记忆", session_id="B"))

        results_a = await store.search_fts("记忆", session_id="A", limit=10)
        assert all(r.session_id == "A" for r in results_a)
        assert len(results_a) >= 1

    @pytest.mark.asyncio
    async def test_search_include_expired(self, tmp_db_path):
        """include_expired=True returns expired atoms as well."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        atom_id = await store.insert(_make_atom(content="过期记忆"))
        async with store._connect() as db:
            await db.execute(
                "UPDATE memory_atoms SET status = 'expired', expires_at = ? WHERE id = ?",
                (time.time() - 100, atom_id),
            )
            await db.commit()

        results_no_expired = await store.search_fts("过期", include_expired=False)
        expired_contents = {r.content for r in results_no_expired}
        assert "过期记忆" not in expired_contents

        results_with_expired = await store.search_fts("过期", include_expired=True)
        expired_contents_2 = {r.content for r in results_with_expired}
        assert "过期记忆" in expired_contents_2

    @pytest.mark.asyncio
    async def test_search_scores_are_normalized(self, tmp_db_path):
        """Returned atoms have bm25_score and temporal_score in metadata."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(_make_atom(content="记忆测试一"))
        await store.insert(_make_atom(content="记忆测试二"))

        results = await store.search_fts("记忆", limit=10)
        assert len(results) >= 1
        for atom in results:
            assert "bm25_score" in atom.metadata
            assert "temporal_score" in atom.metadata
            assert 0.0 <= float(atom.metadata["bm25_score"]) <= 1.0

    @pytest.mark.asyncio
    async def test_search_filters_by_persona_id(self, tmp_db_path):
        """search_fts with persona_id returns only matching atoms."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(_make_atom(content="人格A记忆内容", persona_id="pa"))
        await store.insert(_make_atom(content="人格B记忆内容", persona_id="pb"))

        results = await store.search_fts("记忆", persona_id="pa", limit=10)
        assert len(results) >= 1
        assert all(r.persona_id == "pa" for r in results)

    @pytest.mark.asyncio
    async def test_search_with_session_and_persona(self, tmp_db_path):
        """search_fts with both session_id and persona_id."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(
            _make_atom(content="匹配记忆", session_id="s1", persona_id="p1")
        )
        await store.insert(
            _make_atom(content="不匹配记忆1", session_id="s2", persona_id="p1")
        )
        await store.insert(
            _make_atom(content="不匹配记忆2", session_id="s1", persona_id="p2")
        )

        results = await store.search_fts(
            "记忆", session_id="s1", persona_id="p1", limit=10
        )
        assert len(results) == 1
        assert results[0].content == "匹配记忆"


class TestAtomFTS_search_fts_by_type:
    """Type-filtered FTS search via search_fts_by_type."""

    @pytest.mark.asyncio
    async def test_filter_by_atom_types(self, tmp_db_path):
        """search_fts_by_type filters by provided atom_types."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(_make_atom(content="事实记忆", atom_type=AtomType.FACTUAL))
        await store.insert(_make_atom(content="事件记忆", atom_type=AtomType.EPISODIC))
        await store.insert(
            _make_atom(content="偏好记忆", atom_type=AtomType.PREFERENCE)
        )

        results = await store.search_fts_by_type(
            "记忆", atom_types=["factual", "episodic"], limit=10
        )
        types = {r.atom_type for r in results}
        assert AtomType.PREFERENCE not in types
        assert AtomType.FACTUAL in types or AtomType.EPISODIC in types

    @pytest.mark.asyncio
    async def test_empty_query_returns_by_type(self, tmp_db_path):
        """search_fts_by_type with empty query returns atoms of the specified types."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(_make_atom(content="A", atom_type=AtomType.FACTUAL))
        await store.insert(_make_atom(content="B", atom_type=AtomType.FACTUAL))
        await store.insert(_make_atom(content="C", atom_type=AtomType.EPISODIC))

        results = await store.search_fts_by_type("", atom_types=["factual"], limit=10)
        assert all(r.atom_type == AtomType.FACTUAL for r in results)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_filter_by_session_id(self, tmp_db_path):
        """search_fts_by_type with session_id filter."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(_make_atom(content="记忆A", session_id="s1"))
        await store.insert(_make_atom(content="记忆B", session_id="s2"))

        results = await store.search_fts_by_type("记忆", session_id="s1", limit=10)
        assert len(results) >= 1
        assert all(r.session_id == "s1" for r in results)

    @pytest.mark.asyncio
    async def test_filter_by_persona_id(self, tmp_db_path):
        """search_fts_by_type with persona_id filter."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(_make_atom(content="人格A记忆", persona_id="persona_a"))
        await store.insert(_make_atom(content="人格B记忆", persona_id="persona_b"))

        results = await store.search_fts_by_type(
            "记忆", persona_id="persona_a", limit=10
        )
        assert len(results) >= 1
        assert all(r.persona_id == "persona_a" for r in results)

    @pytest.mark.asyncio
    async def test_filter_by_persona_id_no_query(self, tmp_db_path):
        """search_fts_by_type with persona_id but no query text."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(_make_atom(content="人格A内容", persona_id="pa"))
        await store.insert(_make_atom(content="人格B内容", persona_id="pb"))

        results = await store.search_fts_by_type("", persona_id="pa", limit=10)
        assert len(results) >= 1
        assert all(r.persona_id == "pa" for r in results)

    @pytest.mark.asyncio
    async def test_filter_by_atom_types_and_session_id(self, tmp_db_path):
        """search_fts_by_type with both atom_types and session_id filters."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        await store.insert(
            _make_atom(content="事实记忆", atom_type=AtomType.FACTUAL, session_id="s1")
        )
        await store.insert(
            _make_atom(content="事件记忆", atom_type=AtomType.EPISODIC, session_id="s1")
        )
        await store.insert(
            _make_atom(
                content="偏好记忆", atom_type=AtomType.PREFERENCE, session_id="s1"
            )
        )

        results = await store.search_fts_by_type(
            "记忆", atom_types=["factual", "preference"], session_id="s1", limit=10
        )
        types = {r.atom_type for r in results}
        assert AtomType.EPISODIC not in types

    @pytest.mark.asyncio
    async def test_fts_by_type_with_expired(self, tmp_db_path):
        """search_fts_by_type with include_expired=True."""
        store = AtomStore(tmp_db_path)
        await store.initialize()

        aid = await store.insert(
            _make_atom(content="过期类型记忆", atom_type=AtomType.FACTUAL)
        )
        async with store._connect() as db:
            await db.execute(
                "UPDATE memory_atoms SET status = 'expired', expires_at = ? WHERE id = ?",
                (time.time() - 100, aid),
            )
            await db.commit()

        results_no = await store.search_fts_by_type("过期", include_expired=False)
        assert len(results_no) == 0

        results_yes = await store.search_fts_by_type("过期", include_expired=True)
        assert len(results_yes) >= 1
