"""intent_keywords 测试 — 意图检测的关键词列表。"""

from __future__ import annotations

import pytest


class TestIntentKeywords:
    def test_relation_terms_not_empty(self) -> None:
        """RELATION_TERMS contains expected keywords."""
        from core.features.retrieval.intent_keywords import RELATION_TERMS

        assert isinstance(RELATION_TERMS, tuple)
        assert len(RELATION_TERMS) > 0
        assert "谁" in RELATION_TERMS
        assert "friend" in RELATION_TERMS

    def test_temporal_terms_not_empty(self) -> None:
        """TEMPORAL_TERMS contains expected keywords."""
        from core.features.retrieval.intent_keywords import TEMPORAL_TERMS

        assert isinstance(TEMPORAL_TERMS, tuple)
        assert len(TEMPORAL_TERMS) > 0
        assert "昨天" in TEMPORAL_TERMS
        assert "recently" in TEMPORAL_TERMS

    def test_factual_terms_not_empty(self) -> None:
        """FACTUAL_TERMS contains expected keywords."""
        from core.features.retrieval.intent_keywords import FACTUAL_TERMS

        assert isinstance(FACTUAL_TERMS, tuple)
        assert len(FACTUAL_TERMS) > 0
        assert "是什么" in FACTUAL_TERMS
        assert "how to" in FACTUAL_TERMS

    @pytest.mark.parametrize(
        "query,expected_in_collection",
        [
            ("我和他是朋友", "RELATION"),
            ("昨天发生了什么", "TEMPORAL"),
            ("这个是什么东西", "FACTUAL"),
        ],
    )
    def test_keyword_matching_in_queries(
        self, query: str, expected_in_collection: str
    ) -> None:
        """Common Chinese queries should hit the expected keyword collection."""
        from core.features.retrieval.intent_keywords import (
            FACTUAL_TERMS,
            RELATION_TERMS,
            TEMPORAL_TERMS,
        )

        collections = {
            "RELATION": RELATION_TERMS,
            "TEMPORAL": TEMPORAL_TERMS,
            "FACTUAL": FACTUAL_TERMS,
        }
        target = collections[expected_in_collection]
        normalized = query.casefold()
        assert any(term in normalized for term in target)
