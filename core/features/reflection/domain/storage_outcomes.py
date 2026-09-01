"""定义自动反思候选写入的互斥终态。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum


class ReflectionStoreOutcome(str, Enum):
    """表示一条反思候选的互斥终态。"""

    CANONICAL = "canonical"
    QUARANTINED = "quarantined"
    DISCARDED = "discarded"
    MARK_WRITE = "mark_write"
    FAILED = "failed"
    SKIPPED_IDEMPOTENT = "skipped_idempotent"


@dataclass(frozen=True, slots=True)
class ReflectionStoreResult:
    """保存单条候选结果、幂等键及可选 canonical ID。"""

    outcome: ReflectionStoreOutcome
    idempotency_key: str = ""
    canonical_id: int | None = None


@dataclass(frozen=True, slots=True)
class ReflectionStoreSummary:
    """保存单窗口的互斥结果计数。"""

    canonical_count: int
    quarantine_count: int
    discard_count: int
    mark_write_count: int
    failed_count: int
    skipped_idempotent_count: int
    completed_idempotency_keys: frozenset[str]


def summarize_store_results(
    results: Iterable[ReflectionStoreResult],
) -> ReflectionStoreSummary:
    """汇总候选终态，并仅提交非失败结果的幂等键。

    Args:
        results: 单条候选的互斥存储结果。

    Returns:
        各终态计数及可写入 pending 状态的已完成幂等键。
    """

    counts = {outcome: 0 for outcome in ReflectionStoreOutcome}
    completed_keys: set[str] = set()
    for result in results:
        counts[result.outcome] += 1
        if (
            result.outcome is not ReflectionStoreOutcome.FAILED
            and result.idempotency_key
        ):
            completed_keys.add(result.idempotency_key)
    return ReflectionStoreSummary(
        canonical_count=counts[ReflectionStoreOutcome.CANONICAL],
        quarantine_count=counts[ReflectionStoreOutcome.QUARANTINED],
        discard_count=counts[ReflectionStoreOutcome.DISCARDED],
        mark_write_count=counts[ReflectionStoreOutcome.MARK_WRITE],
        failed_count=counts[ReflectionStoreOutcome.FAILED],
        skipped_idempotent_count=counts[ReflectionStoreOutcome.SKIPPED_IDEMPOTENT],
        completed_idempotency_keys=frozenset(completed_keys),
    )


__all__ = [
    "ReflectionStoreOutcome",
    "ReflectionStoreResult",
    "ReflectionStoreSummary",
    "summarize_store_results",
]
