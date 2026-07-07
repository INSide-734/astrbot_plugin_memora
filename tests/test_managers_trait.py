"""测试 TraitEvolutionTracker — trait drift detection."""

from __future__ import annotations

import pytest

from core.managers.trait_evolution import TraitEvolutionTracker


class TestIngestMemory:
    """测试从消息记录特征证据。"""

    def test_simple_label_match(self) -> None:
        """Content containing trait labels increments counters."""
        tracker = TraitEvolutionTracker()
        tracker.ingest_memory("我对新事物很开放好奇")
        # "开放好奇" is the positive label for "openness"
        profile = tracker.get_trait_profile()
        assert profile["openness"]["positive_count"] == 1
        assert profile["openness"]["negative_count"] == 0

    def test_negative_label_match(self) -> None:
        """Content with negative trait label increments negative counter."""
        tracker = TraitEvolutionTracker()
        tracker.ingest_memory("他非常保守传统")
        profile = tracker.get_trait_profile()
        assert profile["openness"]["positive_count"] == 0
        assert profile["openness"]["negative_count"] == 1

    def test_both_labels_no_effect(self) -> None:
        """当 both positive and negative labels appear, neither increments."""
        tracker = TraitEvolutionTracker()
        tracker.ingest_memory("他开放好奇但同时也保守传统")
        profile = tracker.get_trait_profile()
        assert profile["openness"]["positive_count"] == 0
        assert profile["openness"]["negative_count"] == 0

    def test_sentiment_fallback(self) -> None:
        """当 no label matches, sentiment determines counter."""
        tracker = TraitEvolutionTracker()
        tracker.ingest_memory("今天天气不错", {"sentiment": "positive"})
        profile = tracker.get_trait_profile()
        # All 5 dimensions get +1 positive
        for dim in profile:
            assert profile[dim]["positive_count"] == 1
            assert profile[dim]["negative_count"] == 0

    def test_sentiment_negative_fallback(self) -> None:
        """无标签匹配的负面情感会增加负面计数。"""
        tracker = TraitEvolutionTracker()
        tracker.ingest_memory("今天真糟糕", {"sentiment": "negative"})
        profile = tracker.get_trait_profile()
        for dim in profile:
            assert profile[dim]["negative_count"] == 1

    def test_no_metadata_defaults_neutral(self) -> None:
        """在没有 metadata/sentiment and no label match, nothing changes."""
        tracker = TraitEvolutionTracker()
        tracker.ingest_memory("hello world")
        profile = tracker.get_trait_profile()
        for dim in profile:
            assert profile[dim]["positive_count"] == 0
            assert profile[dim]["negative_count"] == 0

    def test_case_insensitive_label_match(self) -> None:
        """Label matching is case-insensitive (content is lowercased)."""
        tracker = TraitEvolutionTracker()
        # Labels in the dict are e.g. "开放好奇", content is lowercased
        tracker.ingest_memory("TA很外向活泼")  # will be lowercased
        # "外向活泼" becomes lowercase via content.lower()
        profile = tracker.get_trait_profile()
        # In English labels this works; Chinese labels should also match
        assert (
            profile["extraversion"]["positive_count"] + profile["extraversion"]["negative_count"]
        ) > 0


class TestCheckDrift:
    """测试特征漂移检测。"""

    def test_no_drift_with_insufficient_evidence(self) -> None:
        """不 enough evidence (below min_opposing) means no drift."""
        tracker = TraitEvolutionTracker(min_opposing=10)
        # Only 5 positive entries for openness
        for _ in range(5):
            tracker.ingest_memory("我开放好奇")
        events = tracker.check_drift()
        assert len(events) == 0

    def test_drift_with_sufficient_evidence(self) -> None:
        """Enough evidence triggers drift event."""
        tracker = TraitEvolutionTracker(min_opposing=10)
        for _ in range(14):
            tracker.ingest_memory("他非常保守传统")
        events = tracker.check_drift()
        assert len(events) >= 1
        # openness negative should dominate
        openness_event = next((e for e in events if e["dimension"] == "openness"), None)
        assert openness_event is not None
        assert openness_event["new_dominant"] == "保守传统"
        assert openness_event["total_evidence"] >= 14

    def test_drift_ratio_below_threshold_no_event(self) -> None:
        """当 contradiction ratio is below threshold, no drift."""
        tracker = TraitEvolutionTracker(min_opposing=14, contradiction_ratio=0.95)
        # Mixed evidence: 8 positive, 8 negative for openness
        for _ in range(8):
            tracker.ingest_memory("我开放好奇")
        for _ in range(8):
            tracker.ingest_memory("我保守传统")
        events = tracker.check_drift()
        assert len(events) == 0  # ratio ~0.5 < 0.95

    def test_drift_event_has_required_fields(self) -> None:
        """Drift event contains all expected fields."""
        tracker = TraitEvolutionTracker(min_opposing=10)
        for _ in range(14):
            tracker.ingest_memory("他外向活泼")
        events = tracker.check_drift()
        assert len(events) >= 1
        event = events[0]
        assert "dimension" in event
        assert "old_dominant" in event
        assert "new_dominant" in event
        assert "positive_count" in event
        assert "negative_count" in event
        assert "contradiction_ratio" in event
        assert "total_evidence" in event

    def test_active_drifts_tracks_new_dominants(self) -> None:
        """active_drifts stores the new_dominant direction key."""
        tracker = TraitEvolutionTracker(min_opposing=10)
        for _ in range(14):
            tracker.ingest_memory("他外向活泼")
        events = tracker.check_drift()
        assert len(events) >= 1
        # _active_drifts is updated; verify it has entries
        assert len(tracker._active_drifts) >= 1


class TestTraitProfile:
    """测试 get_trait_profile."""

    def test_profile_has_all_dimensions(self) -> None:
        """Profile includes all 5 trait dimensions."""
        tracker = TraitEvolutionTracker()
        profile = tracker.get_trait_profile()
        assert set(profile.keys()) == {
            "openness",
            "conscientiousness",
            "extraversion",
            "agreeableness",
            "neuroticism",
        }

    def test_profile_confidence_initial(self) -> None:
        """初始 profile has confidence based on single evidence."""
        tracker = TraitEvolutionTracker()
        tracker.ingest_memory("我开放好奇")
        profile = tracker.get_trait_profile()
        assert profile["openness"]["positive_count"] == 1
        assert profile["openness"]["negative_count"] == 0
        assert profile["openness"]["dominant"] == "开放好奇"
        assert profile["openness"]["confidence"] == 1.0

    def test_profile_after_mixed_evidence(self) -> None:
        """Confidence reflects ratio after mixed evidence."""
        tracker = TraitEvolutionTracker()
        tracker.ingest_memory("我开放好奇")
        tracker.ingest_memory("我保守传统")
        tracker.ingest_memory("我开放好奇")
        profile = tracker.get_trait_profile()
        assert profile["openness"]["positive_count"] == 2
        assert profile["openness"]["negative_count"] == 1
        assert profile["openness"]["dominant"] == "开放好奇"
        # confidence = max(pos, neg) / total = 2/3; rounded to 4 decimal places → 0.6667
        assert profile["openness"]["confidence"] == pytest.approx(2 / 3, abs=0.001)


class TestDriftHistory:
    """测试 drift_history 与摘要。"""

    def test_drift_history_initial(self) -> None:
        """Initially empty drift history."""
        tracker = TraitEvolutionTracker()
        assert tracker.drift_history == []

    def test_drift_summaries(self) -> None:
        """get_drift_summary returns correct structure."""
        tracker = TraitEvolutionTracker()
        summary = tracker.get_drift_summary()
        assert summary["active_drifts"] == 0
        assert summary["total_drifts"] == 0
        assert not summary["in_evolution_phase"]
