"""性能指标收集器 — P50/P95/P99 延迟 + 缓存命中率。"""

from __future__ import annotations

import bisect
import time
from collections import deque
from contextlib import contextmanager
from typing import Any


class MetricsCollector:
    """单例指标收集器，用于关键操作的延迟统计。

    追踪每个操作的延迟分布 (P50/P95/P99) 和搜索缓存命中率。
    得益于 bisect 维护的有序性，所有操作插入为 O(log n)，
    百分位查询为 O(1)。

    用法::

        metrics = MetricsCollector()
        with metrics.measure("search") as ctx:
            results = await retriever.search(query)
            ctx["result_count"] = len(results)
    """

    _instance: MetricsCollector | None = None

    def __new__(cls) -> MetricsCollector:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._max_samples = 1000
        self._latencies: dict[str, deque[float]] = {}
        self._cache_hits: int = 0
        self._cache_misses: int = 0

    # ---- 测量 -------------------------------------------------

    @contextmanager
    def measure(self, operation: str):
        """上下文管理器，用于测量操作延迟。

        产生一个调用者可注释的可变上下文字典::

            with metrics.measure("add_memory") as ctx:
                doc_id = await lifecycle.add_memory(content)
                ctx["doc_id"] = doc_id
        """
        ctx: dict[str, Any] = {}
        start = time.perf_counter()
        try:
            yield ctx
        finally:
            elapsed = time.perf_counter() - start
            self._record(operation, elapsed)

    def _record(self, operation: str, elapsed: float) -> None:
        if operation not in self._latencies:
            self._latencies[operation] = deque(maxlen=self._max_samples)
        bisect.insort(self._latencies[operation], elapsed)

    # ---- 缓存统计 -------------------------------------------------

    def record_cache_hit(self) -> None:
        self._cache_hits += 1

    def record_cache_miss(self) -> None:
        self._cache_misses += 1

    @property
    def cache_hit_rate(self) -> float:
        total = self._cache_hits + self._cache_misses
        if total == 0:
            return 0.0
        return self._cache_hits / total

    # ---- 统计查询 -----------------------------------------------

    def get_stats(self, operation: str) -> dict[str, float]:
        """返回 *operation* 的 {p50, p95, p99, count, min, max}。"""
        dq = self._latencies.get(operation)
        if not dq:
            return {}
        n = len(dq)
        return {
            "count": n,
            "min": dq[0],
            "max": dq[-1],
            "p50": dq[int(n * 0.50)],
            "p95": dq[min(int(n * 0.95), n - 1)],
            "p99": dq[min(int(n * 0.99), n - 1)],
        }

    def get_all_stats(self) -> dict[str, dict[str, float]]:
        return {op: self.get_stats(op) for op in self._latencies}

    # ---- 生命周期 ---------------------------------------------------

    def reset(self) -> None:
        """清除所有累计指标。"""
        self._latencies.clear()
        self._cache_hits = 0
        self._cache_misses = 0


__all__ = ["MetricsCollector"]
