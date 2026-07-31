"""LLM 请求关键路径的局部计时与软截止时间。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..managers.retrieval_timing import RetrievalTimingSink


@dataclass(slots=True)
class RecallTimingContext:
    """贯穿一次 LLM 请求召回链的局部计时上下文。"""

    started_monotonic: float
    deadline_monotonic: float | None
    retrieval: RetrievalTimingSink = field(default_factory=RetrievalTimingSink)

    @classmethod
    def start(
        cls,
        soft_budget_ms: int | float,
        *,
        started_monotonic: float | None = None,
    ) -> "RecallTimingContext":
        """从请求钩子起点创建上下文；预算为零时不设置截止时间。"""

        started = (
            time.perf_counter()
            if started_monotonic is None
            else float(started_monotonic)
        )
        budget_ms = max(0.0, float(soft_budget_ms))
        deadline = started + budget_ms / 1000.0 if budget_ms > 0.0 else None
        return cls(started_monotonic=started, deadline_monotonic=deadline)

    def record(self, key: str, value: object) -> None:
        """记录请求关键路径中的单个安全标量。"""

        self.retrieval.record(key, value)

    def record_elapsed(self, key: str, started_monotonic: float) -> float:
        """记录从给定单调时钟起点到当前时刻的非负毫秒耗时。"""

        elapsed_ms = max(0.0, (time.perf_counter() - started_monotonic) * 1000.0)
        self.record(key, elapsed_ms)
        return elapsed_ms

    def remaining_seconds(self, *, now_monotonic: float | None = None) -> float | None:
        """返回软截止时间剩余秒数；未启用预算时返回 ``None``。"""

        if self.deadline_monotonic is None:
            return None
        now = time.perf_counter() if now_monotonic is None else now_monotonic
        return max(0.0, self.deadline_monotonic - float(now))

    def snapshot(self) -> dict[str, float | int | bool | str]:
        """导出当前请求的隐私安全计时快照。"""

        return self.retrieval.snapshot()


__all__ = ["RecallTimingContext"]
