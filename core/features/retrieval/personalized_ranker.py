"""基于用户画像标签权重的个性化排序加成。"""

from __future__ import annotations

from ..profiles.domain.models import UserProfile
from .rrf_fusion import HybridResult


class PersonalizedRanker:
    """根据用户画像标签权重对检索结果进行加权。"""

    def __init__(self, boost_strength: float = 0.15) -> None:
        self._boost_strength = max(0.0, min(0.5, boost_strength))

    def apply(
        self,
        results: list[HybridResult],
        tag_weights: dict[str, float],
        profile: UserProfile | None = None,
    ) -> list[HybridResult]:
        if not tag_weights or not results:
            return results

        for r in results:
            boost = self._compute_boost(r, tag_weights)
            if profile is not None:
                boost += self._preference_boost(r, profile)
            if boost > 0:
                r.final_score = min(1.0, r.final_score + boost)

        results.sort(key=lambda item: item.final_score, reverse=True)
        return results

    def _compute_boost(
        self, result: HybridResult, tag_weights: dict[str, float]
    ) -> float:
        content = (result.content or "").lower()
        meta_str = str(result.metadata or {}).lower()
        total_boost = 0.0
        matched = 0

        for tag_value, weight in tag_weights.items():
            tag_lower = tag_value.lower()
            if tag_lower in content or tag_lower in meta_str:
                total_boost += weight * self._boost_strength
                matched += 1

        if matched > 0:
            total_boost = total_boost * (1.0 + 0.1 * (matched - 1)) / matched
            total_boost = min(0.3, total_boost)
        return round(total_boost, 4)

    @staticmethod
    def _preference_boost(result: HybridResult, profile: UserProfile) -> float:
        boost = 0.0
        content = (result.content or "").lower()
        for topic in profile.preferences.preferred_topics:
            if topic.lower() in content:
                boost += 0.05
        for topic in profile.preferences.avoided_topics:
            if topic.lower() in content:
                boost -= 0.1
        return round(max(-0.2, min(0.2, boost)), 4)


__all__ = ["PersonalizedRanker"]
