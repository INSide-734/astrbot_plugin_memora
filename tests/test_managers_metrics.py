"""测试 MetricsCollector — 延迟追踪与缓存统计。"""

from __future__ import annotations

import time

import pytest

from core.managers.metrics_collector import MetricsCollector


class TestMetricsSingleton:
    """单例模式测试。"""

    def test_singleton_returns_same_instance(self) -> None:
        """多次构造返回同一个实例。"""
        m1 = MetricsCollector()
        m2 = MetricsCollector()
        assert m1 is m2

    def test_singleton_resets(self) -> None:
        """reset 清除累计数据。"""
        m = MetricsCollector()
        m.record_cache_hit()
        m.reset()
        assert m.cache_hit_rate == 0.0


class TestCacheStats:
    """缓存命中/未命中追踪测试。"""

    def test_cache_hit_rate_initial(self) -> None:
        """初始缓存命中率为 0.0（无数据）。"""
        m = MetricsCollector()
        m.reset()
        assert m.cache_hit_rate == 0.0

    def test_cache_hit_rate_with_data(self) -> None:
        """缓存命中率 = hits / (hits + misses)。"""
        m = MetricsCollector()
        m.reset()
        m.record_cache_hit()
        m.record_cache_hit()
        m.record_cache_miss()
        assert m.cache_hit_rate == pytest.approx(2 / 3)

    def test_cache_hit_rate_all_hits(self) -> None:
        """全部命中时命中率为 1.0。"""
        m = MetricsCollector()
        m.reset()
        for _ in range(5):
            m.record_cache_hit()
        assert m.cache_hit_rate == 1.0

    def test_cache_hit_rate_all_misses(self) -> None:
        """全部未命中时命中率为 0.0。"""
        m = MetricsCollector()
        m.reset()
        for _ in range(3):
            m.record_cache_miss()
        assert m.cache_hit_rate == 0.0


class TestMeasurement:
    """延迟测量上下文管理器测试。"""

    def test_measure_records_latency(self) -> None:
        """measure 上下文管理器记录操作延迟。"""
        m = MetricsCollector()
        m.reset()
        with m.measure("test_op") as ctx:
            ctx["key"] = "value"
        stats = m.get_stats("test_op")
        assert stats["count"] == 1
        assert stats["min"] > 0  # 至少经过了一些时间
        assert stats["max"] > 0

    def test_measure_multiple_operations(self) -> None:
        """多次测量全部被记录。"""
        m = MetricsCollector()
        m.reset()
        with m.measure("op_a"):
            pass
        with m.measure("op_a"):
            pass
        with m.measure("op_b"):
            pass
        stats_a = m.get_stats("op_a")
        stats_b = m.get_stats("op_b")
        assert stats_a["count"] == 2
        assert stats_b["count"] == 1

    def test_measure_exception_still_records(self) -> None:
        """measure 内部抛出异常时仍然记录耗时。"""
        m = MetricsCollector()
        m.reset()
        try:
            with m.measure("failing_op"):
                raise ValueError("test")
        except ValueError:
            pass
        stats = m.get_stats("failing_op")
        assert stats["count"] == 1


class TestLatencyStats:
    """百分位查询测试。"""

    def test_get_stats_unknown_operation(self) -> None:
        """未知操作返回空字典。"""
        m = MetricsCollector()
        m.reset()
        assert m.get_stats("nonexistent") == {}

    def test_percentiles_are_monotonic(self) -> None:
        """P50 <= P95 <= P99。"""
        m = MetricsCollector()
        m.reset()
        # 通过 measure 插入已知延迟
        for delay in [0.01, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 1.0, 2.0]:
            with m.measure("op"):
                time.sleep(delay)
        stats = m.get_stats("op")
        assert (
            stats["min"] <= stats["p50"] <= stats["p95"] <= stats["p99"] <= stats["max"]
        )

    def test_get_all_stats(self) -> None:
        """get_all_stats 返回所有操作的统计。"""
        m = MetricsCollector()
        m.reset()
        with m.measure("op_a"):
            pass
        with m.measure("op_b"):
            pass
        all_stats = m.get_all_stats()
        assert "op_a" in all_stats
        assert "op_b" in all_stats
