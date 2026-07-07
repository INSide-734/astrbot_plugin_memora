"""测试 PersonalizedRanker — boost results based on user profile tags."""

from __future__ import annotations

from typing import Any

import pytest


def _make_result(doc_id: int, final_score: float, content: str = "", metadata: dict | None = None) -> Any:
    from core.retrieval.rrf_fusion import HybridResult
    return HybridResult(
        doc_id=doc_id,
        final_score=final_score,
        rrf_score=final_score,
        bm25_score=None,
        vector_score=None,
        content=content,
        metadata=metadata or {},
    )


class TestPersonalizedRanker:

    @pytest.fixture
    def ranker(self) -> Any:
        from core.retrieval.personalized_ranker import PersonalizedRanker
        return PersonalizedRanker(boost_strength=0.15)

    def test_apply_empty_tag_weights(self, ranker: Any) -> None:
        """没有 tag weights means no changes to results."""
        results = [_make_result(1, 0.9, "test")]
        output = ranker.apply(results, tag_weights={})
        assert output[0].final_score == 0.9

    def test_apply_empty_results(self, ranker: Any) -> None:
        """空 results list returns unchanged."""
        output = ranker.apply([], tag_weights={"coffee": 0.8})
        assert output == []

    def test_apply_tag_match_in_content(self, ranker: Any) -> None:
        """Matching tag in content boosts the score."""
        results = [
            _make_result(1, 0.5, "I really enjoy drinking coffee every morning"),
            _make_result(2, 0.5, "Walking the dog in the park"),
        ]
        tag_weights = {"coffee": 0.8}
        output = ranker.apply(results, tag_weights)
        # Doc 1 should be boosted
        assert output[0].doc_id == 1
        assert output[0].final_score > 0.5
        # Doc 2 unchanged
        assert output[1].final_score == 0.5

    def test_apply_tag_match_in_metadata(self, ranker: Any) -> None:
        """Matching tag in metadata also triggers boost."""
        results = [
            _make_result(1, 0.5, "Some content", {"topics": "coffee"}),
        ]
        output = ranker.apply(results, tag_weights={"coffee": 0.8})
        assert output[0].final_score > 0.5

    def test_apply_multiple_tag_matches(self, ranker: Any) -> None:
        """多个 matching tags increase boost (capped at 0.3)."""
        results = [
            _make_result(1, 0.4, "I love coffee and coding in Python"),
        ]
        tag_weights = {"coffee": 0.8, "python": 0.7}
        output = ranker.apply(results, tag_weights)
        # Boost should be computed for both tags
        assert output[0].final_score > 0.4
        assert output[0].final_score <= 0.7  # capped at 0.3 + base

    def test_apply_score_capped_at_one(self, ranker: Any) -> None:
        """最终 score never exceeds 1.0."""
        results = [
            _make_result(1, 0.95, "coffee coffee coffee coffee"),
        ]
        tag_weights = {"coffee": 1.0}
        output = ranker.apply(results, tag_weights)
        assert output[0].final_score <= 1.0

    def test_apply_with_profile_preferred_topics(self, ranker: Any) -> None:
        """Profile preferred_topics add extra boost (requires non-empty tag_weights to enter apply)."""
        from core.models.user_profile import UserPreferences, UserProfile

        profile = UserProfile(
            user_id="u1",
            preferences=UserPreferences(
                preferred_topics=["coffee", "music"],
                avoided_topics=[],
            ),
        )
        results = [
            _make_result(1, 0.5, "I went to a coffee shop today"),
        ]
        # tag_weights must be non-empty for apply to process results
        output = ranker.apply(results, tag_weights={"dummy": 0.0}, profile=profile)
        assert output[0].final_score > 0.5

    def test_preference_boost_avoided_topics(self) -> None:
        """静态 _preference_boost returns negative for avoided topics."""
        from core.models.user_profile import UserPreferences, UserProfile
        from core.retrieval.personalized_ranker import PersonalizedRanker

        profile = UserProfile(
            user_id="u1",
            preferences=UserPreferences(
                preferred_topics=[],
                avoided_topics=["politics"],
            ),
        )
        result = _make_result(1, 0.5, "Discussion about politics today")
        boost = PersonalizedRanker._preference_boost(result, profile)
        assert boost < 0  # Negative boost for avoided topic

    def test_preference_boost_preferred_topics(self) -> None:
        """静态 _preference_boost returns positive for preferred topics."""
        from core.models.user_profile import UserPreferences, UserProfile
        from core.retrieval.personalized_ranker import PersonalizedRanker

        profile = UserProfile(
            user_id="u1",
            preferences=UserPreferences(
                preferred_topics=["coffee"],
                avoided_topics=[],
            ),
        )
        result = _make_result(1, 0.5, "I love drinking coffee")
        boost = PersonalizedRanker._preference_boost(result, profile)
        assert boost > 0  # Positive boost for preferred topic

    def test_boost_strength_clamped(self) -> None:
        """Constructor clamps boost_strength to [0, 0.5]."""
        from core.retrieval.personalized_ranker import PersonalizedRanker
        r1 = PersonalizedRanker(boost_strength=1.0)
        assert r1._boost_strength == 0.5  # type: ignore[attr-defined]
        r2 = PersonalizedRanker(boost_strength=-0.1)
        assert r2._boost_strength == 0.0  # type: ignore[attr-defined]
