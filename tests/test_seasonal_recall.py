"""测试季节性召回函数 — day-of-year similarity and boost."""

import time

from core.features.retrieval.seasonal_recall import seasonal_boost, seasonal_similarity


class TestSeasonalSimilarity:
    def test_same_day_is_max(self):
        now = time.time()
        sim = seasonal_similarity(now, now)
        assert sim == 1.0

    def test_opposite_day_is_low(self):
        now = time.time()
        half_year = now - 182 * 86400
        sim = seasonal_similarity(half_year, now)
        assert sim < 0.1

    def test_exact_one_year_ago_is_max(self):
        now = time.time()
        one_year_ago = now - 365 * 86400
        sim = seasonal_similarity(one_year_ago, now)
        assert sim > 0.95

    def test_similarity_bounds(self):
        now = time.time()
        for offset_days in [0, 30, 90, 180, 365]:
            ts = now - offset_days * 86400
            sim = seasonal_similarity(ts, now)
            assert 0.0 <= sim <= 1.0


class TestSeasonalBoost:
    def test_boost_at_anniversary(self):
        now = time.time()
        one_year_ago = now - 365 * 86400
        boost = seasonal_boost(one_year_ago, now)
        assert boost > 1.0

    def test_no_boost_far_from_anniversary(self):
        now = time.time()
        ts = now - 180 * 86400
        boost = seasonal_boost(ts, now)
        assert boost == 1.0

    def test_boost_minimum_is_one(self):
        boost = seasonal_boost(time.time(), time.time())
        assert boost >= 1.0

    def test_default_current_timestamp(self):
        one_year_ago = time.time() - 365 * 86400
        boost = seasonal_boost(one_year_ago)
        assert isinstance(boost, float)
        assert boost >= 1.0
