"""提供跨层 canonical 写入阶段计时上下文。"""

from __future__ import annotations

import contextvars
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import ParamSpec, TypeVar

from .debug_reporter import report_debug_event

_WRITE_STAGES = ("document_vector", "fts", "atom", "graph", "evolution")
P = ParamSpec("P")
R = TypeVar("R")


@dataclass(slots=True)
class MemoryWriteTiming:
    """聚合一次 canonical 写入的固定阶段耗时。"""

    durations_ms: dict[str, float] = field(default_factory=dict)
    versions: dict[str, int] = field(default_factory=dict)
    emitted: bool = False

    def record(self, stage: str, duration_ms: float) -> None:
        """累加一个固定阶段的非负耗时并推进版本。"""

        if stage not in _WRITE_STAGES:
            return
        self.durations_ms[stage] = self.durations_ms.get(stage, 0.0) + max(
            0.0, float(duration_ms)
        )
        self.versions[stage] = self.versions.get(stage, 0) + 1

    def emit(self) -> None:
        """按稳定顺序发射一次不含正文或标识符的阶段事件。"""

        if self.emitted:
            return
        self.emitted = True
        for stage in _WRITE_STAGES:
            if stage not in self.durations_ms:
                continue
            report_debug_event(
                "storage_task",
                component="memory_engine",
                stage=stage,
                status="completed",
                reason_code="memory_write_stage_completed",
                task_type="storage",
                duration_ms=self.durations_ms[stage],
            )


_active_write_timing: contextvars.ContextVar[MemoryWriteTiming | None] = (
    contextvars.ContextVar("memora_memory_write_timing", default=None)
)


@contextmanager
def memory_write_timing_scope() -> Iterator[MemoryWriteTiming]:
    """创建或复用当前异步上下文中的 canonical 写入计时器。"""

    existing = _active_write_timing.get()
    if existing is not None:
        yield existing
        return
    timing = MemoryWriteTiming()
    token = _active_write_timing.set(timing)
    try:
        yield timing
    finally:
        _active_write_timing.reset(token)


@contextmanager
def measure_memory_write_stage(stage: str) -> Iterator[None]:
    """记录固定写入阶段；若内层记录同名阶段则忽略外层重叠耗时。"""

    timing = _active_write_timing.get()
    if timing is None or stage not in _WRITE_STAGES:
        yield
        return
    version_at_start = timing.versions.get(stage, 0)
    started = time.perf_counter()
    try:
        yield
    finally:
        if timing.versions.get(stage, 0) == version_at_start:
            timing.record(stage, (time.perf_counter() - started) * 1000.0)


def observe_memory_write(
    function: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    """为异步 canonical 写入口创建计时作用域并在成功返回后发射。"""

    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with memory_write_timing_scope() as timing:
            result = await function(*args, **kwargs)
            timing.emit()
            return result

    return wrapped


__all__ = [
    "MemoryWriteTiming",
    "measure_memory_write_stage",
    "memory_write_timing_scope",
    "observe_memory_write",
]
