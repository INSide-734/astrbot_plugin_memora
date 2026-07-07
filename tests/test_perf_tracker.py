"""测试 PerfTracker 滚动统计与百分位行为。"""

from __future__ import annotations

import math

from core.monitoring.perf_tracker import PerfTracker


def _sample(
    total: float,
    bm25: float | None = None,
    vector: float | None = None,
    graph: float | None = None,
    rerank: float | None = None,
) -> dict[str, float]:
    return {
        "total_ms": total,
        "bm25_ms": total if bm25 is None else bm25,
        "vector_ms": total if vector is None else vector,
        "graph_ms": total if graph is None else graph,
        "rerank_ms": total if rerank is None else rerank,
    }


class TestPerfTrackerRollingStats:
    def test_keeps_rolling_average_aligned_with_retained_ring_buffer(self) -> None:
        tracker = PerfTracker(maxlen=3)

        tracker.record(_sample(10.0))
        tracker.record(_sample(20.0))
        tracker.record(_sample(30.0))
        tracker.record(_sample(40.0))

        stats = tracker.get_perf_data()

        # Ring buffer should retain only [20, 30, 40].
        assert len(tracker) == 3
        assert stats["recent"] == [_sample(20.0), _sample(30.0), _sample(40.0)]
        assert stats["count_total_ms"] == 3
        assert stats["avg_total_ms"] == 30.0

    def test_recomputes_std_from_retained_samples_after_overflow(self) -> None:
        tracker = PerfTracker(maxlen=3)

        tracker.record(_sample(10.0))
        tracker.record(_sample(20.0))
        tracker.record(_sample(30.0))
        tracker.record(_sample(40.0))

        stats = tracker.get_perf_data()
        expected = math.sqrt((100.0 + 0.0 + 100.0) / 3.0)
        assert stats["std_total_ms"] == round(expected, 4)

    def test_recent_limit_is_clamped_to_available_samples(self) -> None:
        tracker = PerfTracker(maxlen=5)
        tracker.record(_sample(1.0))
        tracker.record(_sample(2.0))

        stats = tracker.get_perf_data(recent_limit=10)

        assert stats["recent"] == [_sample(1.0), _sample(2.0)]

    def test_rebuild_preserves_per_key_counts_for_partial_retained_samples(self) -> None:
        tracker = PerfTracker(maxlen=2)

        tracker.record(_sample(20.0))
        tracker.record({"total_ms": 30.0})
        tracker.record(_sample(40.0))

        stats = tracker.get_perf_data()

        assert stats["recent"] == [{"total_ms": 30.0}, _sample(40.0)]
        assert stats["count_total_ms"] == 2
        assert stats["avg_total_ms"] == 35.0
        assert stats["count_bm25_ms"] == 1
        assert stats["avg_bm25_ms"] == 40.0
        assert stats["std_bm25_ms"] == 0.0

    def test_maxlen_is_clamped_to_at_least_one_sample(self) -> None:
        tracker = PerfTracker(maxlen=0)

        tracker.record(_sample(10.0))
        tracker.record(_sample(20.0))

        stats = tracker.get_perf_data()

        assert len(tracker) == 1
        assert stats["recent"] == [_sample(20.0)]
        assert stats["count_total_ms"] == 1
        assert stats["avg_total_ms"] == 20.0


class TestPerfTrackerPercentiles:
    def test_returns_interpolated_percentile_for_retained_values(self) -> None:
        tracker = PerfTracker(maxlen=4)
        for value in (10.0, 20.0, 30.0, 40.0):
            tracker.record(_sample(value))

        assert tracker.get_percentile("total_ms", 50) == 25.0
        assert tracker.get_percentile("total_ms", 95) == 38.5

    def test_returns_none_when_key_has_no_values(self) -> None:
        tracker = PerfTracker()
        tracker.record({"total_ms": 10.0})

        assert tracker.get_percentile("graph_ms", 50) is None

    def test_clamps_percentiles_to_extremes(self) -> None:
        tracker = PerfTracker()
        for value in (5.0, 15.0, 25.0):
            tracker.record(_sample(value))

        assert tracker.get_percentile("total_ms", -1) == 5.0
        assert tracker.get_percentile("total_ms", 101) == 25.0


class TestPerfTrackerRepresentation:
    def test_repr_reflects_capacity_and_mean(self) -> None:
        tracker = PerfTracker(maxlen=2)
        tracker.record(_sample(12.5))

        text = repr(tracker)

        assert "samples=1/2" in text
        assert "avg_total_ms=12.50" in text
