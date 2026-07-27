"""测试 MABWeightLearner — epsilon-greedy fusion weight learning."""

from __future__ import annotations

import pytest

from core.managers.weight_learner import MABWeightLearner


class TestMABDefaults:
    """测试默认初始化。"""

    def test_default_weights(self) -> None:
        """默认 global weights are 0.65 doc / 0.35 graph."""
        learner = MABWeightLearner()
        dw, gw = learner.get_weights()
        assert dw == 0.65
        assert gw == 0.35

    def test_default_stats(self) -> None:
        """默认 stats are sensible."""
        learner = MABWeightLearner()
        stats = learner.stats
        assert stats["global_doc_weight"] == 0.65
        assert stats["global_graph_weight"] == 0.35
        assert stats["total_trials"] == 0
        assert stats["persona_count"] == 0


class TestExploreWeights:
    """测试 epsilon-greedy 探索。"""

    def test_explore_returns_valid_weights(self) -> None:
        """Explore weights are always in [0.1, 0.9] and sum to 1."""
        learner = MABWeightLearner(epsilon=1.0)  # always explore
        for _ in range(50):
            dw, gw = learner.get_explore_weights()
            assert 0.1 <= dw <= 0.9
            assert 0.1 <= gw <= 0.9
            assert dw + gw == pytest.approx(1.0)


class TestRecordFeedback:
    """测试记录隐式反馈。"""

    def test_feedback_updates_weights(self) -> None:
        """正 feedback with doc-heavy weighting increases doc weight."""
        learner = MABWeightLearner(learning_rate=0.1)
        dw_before, gw_before = learner.get_weights()
        learner.record_feedback(doc_weight=0.8, graph_weight=0.2, reward=1.0)
        dw_after, gw_after = learner.get_weights()
        # Doc weight should increase (reward > 0.5, current doc > weight)
        assert dw_after >= dw_before

    def test_feedback_increases_trials(self) -> None:
        """Each feedback increments total_trials."""
        learner = MABWeightLearner()
        learner.record_feedback(0.65, 0.35, 0.7)
        learner.record_feedback(0.65, 0.35, 0.3)
        assert learner.stats["total_trials"] == 2

    def test_persona_specific_weights(self) -> None:
        """Feedback with persona_id creates persona-specific weights."""
        learner = MABWeightLearner(learning_rate=0.1)
        # Record feedback for a specific persona
        learner.record_feedback(
            doc_weight=0.7, graph_weight=0.3, reward=1.0, persona_id="persona1"
        )
        dw, gw = learner.get_weights(persona_id="persona1")
        assert dw > 0.65  # should increase
        assert 0.1 <= dw <= 0.9
        assert 0.1 <= gw <= 0.9
        assert dw + gw == pytest.approx(1.0)


class TestStats:
    """测试 stats 属性。"""

    def test_stats_after_feedback(self) -> None:
        """Stats reflect accumulated feedback."""
        learner = MABWeightLearner()
        learner.record_feedback(0.65, 0.35, 1.0)
        learner.record_feedback(0.65, 0.35, 0.0)
        stats = learner.stats
        assert stats["total_trials"] == 2
        assert "avg_doc_reward" in stats
        assert "avg_graph_reward" in stats
        assert "epsilon" in stats

    def test_stats_persona_count(self) -> None:
        """Stats tracks number of persona-specific weights."""
        learner = MABWeightLearner()
        learner.record_feedback(0.65, 0.35, 0.5, persona_id="p1")
        learner.record_feedback(0.65, 0.35, 0.5, persona_id="p2")
        learner.record_feedback(0.65, 0.35, 0.5, persona_id="p1")
        assert learner.stats["persona_count"] == 2
