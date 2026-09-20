"""派生重建的低基数、隐私安全测量辅助。"""

from __future__ import annotations

import contextvars
import math
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

REBUILD_TRIGGER_REASONS = frozenset(
    {
        "indexes_inconsistent",
        "catalog_dirty",
        "indexes_and_catalog",
        "indexes_consistent",
        "unknown",
    }
)
_REBUILD_STAGE_NAMES = frozenset(
    {
        "canonical",
        "indexes",
        "bm25",
        "vector",
        "catalog",
        "atoms",
        "graph",
        "evolution",
        "semantic_compression",
        "notes",
        "rebuild",
        "unknown",
    }
)
_REBUILD_STATUSES = frozenset({"completed", "failed", "skipped", "cancelled"})
_ACTIVE_MEASUREMENT: contextvars.ContextVar["RebuildMeasurement | None"] = (
    contextvars.ContextVar("memora_rebuild_measurement", default=None)
)


def normalize_rebuild_trigger(value: Any) -> str:
    """把触发原因限制为固定的低基数闭集。"""

    reason = value.strip() if isinstance(value, str) else ""
    return reason if reason in REBUILD_TRIGGER_REASONS else "unknown"


def classify_rebuild_trigger(
    indexes_inconsistent: bool,
    catalog_dirty: bool,
) -> str:
    """按索引和目录状态确定启动重建触发原因。"""

    if indexes_inconsistent and catalog_dirty:
        return "indexes_and_catalog"
    if indexes_inconsistent:
        return "indexes_inconsistent"
    if catalog_dirty:
        return "catalog_dirty"
    return "indexes_consistent"


def _safe_nonnegative_int(value: Any) -> int | None:
    """读取非负整数；拒绝布尔值、浮点和异常文本。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _count_fields(result: Mapping[str, Any] | None) -> dict[str, int | None]:
    """从已有结果字段提取安全计数，不从 processed 猜测 total。"""

    if not isinstance(result, Mapping):
        return {"processed": None, "failed": None, "total": None}
    processed = _safe_nonnegative_int(result.get("processed"))
    failed = _safe_nonnegative_int(result.get("failed"))
    if failed is None:
        failed = _safe_nonnegative_int(result.get("errors"))
    if processed is None:
        processed = _safe_nonnegative_int(result.get("rebuilt"))
    if processed is None:
        processed = _safe_nonnegative_int(result.get("canonical_sources"))
    if processed is None:
        processed = _safe_nonnegative_int(result.get("created"))
    total = _safe_nonnegative_int(result.get("total"))
    if total is None and processed is not None:
        skipped = _safe_nonnegative_int(result.get("skipped"))
        if skipped is not None:
            total = processed + skipped
    documents = _safe_nonnegative_int(result.get("documents"))
    if documents is not None:
        processed = documents
        total = documents
    return {"processed": processed, "failed": failed, "total": total}


def _safe_duration(value: Any) -> float:
    """返回有限、非负的秒数标量。"""

    try:
        duration = float(value)
    except (TypeError, ValueError):
        return 0.0
    return duration if math.isfinite(duration) and duration >= 0 else 0.0


@dataclass(slots=True)
class RebuildMeasurement:
    """聚合一次重建的固定阶段计时、计数和物理 embedding 次数。"""

    trigger_reason: str
    started_at: float = field(default_factory=time.perf_counter)
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    processed: int | None = None
    failed: int | None = None
    total: int | None = None
    embedding_requests: int = 0
    embedding_batches: int = 0

    def record_stage(
        self,
        name: str,
        duration_seconds: float,
        result: Mapping[str, Any] | None = None,
        *,
        status: str = "completed",
    ) -> None:
        """记录阶段安全标量；阶段名称和状态均为固定闭集。"""

        stage_name = name if name in _REBUILD_STAGE_NAMES else "unknown"
        safe_status = status if status in _REBUILD_STATUSES else "failed"
        self.stages[stage_name] = {
            "status": safe_status,
            "duration_seconds": _safe_duration(duration_seconds),
            **_count_fields(result),
        }

    def set_totals_from_result(self, result: Mapping[str, Any] | None) -> None:
        """保存重建结果中明确提供的处理、失败和总数。"""

        counts = _count_fields(result)
        if isinstance(result, Mapping) and counts["processed"] is None:
            stages = result.get("stages")
            if isinstance(stages, Mapping):
                counts = _count_fields(stages.get("indexes"))
        self.processed = counts["processed"]
        self.failed = counts["failed"]
        self.total = counts["total"]

    def record_embedding_request(self) -> None:
        """记录一次已经进入底层 embedding 调用边界的请求。"""

        self.embedding_requests += 1

    def record_embedding_batch(self) -> None:
        """记录一个实际提交给 embedding adapter 的逻辑批次。"""

        self.embedding_batches += 1

    def snapshot(self, *, duration_seconds: float | None = None) -> dict[str, Any]:
        """返回只含固定枚举和数值的测量快照。"""

        elapsed = (
            _safe_duration(duration_seconds)
            if duration_seconds is not None
            else _safe_duration(time.perf_counter() - self.started_at)
        )
        return {
            "trigger_reason": normalize_rebuild_trigger(self.trigger_reason),
            "duration_seconds": elapsed,
            "processed": self.processed,
            "failed": self.failed,
            "total": self.total,
            "embedding_requests": max(0, int(self.embedding_requests)),
            "embedding_batches": max(0, int(self.embedding_batches)),
            "stages": {
                name: dict(stage) for name, stage in sorted(self.stages.items())
            },
        }


@contextmanager
def rebuild_measurement_scope(trigger_reason: Any) -> Iterator[RebuildMeasurement]:
    """创建或复用当前异步上下文中的重建测量器。"""

    current = _ACTIVE_MEASUREMENT.get()
    if current is not None:
        yield current
        return
    measurement = RebuildMeasurement(normalize_rebuild_trigger(trigger_reason))
    token = _ACTIVE_MEASUREMENT.set(measurement)
    try:
        yield measurement
    finally:
        _ACTIVE_MEASUREMENT.reset(token)


def current_rebuild_measurement() -> RebuildMeasurement | None:
    """返回当前异步上下文的重建测量器。"""

    return _ACTIVE_MEASUREMENT.get()


def record_embedding_request() -> None:
    """在当前重建测量中记录一次物理 embedding 请求。"""

    measurement = current_rebuild_measurement()
    if measurement is not None:
        measurement.record_embedding_request()


def record_embedding_batch() -> None:
    """在当前重建测量中记录一个 embedding 逻辑批次。"""

    measurement = current_rebuild_measurement()
    if measurement is not None:
        measurement.record_embedding_batch()


def attach_rebuild_observability(
    result: Mapping[str, Any] | None,
    measurement: RebuildMeasurement,
    *,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    """在保留既有结果字段的同时附加安全测量。"""

    output = dict(result) if isinstance(result, Mapping) else {}
    measurement.set_totals_from_result(output)
    snapshot = measurement.snapshot(duration_seconds=duration_seconds)
    output["observability"] = snapshot
    output.setdefault("trigger_reason", snapshot["trigger_reason"])
    output.setdefault("embedding_requests", snapshot["embedding_requests"])
    output.setdefault("embedding_batches", snapshot["embedding_batches"])
    return output


def finalize_rebuild_observability(
    result: Mapping[str, Any] | None,
    measurement: RebuildMeasurement,
    *,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    """按索引阶段优先、canonical 回退汇总已知重建计数。"""

    output = dict(result) if isinstance(result, Mapping) else {}
    index_stage = measurement.stages.get("indexes")
    canonical_stage = measurement.stages.get("canonical")
    # 只有确实给出计数的阶段才能覆盖测量：skipped/failed 的 indexes 阶段
    # 会带入全 None，直接采用会把 canonical 已记录的文档数冲掉。
    for stage in (index_stage, canonical_stage):
        if isinstance(stage, Mapping) and stage.get("processed") is not None:
            measurement.processed = stage.get("processed")
            measurement.failed = stage.get("failed")
            measurement.total = stage.get("total")
            break
    snapshot = measurement.snapshot(duration_seconds=duration_seconds)
    output["observability"] = snapshot
    output.setdefault("trigger_reason", snapshot["trigger_reason"])
    output.setdefault("embedding_requests", snapshot["embedding_requests"])
    output.setdefault("embedding_batches", snapshot["embedding_batches"])
    return output


def closed_rebuild_observability(reason: Any) -> dict[str, Any]:
    """为未触发重建返回兼容的零工作测量。"""

    measurement = RebuildMeasurement(normalize_rebuild_trigger(reason))
    measurement.processed = 0
    measurement.failed = 0
    measurement.total = 0
    return measurement.snapshot(duration_seconds=0.0)


__all__ = [
    "REBUILD_TRIGGER_REASONS",
    "RebuildMeasurement",
    "attach_rebuild_observability",
    "classify_rebuild_trigger",
    "closed_rebuild_observability",
    "current_rebuild_measurement",
    "normalize_rebuild_trigger",
    "record_embedding_batch",
    "record_embedding_request",
    "rebuild_measurement_scope",
]
