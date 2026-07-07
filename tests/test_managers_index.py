"""IndexManager 测试 — 增量索引重建 + IVF 推荐。"""

from __future__ import annotations

import pytest

from core.managers.index_manager import IndexManager


class TestRecordNewVectors:
    """Tests for recording new vector counts."""

    def test_initial_counts_are_zero(self) -> None:
        """Newly created IndexManager starts at 0."""
        mgr = IndexManager()
        s = mgr.stats
        assert s["total_vectors"] == 0
        assert s["incremental_pending"] == 0

    def test_record_increments_counts(self) -> None:
        """Recording vectors increases both counters."""
        mgr = IndexManager()
        mgr.record_new_vectors(10)
        mgr.record_new_vectors(5)
        s = mgr.stats
        assert s["total_vectors"] == 15
        assert s["incremental_pending"] == 15

    def test_record_negative_clamped(self) -> None:
        """Negative count is clamped to 0."""
        mgr = IndexManager()
        mgr.record_new_vectors(-5)
        s = mgr.stats
        assert s["total_vectors"] == 0
        assert s["incremental_pending"] == 0


class TestIVFRecommendation:
    """Tests for IVF switch recommendation."""

    def test_recommend_flat_when_below_threshold(self) -> None:
        """FlatL2 is recommended when vector count < 10000."""
        mgr = IndexManager()
        mgr.record_new_vectors(100)
        rec = mgr.check_ivf_recommendation()
        assert rec["should_switch"] is False
        assert rec["recommended_type"] == "FlatL2"

    def test_recommend_ivf_when_above_threshold(self) -> None:
        """IVF is recommended when vector count >= 10000."""
        mgr = IndexManager()
        mgr.record_new_vectors(10000)
        rec = mgr.check_ivf_recommendation()
        assert rec["should_switch"] is True
        assert "IVF" in rec["recommended_type"]

    def test_mark_index_type_updates_stats(self) -> None:
        """mark_index_type updates the current type in stats."""
        mgr = IndexManager()
        mgr.mark_index_type("IVF4096,Flat")
        assert mgr.stats["index_type"] == "IVF4096,Flat"


class TestMaybeRebuild:
    """Tests for the maybe_rebuild logic."""

    @pytest.mark.asyncio
    async def test_skipped_below_threshold(self) -> None:
        """Rebuild is skipped when incremental count < 500."""
        mgr = IndexManager()
        mgr.record_new_vectors(10)
        result = await mgr.maybe_rebuild()
        assert result["skipped"] == "below_threshold"
        assert result["incremental"] == 10

    @pytest.mark.asyncio
    async def test_skipped_no_callback(self) -> None:
        """Rebuild is skipped when no callback is registered."""
        mgr = IndexManager()
        mgr.record_new_vectors(600)
        result = await mgr.maybe_rebuild()
        assert result["skipped"] == "no_callback"


class TestStats:
    """Tests for the stats property."""

    def test_stats_contains_all_fields(self) -> None:
        """Stats dict contains expected keys."""
        mgr = IndexManager()
        s = mgr.stats
        assert "index_type" in s
        assert "total_vectors" in s
        assert "incremental_pending" in s
        assert "ivf_recommended" in s
        assert "ivf_threshold" in s
        assert "incremental_threshold" in s
        assert "last_rebuild_at" in s
