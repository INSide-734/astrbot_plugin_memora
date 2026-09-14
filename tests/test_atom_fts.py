"""AtomFTSMixin 测试 — 基于FTS5的记忆原子全文搜索。"""

import json
import logging
import time

import pytest

from core.features.memory.domain.memory_atom import AtomType, MemoryAtom
from core.features.memory.infrastructure import atom_fts as atom_fts_module
from core.features.memory.infrastructure.atom_fts import _build_match_expression
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


class TestBuildMatchExpression:
    """MATCH 表达式构造：片段一律短语化，空查询不进入 FTS。"""

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("", None),
            ("   ", None),
            ("'", '"\'"'),
            (":", '":"'),
            (".", '"."'),
            ("7", '"7"'),
            ("()", '"()"'),
            ('a"b', '"a""b"'),
            (
                "明早还跑不跑三公里 OR 血型是不是 O 型?",
                '"明早还跑不跑三公里" OR "OR" OR "血型是不是" OR "O" OR "型?"',
            ),
        ],
    )
    def test_fragments_are_always_quoted_phrases(self, query, expected):
        """关键字、标点、数字都退化为短语；OR 只作连接符不做词面处理。"""
        assert _build_match_expression(query) == expected


# FTS token 命中与 LIKE 子串命中完全重合的固定语料，
# 使两组结果集在所有用例下可直接比较。
_HARDENING_CORPUS = (
    "明早还跑不跑三公里 血型是不是 O 型?",
    "血型是 O 型，比例 3.14; OR 是逻辑运算符",
    "AND 与 NEAR 都是 FTS5 关键字",
    "版本 7 的记忆",
    "无关的普通记忆",
)

_HOSTILE_QUERIES = (
    "'",
    ":",
    ".",
    "OR",
    "AND",
    "NEAR",
    "7",
    "()",
    '"',
    "",
    "   ",
    "明早还跑不跑三公里 OR 血型是不是 O 型?",
    "他说：“明早还跑不跑三公里？”——血型是不是 O 型；",
)


async def _like_fallback_ids(
    store: AtomStore,
    query: str,
    limit: int = 10,
    atom_types: tuple[str, ...] | None = None,
) -> set[int]:
    """按 LIKE 回退口径（原始空白切分 + active/类型过滤）计算期望命中集。"""
    patterns = json.dumps([f"%{token}%" for token in query.split() if token])
    type_values = list(atom_types or ())
    async with store._connect() as db:
        cursor = await db.execute(
            """
            SELECT ma.id AS id
            FROM memory_atoms ma
            WHERE EXISTS (
                SELECT 1 FROM json_each(:patterns) AS pattern
                WHERE ma.content LIKE pattern.value
            )
              AND ma.status = 'active'
              AND (
                :has_atom_types = 0
                OR ma.atom_type IN (SELECT value FROM json_each(:atom_types))
              )
            ORDER BY ma.id DESC
            LIMIT :limit
            """,
            {
                "patterns": patterns,
                "has_atom_types": int(bool(type_values)),
                "atom_types": json.dumps(type_values),
                "limit": limit,
            },
        )
        rows = await cursor.fetchall()
    return {int(row[0]) for row in rows}


class TestAtomFTS_search_fts_hardening:
    """含 FTS5 语法字符的查询不得报错，且命中集与 LIKE 回退一致。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("query", _HOSTILE_QUERIES)
    async def test_hostile_query_logs_no_warning_and_matches_like(
        self, tmp_db_path, caplog, query
    ):
        """`'`、`:`、`.`、`OR`、`()`、纯空白等输入不再触发 fts5: syntax error。"""
        store = AtomStore(tmp_db_path)
        await store.initialize()
        for content in _HARDENING_CORPUS:
            await store.insert(_make_atom(content=content))

        with caplog.at_level(logging.WARNING):
            results = await store.search_fts(query, limit=10)

        warnings = [
            record for record in caplog.records if record.levelno >= logging.WARNING
        ]
        assert warnings == []
        assert {atom.atom_id for atom in results} == await _like_fallback_ids(
            store, query
        )


class TestAtomFTS_search_fts_by_type:
    """Type-filtered FTS search via search_fts_by_type."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("query", ("'", "OR", "."))
    async def test_hostile_query_in_type_branch_logs_no_warning_and_matches_like(
        self, tmp_db_path, caplog, query
    ):
        """含 FTS5 语法字符的查询在类型分支下同样不得报错，命中集与 LIKE 一致。"""
        store = AtomStore(tmp_db_path)
        await store.initialize()
        for content in _HARDENING_CORPUS:
            await store.insert(_make_atom(content=content))

        with caplog.at_level(logging.WARNING):
            results = await store.search_fts_by_type(
                query, atom_types=["factual"], limit=10
            )

        warnings = [
            record for record in caplog.records if record.levelno >= logging.WARNING
        ]
        assert warnings == []
        assert {atom.atom_id for atom in results} == await _like_fallback_ids(
            store, query, atom_types=("factual",)
        )

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


class TestAtomFTS_failure_logging_privacy:
    """FTS 失败必须留痕，但不得回显 sqlite 异常 message（其中含查询原文）。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ("search_fts", "search_fts_by_type"))
    async def test_failure_warning_does_not_echo_query_text(
        self, tmp_db_path, caplog, monkeypatch, method
    ):
        """还原裸 token 形态触发 `fts5: syntax error near "OR"`，日志只应记异常类型。"""
        store = AtomStore(tmp_db_path)
        await store.initialize()
        await store.insert(_make_atom(content="普通记忆"))

        marker = "OR"
        monkeypatch.setattr(
            atom_fts_module, "_build_match_expression", lambda query: query
        )

        with caplog.at_level(logging.WARNING):
            results = await getattr(store, method)(marker, limit=5)

        assert results == []
        warnings = [
            record
            for record in caplog.records
            if record.name.startswith("astrbot") and record.levelno >= logging.WARNING
        ]
        assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
        message = warnings[0].getMessage()
        assert "OperationalError" in message
        assert "syntax error" not in message
        assert marker not in message
