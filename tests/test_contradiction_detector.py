"""contradiction_detector.py 测试 — ContradictionDetector。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from core.processors.contradiction_detector import (
    ContradictionDetector,
    _detect_semantic_contradiction,
    _jaccard,
    _tokenize,
)


class TestTokenize:
    def test_tokenize_chinese(self) -> None:
        tokens = _tokenize("我喜欢咖啡")
        assert "喜" in tokens or "欢" in tokens
        assert len(tokens) > 0

    def test_tokenize_english_mixed(self) -> None:
        tokens = _tokenize("I like coffee and 咖啡")
        assert "i" in tokens or "like" in tokens or "coffee" in tokens

    def test_tokenize_empty(self) -> None:
        assert _tokenize("") == []

    def test_tokenize_punctuation_only(self) -> None:
        tokens = _tokenize("。。。")
        # Punctuation is not captured by the regex
        assert tokens == []


class TestJaccard:
    def test_identical_sets(self) -> None:
        assert _jaccard({"a", "b", "c"}, {"a", "b", "c"}) == 1.0

    def test_disjoint_sets(self) -> None:
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self) -> None:
        result = _jaccard({"a", "b", "c"}, {"b", "c", "d"})
        assert result == pytest.approx(2.0 / 4.0)

    def test_empty_set(self) -> None:
        assert _jaccard(set(), {"a"}) == 0.0
        assert _jaccard({"a"}, set()) == 0.0
        assert _jaccard(set(), set()) == 0.0


class TestSemanticContradiction:
    def test_new_negation_old_affirmative(self) -> None:
        assert _detect_semantic_contradiction("我不喜欢咖啡", "我喜欢咖啡") is True

    def test_old_negation_new_affirmative(self) -> None:
        assert _detect_semantic_contradiction("我喜欢咖啡", "我不喜欢咖啡") is True

    def test_both_affirmative_no_contradiction(self) -> None:
        assert _detect_semantic_contradiction("我喜欢咖啡", "我也喜欢喝茶") is False

    def test_both_negation_no_contradiction(self) -> None:
        assert _detect_semantic_contradiction("我不喝咖啡", "我也不喝茶") is False

    def test_neutral_no_keywords(self) -> None:
        assert _detect_semantic_contradiction("今天天气不错", "昨天去了公园") is False

    def test_quit_keyword_triggers_negation(self) -> None:
        assert (
            _detect_semantic_contradiction("我已经戒咖啡三个月了", "我喜欢喝咖啡")
            is True
        )


class TestContradictionDetector:
    @pytest.fixture
    def detector(self) -> ContradictionDetector:
        search_fn = AsyncMock(return_value=[])
        update_fn = AsyncMock(return_value=True)
        return ContradictionDetector(search_fn=search_fn, update_fn=update_fn)

    def test_default_enabled(self) -> None:
        d = ContradictionDetector()
        assert d.enabled is True

    def test_disabled_returns_empty(self) -> None:
        d = ContradictionDetector(enabled=False)
        result = asyncio.run(d.check_and_mark("test", ["topic"], "session1"))
        assert result == []

    def test_no_search_fn_returns_empty(self) -> None:
        d = ContradictionDetector(search_fn=None)
        result = asyncio.run(d.check_and_mark("test", ["topic"]))
        assert result == []

    def test_empty_content_returns_empty(self, detector: ContradictionDetector) -> None:
        result = asyncio.run(detector.check_and_mark("", ["topic"]))
        assert result == []

    def test_empty_topics_returns_empty(self, detector: ContradictionDetector) -> None:
        result = asyncio.run(detector.check_and_mark("有内容", []))
        assert result == []

    def test_no_candidates_found(self) -> None:
        search_fn = AsyncMock(return_value=[])
        detector = ContradictionDetector(search_fn=search_fn, update_fn=AsyncMock())
        result = asyncio.run(detector.check_and_mark("message", ["topic"]))
        assert result == []

    def test_candidates_with_contradiction_marked(self) -> None:
        # Use short, overlapping tokens to ensure Jaccard >= 0.40
        search_fn = AsyncMock(
            return_value=[
                {"id": 1, "text": "我喜欢喝咖啡", "metadata": {}},
            ]
        )
        update_fn = AsyncMock(return_value=True)
        detector = ContradictionDetector(search_fn=search_fn, update_fn=update_fn)

        result = asyncio.run(
            detector.check_and_mark("我不再喝咖啡了", ["咖啡", "饮食"])
        )
        assert len(result) >= 1
        assert update_fn.called

    def test_candidates_no_contradiction_not_marked(self) -> None:
        search_fn = AsyncMock(
            return_value=[
                {"id": 1, "text": "我喜欢喝咖啡", "metadata": {}},
            ]
        )
        update_fn = AsyncMock(return_value=True)
        detector = ContradictionDetector(search_fn=search_fn, update_fn=update_fn)

        result = asyncio.run(detector.check_and_mark("我也喜欢喝咖啡", ["咖啡"]))
        assert result == []
        assert not update_fn.called

    def test_candidate_without_id_skipped(self) -> None:
        search_fn = AsyncMock(
            return_value=[
                {"text": "我喜欢喝咖啡"},
            ]
        )
        update_fn = AsyncMock(return_value=True)
        detector = ContradictionDetector(search_fn=search_fn, update_fn=update_fn)

        result = asyncio.run(detector.check_and_mark("我不喜欢喝咖啡", ["咖啡"]))
        assert result == []

    def test_enabled_setter(self) -> None:
        d = ContradictionDetector(enabled=True)
        d.enabled = False
        assert d.enabled is False

    def test_search_exception_returns_empty(self) -> None:
        search_fn = AsyncMock(side_effect=RuntimeError("search failed"))
        detector = ContradictionDetector(search_fn=search_fn)
        result = asyncio.run(detector.check_and_mark("msg", ["topic"]))
        assert result == []
