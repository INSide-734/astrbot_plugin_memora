"""测试 PersonalizedRanker 基于用户画像标签调整结果。"""

from __future__ import annotations

from typing import Any

import pytest


def _make_result(
    doc_id: int, final_score: float, content: str = "", metadata: dict | None = None
) -> Any:
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
        """标签权重为空时不改变结果。"""
        results = [_make_result(1, 0.9, "test")]
        output = ranker.apply(results, tag_weights={})
        assert output[0].final_score == 0.9

    def test_apply_empty_results(self, ranker: Any) -> None:
        """结果列表为空时原样返回。"""
        output = ranker.apply([], tag_weights={"coffee": 0.8})
        assert output == []

    def test_apply_tag_match_in_content(self, ranker: Any) -> None:
        """正文命中标签时提升分数。"""
        results = [
            _make_result(1, 0.5, "I really enjoy drinking coffee every morning"),
            _make_result(2, 0.5, "Walking the dog in the park"),
        ]
        tag_weights = {"coffee": 0.8}
        output = ranker.apply(results, tag_weights)
        # 第一条文档应被提升。
        assert output[0].doc_id == 1
        assert output[0].final_score > 0.5
        # 第二条文档应保持不变。
        assert output[1].final_score == 0.5

    def test_apply_tag_match_in_metadata(self, ranker: Any) -> None:
        """metadata 命中标签时同样提升分数。"""
        results = [
            _make_result(1, 0.5, "Some content", {"topics": "coffee"}),
        ]
        output = ranker.apply(results, tag_weights={"coffee": 0.8})
        assert output[0].final_score > 0.5

    def test_apply_multiple_tag_matches(self, ranker: Any) -> None:
        """多个命中标签累积提升，但上限为 0.3。"""
        results = [
            _make_result(1, 0.4, "I love coffee and coding in Python"),
        ]
        tag_weights = {"coffee": 0.8, "python": 0.7}
        output = ranker.apply(results, tag_weights)
        # 两个标签都应参与提升计算。
        assert output[0].final_score > 0.4
        assert output[0].final_score <= 0.7  # 基础分加提升后仍受 0.3 上限约束。

    def test_apply_score_capped_at_one(self, ranker: Any) -> None:
        """最终分数不得超过 1.0。"""
        results = [
            _make_result(1, 0.95, "coffee coffee coffee coffee"),
        ]
        tag_weights = {"coffee": 1.0}
        output = ranker.apply(results, tag_weights)
        assert output[0].final_score <= 1.0

    def test_apply_with_profile_preferred_topics(self, ranker: Any) -> None:
        """画像偏好话题在标签权重非空时提供额外提升。"""
        from core.features.profiles.domain.models import UserPreferences, UserProfile

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
        # 标签权重必须非空，排序器才会处理结果。
        output = ranker.apply(results, tag_weights={"dummy": 0.0}, profile=profile)
        assert output[0].final_score > 0.5

    def test_preference_boost_avoided_topics(self) -> None:
        """静态偏好计算对回避话题返回负提升。"""
        from core.features.profiles.domain.models import UserPreferences, UserProfile
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
        assert boost < 0  # 回避话题应降低分数。

    def test_preference_boost_preferred_topics(self) -> None:
        """静态偏好计算对偏好话题返回正提升。"""
        from core.features.profiles.domain.models import UserPreferences, UserProfile
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
        assert boost > 0  # 偏好话题应提升分数。

    def test_boost_strength_clamped(self) -> None:
        """构造器将提升强度限制在 [0, 0.5]。"""
        from core.retrieval.personalized_ranker import PersonalizedRanker

        r1 = PersonalizedRanker(boost_strength=1.0)
        assert r1._boost_strength == 0.5  # type: ignore[attr-defined]
        r2 = PersonalizedRanker(boost_strength=-0.1)
        assert r2._boost_strength == 0.0  # type: ignore[attr-defined]
