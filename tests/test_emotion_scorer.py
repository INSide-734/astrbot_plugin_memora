"""emotion_scorer 函数测试 — Jaccard重叠度、强度加权。"""

from core.features.retrieval.emotion_scorer import (
    compute_emotion_boost,
    emotion_similarity,
)


class TestEmotionSimilarity:
    def test_exact_tag_match(self):
        score = emotion_similarity(["joy", "excited"], ["joy", "excited"], 0.8)
        assert score > 0.5

    def test_no_overlap_zero(self):
        # No tag overlap → only intensity component (0.25 * 0.5 = 0.125)
        score = emotion_similarity(["sad"], ["joy"], 0.5)
        assert score == 0.125

    def test_empty_tags_zero(self):
        assert emotion_similarity([], ["joy"], 0.5) == 0.0
        assert emotion_similarity(["joy"], [], 0.5) == 0.0

    def test_high_intensity_amplifies(self):
        score_low = emotion_similarity(["joy"], ["joy"], 0.3)
        score_high = emotion_similarity(["joy"], ["joy"], 0.9)
        assert score_high > score_low

    def test_partial_overlap_between_zero_and_one(self):
        score = emotion_similarity(["joy", "excited"], ["joy", "surprise"], 0.7)
        assert 0.0 < score < 1.0

    def test_score_bounds_zero_to_one(self):
        for _ in range(50):
            import random

            tags = random.sample(
                [
                    "joy",
                    "sad",
                    "anger",
                    "fear",
                    "surprise",
                    "disgust",
                    "neutral",
                    "excited",
                    "calm",
                    "happy",
                ],
                k=random.randint(0, 5),
            )
            mood = random.sample(["joy", "sad", "neutral"], k=random.randint(0, 3))
            intensity = random.uniform(0, 1)
            score = emotion_similarity(tags, mood, intensity)
            assert 0.0 <= score <= 1.0


class TestComputeEmotionBoost:
    def test_default_multiplier(self):
        boost = compute_emotion_boost(0.5)
        assert boost == 1.0 + 0.3 * 0.5

    def test_zero_similarity_gives_one(self):
        assert compute_emotion_boost(0.0) == 1.0

    def test_max_similarity_gives_max_boost(self):
        assert compute_emotion_boost(1.0) == 1.0 + 0.3 * 1.0

    def test_custom_multiplier(self):
        boost = compute_emotion_boost(0.5, base_multiplier=0.5)
        assert boost == 1.0 + 0.5 * 0.5
