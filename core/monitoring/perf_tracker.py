"""召回链路使用的环形缓冲性能跟踪器。"""

from __future__ import annotations

import math
from collections import deque
from typing import Any

# ---------------------------------------------------------------------------
# 有序耗时键集合：每条记录都应包含这些字段
# ---------------------------------------------------------------------------
_TIMING_KEYS: tuple[str, ...] = (
    "total_ms",
    "bm25_ms",
    "vector_ms",
    "graph_ms",
    "rerank_ms",
)


class PerfTracker:
    """轻量级召回链路性能跟踪器。"""

    MAXLEN_DEFAULT: int = 200
    """默认环形缓冲容量。"""

    def __init__(self, maxlen: int = MAXLEN_DEFAULT) -> None:
        """初始化环形缓冲区与 Welford 统计器。"""
        self._maxlen: int = max(maxlen, 1)
        self._samples: deque[dict[str, float]] = deque(maxlen=self._maxlen)

        # Welford 状态：count、mean、M2（到均值平方距离的累计量）
        self._count: dict[str, int] = {k: 0 for k in _TIMING_KEYS}
        self._mean: dict[str, float] = {k: 0.0 for k in _TIMING_KEYS}
        self._m2: dict[str, float] = {k: 0.0 for k in _TIMING_KEYS}

    # ------------------------------------------------------------------
    # 记录数据
    # ------------------------------------------------------------------

    def record(self, sample: dict[str, float]) -> None:
        """追加一条耗时样本，并更新滚动统计值。"""
        evicted = self._samples[0] if len(self._samples) == self._maxlen else None
        self._samples.append(sample)
        if evicted is not None:
            self._rebuild_stats()
            return
        for key in _TIMING_KEYS:
            value = sample.get(key)
            if value is None:
                continue
            self._count[key] += 1
            delta: float = value - self._mean[key]
            self._mean[key] += delta / self._count[key]
            delta2: float = value - self._mean[key]
            self._m2[key] += delta * delta2

    # ------------------------------------------------------------------
    # 查询数据
    # ------------------------------------------------------------------

    def get_perf_data(self, recent_limit: int = 50) -> dict[str, Any]:
        """返回汇总统计信息以及最近的样本列表。"""
        stats: dict[str, Any] = {}
        for key in _TIMING_KEYS:
            stats[f"avg_{key}"] = round(self._mean[key], 4)
            stats[f"std_{key}"] = round(self._std(key), 4)
            stats[f"count_{key}"] = self._count[key]

        limit = min(max(recent_limit, 0), len(self._samples))
        stats["recent"] = list(self._samples)[-limit:] if limit else []
        return stats

    # ------------------------------------------------------------------
    # 百分位辅助函数
    # ------------------------------------------------------------------

    def get_percentile(self, key: str, p: float) -> float | None:
        """计算指定键的 p 分位数（0 到 100）。"""
        if not self._samples:
            return None
        values = sorted(s.get(key, 0.0) for s in self._samples if key in s)
        if not values:
            return None
        if p <= 0:
            return values[0]
        if p >= 100:
            return values[-1]
        k = (p / 100.0) * (len(values) - 1)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return values[int(k)]
        d0 = values[f] * (c - k)
        d1 = values[c] * (k - f)
        return round(d0 + d1, 4)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _std(self, key: str) -> float:
        """总体标准差；样本少于 2 条时返回 0.0。"""
        n = self._count[key]
        if n < 2:
            return 0.0
        return math.sqrt(self._m2[key] / n)

    def _rebuild_stats(self) -> None:
        """基于当前保留的样本重新计算滚动统计值。"""
        self._count = {k: 0 for k in _TIMING_KEYS}
        self._mean = {k: 0.0 for k in _TIMING_KEYS}
        self._m2 = {k: 0.0 for k in _TIMING_KEYS}

        for retained in self._samples:
            for key in _TIMING_KEYS:
                value = retained.get(key)
                if value is None:
                    continue
                self._count[key] += 1
                delta = value - self._mean[key]
                self._mean[key] += delta / self._count[key]
                delta2 = value - self._mean[key]
                self._m2[key] += delta * delta2

    def __len__(self) -> int:
        """返回已记录样本数。"""
        return len(self._samples)

    def __repr__(self) -> str:
        return (
            f"<PerfTracker samples={len(self._samples)}/{self._maxlen}"
            f" avg_total_ms={self._mean['total_ms']:.2f}>"
        )
