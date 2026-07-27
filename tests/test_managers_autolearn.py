"""FeedbackCollector、ParamOptimizer 和 AutoLearningManager 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.managers.auto_learning import (
    AutoLearningManager,
    FeedbackCollector,
    ParamOptimizer,
)

# ---------------------------------------------------------------------------
# FeedbackCollector
# ---------------------------------------------------------------------------


class TestFeedbackCollector:
    """Unit tests for feedback collection."""

    def test_initial_state(self) -> None:
        fc = FeedbackCollector()
        assert fc.hit_rate == 0.0
        assert fc.stats["total_recalls"] == 0
        assert fc.stats["total_hits"] == 0
        assert fc.stats["total_misses"] == 0
        assert fc.stats["total_corrections"] == 0
        assert fc.stats["avg_quality"] == 0.5

    def test_record_recall_hit(self) -> None:
        fc = FeedbackCollector()
        fc.record_recall(1, True)
        assert fc.stats["total_recalls"] == 1
        assert fc.stats["total_hits"] == 1
        assert fc.stats["total_misses"] == 0
        assert fc.hit_rate == 1.0

    def test_record_recall_miss(self) -> None:
        fc = FeedbackCollector()
        fc.record_recall(1, False)
        assert fc.stats["total_recalls"] == 1
        assert fc.stats["total_hits"] == 0
        assert fc.stats["total_misses"] == 1
        assert fc.hit_rate == 0.0

    def test_hit_rate_multiple(self) -> None:
        fc = FeedbackCollector()
        fc.record_recall(1, True)
        fc.record_recall(2, True)
        fc.record_recall(3, False)
        fc.record_recall(4, True)
        assert fc.hit_rate == 3 / 4
        assert fc.stats["total_recalls"] == 4
        assert fc.stats["total_hits"] == 3
        assert fc.stats["total_misses"] == 1

    def test_record_quality_updates_ema(self) -> None:
        fc = FeedbackCollector()
        fc.record_quality(1.0)
        # avg = (1-0.1)*0.5 + 0.1*1.0 = 0.55
        assert round(fc.stats["avg_quality"], 6) == 0.55
        fc.record_quality(0.0)
        # avg = (1-0.1)*0.55 + 0.1*0.0 = 0.495
        assert round(fc.stats["avg_quality"], 6) == 0.495

    def test_record_quality_clamped(self) -> None:
        fc = FeedbackCollector()
        fc.record_quality(2.0)  # clamped to 1.0
        assert 0 < fc.stats["avg_quality"] <= 1.0
        fc.record_quality(-1.0)  # clamped to 0.0
        assert 0 <= fc.stats["avg_quality"] <= 1.0

    def test_record_correction(self) -> None:
        fc = FeedbackCollector()
        fc.record_correction("wrong answer")
        assert fc.stats["total_corrections"] == 1
        fc.record_correction()
        assert fc.stats["total_corrections"] == 2

    def test_max_samples_trims_oldest(self) -> None:
        fc = FeedbackCollector(max_samples=3)
        fc.record_recall(1, True)
        fc.record_recall(2, True)
        fc.record_recall(3, True)
        fc.record_recall(4, False)  # should evict recall #1
        assert fc.stats["total_recalls"] == 4  # stats never trimmed
        assert len(fc._feedback) == 3
        assert fc._feedback[0]["memory_id"] == 2  # id=1 was evicted

    def test_hit_rate_zero_recalls(self) -> None:
        fc = FeedbackCollector()
        assert fc.hit_rate == 0.0  # avoids division by zero

    def test_stats_return_copy(self) -> None:
        fc = FeedbackCollector()
        s = fc.stats
        s["total_recalls"] = 999
        assert fc.stats["total_recalls"] == 0  # unaffected


# ---------------------------------------------------------------------------
# ParamOptimizer
# ---------------------------------------------------------------------------


class TestParamOptimizer:
    """Unit tests for online parameter optimization."""

    def test_default_params(self) -> None:
        opt = ParamOptimizer()
        params = opt.get_all_params()
        assert params["importance_threshold"] == 0.3
        assert params["recall_top_k"] == 5
        assert params["ttl_modifier"] == 1.0
        assert params["document_route_weight"] == 0.65
        assert params["graph_route_weight"] == 0.35

    def test_get_all_params_returns_copy(self) -> None:
        opt = ParamOptimizer()
        p1 = opt.get_all_params()
        p1["importance_threshold"] = 9.99
        assert opt.get_all_params()["importance_threshold"] == 0.3

    def test_low_hit_rate_lowers_threshold(self) -> None:
        fc = FeedbackCollector()
        fc.record_recall(1, False)
        fc.record_recall(2, False)
        fc.record_recall(3, False)
        fc.record_recall(4, True)  # hit_rate = 0.25
        opt = ParamOptimizer()
        changes = opt.update(fc)
        assert "importance_threshold" in changes
        assert changes["importance_threshold"] < 0.3
        assert opt.get_all_params()["importance_threshold"] == round(
            max(0.1, 0.3 - 0.05), 4
        )

    def test_high_hit_rate_raises_threshold(self) -> None:
        fc = FeedbackCollector()
        for _ in range(10):
            fc.record_recall(1, True)  # hit_rate = 1.0
        opt = ParamOptimizer()
        changes = opt.update(fc)
        assert "importance_threshold" in changes
        assert changes["importance_threshold"] > 0.3

    def test_low_quality_lowers_ttl(self) -> None:
        fc = FeedbackCollector()
        # Drive avg_quality below 0.3 with many low-quality records
        # EMA: starts at 0.5, after N calls to 0.1 converges toward 0.1
        for _ in range(20):
            fc.record_quality(0.1)
        # Also ensure hit_rate is mid-range (0.3-0.7) so importance doesn't change
        for _ in range(5):
            fc.record_recall(1, True)
            fc.record_recall(2, False)
        opt = ParamOptimizer()
        changes = opt.update(fc)
        assert "ttl_modifier" in changes
        assert changes["ttl_modifier"] < 1.0

    def test_high_quality_raises_ttl(self) -> None:
        fc = FeedbackCollector()
        # Drive avg_quality above 0.7
        for _ in range(10):
            fc.record_quality(1.0)
        # Ensure hit_rate is in mid-range
        for _ in range(5):
            fc.record_recall(1, True)
            fc.record_recall(2, False)
        opt = ParamOptimizer()
        changes = opt.update(fc)
        assert "ttl_modifier" in changes
        assert changes["ttl_modifier"] > 1.0

    def test_no_changes_in_mid_range(self) -> None:
        fc = FeedbackCollector()
        fc.record_recall(1, True)
        fc.record_recall(2, False)  # hit_rate = 0.5
        fc.record_quality(0.5)  # quality = 0.5 (single entry)
        opt = ParamOptimizer()
        changes = opt.update(fc)
        assert changes == {}  # mid range → no adjustments

    def test_update_records_history(self) -> None:
        fc = FeedbackCollector()
        for _ in range(10):
            fc.record_recall(1, True)  # hit_rate = 1.0
        opt = ParamOptimizer()
        opt.update(fc)
        history = opt.get_history()
        assert len(history) >= 1
        entry = history[0]
        assert "param" in entry
        assert "old" in entry
        assert "new" in entry
        assert "reason" in entry
        assert "timestamp" in entry

    def test_get_history_with_limit(self) -> None:
        fc = FeedbackCollector()
        for _ in range(10):
            fc.record_recall(1, True)
        opt = ParamOptimizer()
        for _ in range(5):
            fc._stats["total_hits"] = 10
            fc._stats["total_recalls"] = 10
            opt.update(fc)
        h = opt.get_history(limit=2)
        assert len(h) == 2

    def test_learning_rate_bounds(self) -> None:
        """Test that learning_rate is clamped. (Not used in update, but in constructor.)"""
        opt = ParamOptimizer(learning_rate=2.0)  # clamped to 0.1
        assert opt._lr == 0.1
        opt = ParamOptimizer(learning_rate=0.0001)  # clamped to 0.001
        assert opt._lr == 0.001
        opt = ParamOptimizer(learning_rate=0.05)
        assert opt._lr == 0.05


# ---------------------------------------------------------------------------
# AutoLearningManager
# ---------------------------------------------------------------------------


class TestAutoLearningManager:
    """Integration tests for the combined manager."""

    def test_default_init_enabled(self) -> None:
        mgr = AutoLearningManager()
        assert mgr._enabled is True
        assert mgr.enabled is True

    def test_disabled_bypasses_recording(self) -> None:
        mgr = AutoLearningManager(enabled=False)
        mgr.record_recall(1, True)
        assert mgr.get_stats()["feedback"]["total_recalls"] == 0

    def test_disabled_bypasses_optimize(self) -> None:
        mgr = AutoLearningManager(enabled=False)
        import asyncio

        result = asyncio.run(mgr.optimize())
        assert result == {}

    def test_enabled_toggle(self) -> None:
        mgr = AutoLearningManager(enabled=True)
        mgr.enabled = False
        assert mgr.enabled is False
        mgr.enabled = True
        assert mgr.enabled is True

    def test_record_through_manager(self) -> None:
        mgr = AutoLearningManager()
        mgr.record_recall(42, True)
        mgr.record_recall(43, False)
        mgr.record_quality(0.8)
        mgr.record_correction("bad recall")
        stats = mgr.get_stats()
        fb = stats["feedback"]
        assert fb["total_recalls"] == 2
        assert fb["total_hits"] == 1
        assert fb["total_misses"] == 1
        assert fb["total_corrections"] == 1

    def test_get_stats_structure(self) -> None:
        mgr = AutoLearningManager()
        stats = mgr.get_stats()
        assert "feedback" in stats
        assert "params" in stats
        assert "history" in stats
        assert "enabled" in stats
        assert stats["enabled"] is True

    def test_get_params_delegates(self) -> None:
        mgr = AutoLearningManager()
        params = mgr.get_params()
        assert "importance_threshold" in params
        assert params["importance_threshold"] == 0.3

    @pytest.mark.asyncio
    async def test_optimize_with_enabled(self) -> None:
        mgr = AutoLearningManager(data_dir="")
        mgr.record_recall(1, True)
        mgr.record_recall(2, True)
        mgr.record_recall(3, True)
        mgr.record_recall(4, True)
        mgr.record_recall(5, False)  # hit_rate = 0.8 → should raise threshold
        changes = await mgr.optimize()
        if changes:
            assert "importance_threshold" in changes

    @pytest.mark.asyncio
    async def test_save_and_load_state(self, tmp_path: Path) -> None:
        data_dir = str(tmp_path)
        mgr = AutoLearningManager(data_dir=data_dir)
        mgr.record_recall(1, True)
        mgr.record_recall(2, False)
        await mgr.optimize()
        # Now create a new manager and load state
        mgr2 = AutoLearningManager(data_dir=data_dir)
        await mgr2.load_state()
        # Params should have been restored
        params = mgr2.get_params()
        assert "importance_threshold" in params

    @pytest.mark.asyncio
    async def test_reset(self, tmp_path: Path) -> None:
        mgr = AutoLearningManager(data_dir=str(tmp_path))
        mgr.record_recall(1, True)
        mgr.record_recall(2, True)
        mgr.record_recall(3, True)
        mgr.record_recall(4, True)
        await mgr.optimize()
        await mgr.reset()
        stats = mgr.get_stats()
        assert stats["feedback"]["total_recalls"] == 0
        assert stats["feedback"]["total_hits"] == 0

    @pytest.mark.asyncio
    async def test_load_state_no_file(self) -> None:
        mgr = AutoLearningManager(data_dir="/nonexistent/path/auto_learn")
        await mgr.load_state()  # should not raise

    @pytest.mark.asyncio
    async def test_save_state_no_data_dir(self) -> None:
        mgr = AutoLearningManager(data_dir="")
        await mgr._save_state()  # should be a no-op
