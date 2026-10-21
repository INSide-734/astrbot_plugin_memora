"""确定性配对 bootstrap 统计。

保留既有两条评测路径的随机数/百分位语义：topic 候选使用 LCG 与索引
百分位，feedback 重放使用 ``random.Random`` 与线性百分位。调用方负责
把稳定的 seed 材料转换为整数。
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

__all__ = [
    "DeterministicRandom",
    "nearest_rank",
    "paired_bootstrap_mean_ci",
    "paired_p95_delta_bootstrap_ci",
]


class DeterministicRandom:
    """确定性伪随机数生成器；LCG 实现。"""

    def __init__(self, seed: int) -> None:
        self._state = seed & 0xFFFFFFFFFFFFFFFF
        self._a = 6364136223846793005
        self._c = 1442695040888963407
        self._m = 2**64

    def randint(self, lower: int, upper: int) -> int:
        """返回 [lower, upper] 范围内的随机整数。"""

        self._state = (self._a * self._state + self._c) % self._m
        return lower + int((self._state / self._m) * (upper - lower + 1))


def paired_bootstrap_mean_ci(
    differences: Sequence[float],
    *,
    confidence: float,
    iterations: int,
    seed: int,
    rng: str = "lcg",
    interpolation: bool = False,
) -> tuple[float, float] | None:
    """对配对差值重采样并返回双侧均值置信区间；样本不足返回 None。"""

    values = [float(value) for value in differences]
    if len(values) < 2 or not 0.0 < confidence < 1.0 or iterations <= 0:
        return None
    if min(values) == max(values):
        return (values[0], values[0])

    size = len(values)
    if rng == "python":
        python_rng = random.Random(seed)
        estimates = sorted(
            sum(values[python_rng.randrange(size)] for _ in range(size)) / size
            for _ in range(iterations)
        )
    else:
        lcg = DeterministicRandom(seed)
        estimates = sorted(
            sum(values[lcg.randint(0, size - 1)] for _ in range(size)) / size
            for _ in range(iterations)
        )

    alpha = 1.0 - confidence
    if interpolation:
        tail = alpha * 50.0
        return (
            _linear_percentile(estimates, tail),
            _linear_percentile(estimates, 100.0 - tail),
        )
    lower_index = int(alpha / 2 * iterations)
    upper_index = min(iterations - 1, int((1 - alpha / 2) * iterations))
    return (estimates[lower_index], estimates[upper_index])


def nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    """使用 nearest-rank 定义计算百分位；空样本返回 None。"""

    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100.0 * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def paired_p95_delta_bootstrap_ci(
    paired: Sequence[tuple[float, float]],
    *,
    confidence: float,
    iterations: int,
    seed: int,
) -> tuple[float, float] | None:
    """对配对样本重采样并返回 candidate_p95 - baseline_p95 的区间。"""

    values = [(float(baseline), float(candidate)) for baseline, candidate in paired]
    if len(values) < 2 or not 0.0 < confidence < 1.0 or iterations <= 0:
        return None
    if min(values) == max(values):
        delta = values[0][1] - values[0][0]
        return (delta, delta)
    size = len(values)
    generator = DeterministicRandom(seed)
    deltas: list[float] = []
    for _ in range(iterations):
        sample = [values[generator.randint(0, size - 1)] for _ in range(size)]
        baseline_p95 = nearest_rank([item[0] for item in sample], 95.0)
        candidate_p95 = nearest_rank([item[1] for item in sample], 95.0)
        if baseline_p95 is None or candidate_p95 is None:
            return None
        deltas.append(candidate_p95 - baseline_p95)
    deltas.sort()
    alpha = 1.0 - confidence
    lower_index = int(alpha / 2 * iterations)
    upper_index = min(iterations - 1, int((1 - alpha / 2) * iterations))
    return (deltas[lower_index], deltas[upper_index])


def _linear_percentile(
    values: Sequence[float],
    percentile: float,
    *,
    digits: int = 6,
) -> float:
    """计算确定性线性百分位；调用方保证样本非空。"""

    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], digits)
    weight = position - lower
    return round(
        ordered[lower] * (1 - weight) + ordered[upper] * weight,
        digits,
    )
