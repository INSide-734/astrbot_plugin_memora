"""自动反思候选存储终态的聚合契约。"""

from __future__ import annotations

from typing import cast

import pytest

from core.features.reflection.domain import SummaryReasonCode, WindowOutcome
from core.features.reflection.domain import storage_outcomes as feature_outcomes

ReflectionStoreOutcome = feature_outcomes.ReflectionStoreOutcome
ReflectionStoreResult = feature_outcomes.ReflectionStoreResult
summarize_store_results = feature_outcomes.summarize_store_results


def test_summarize_store_results_counts_mutually_exclusive_outcomes() -> None:
    """六种终态必须互斥计数，失败项不得提交幂等键。"""

    results = [
        ReflectionStoreResult(ReflectionStoreOutcome.CANONICAL, "a"),
        ReflectionStoreResult(ReflectionStoreOutcome.QUARANTINED, "b"),
        ReflectionStoreResult(ReflectionStoreOutcome.DISCARDED, "c"),
        ReflectionStoreResult(ReflectionStoreOutcome.MARK_WRITE, "d"),
        ReflectionStoreResult(ReflectionStoreOutcome.FAILED, "failed-key"),
        ReflectionStoreResult(ReflectionStoreOutcome.SKIPPED_IDEMPOTENT, "e"),
    ]

    summary = summarize_store_results(results)

    assert summary.canonical_count == 1
    assert summary.quarantine_count == 1
    assert summary.discard_count == 1
    assert summary.mark_write_count == 1
    assert summary.failed_count == 1
    assert summary.skipped_idempotent_count == 1
    assert summary.completed_idempotency_keys == frozenset({"a", "b", "c", "d", "e"})


def test_summarize_store_results_accepts_empty_window() -> None:
    """空候选窗口应产生全零且无幂等键的稳定汇总。"""

    summary = summarize_store_results([])

    assert summary.canonical_count == 0
    assert summary.quarantine_count == 0
    assert summary.discard_count == 0
    assert summary.mark_write_count == 0
    assert summary.failed_count == 0
    assert summary.skipped_idempotent_count == 0
    assert summary.completed_idempotency_keys == frozenset()


def test_no_facts_window_outcome_is_empty_and_advancing() -> None:
    """合法无事实窗口只能以零候选、零写入结果推进。"""

    outcome = WindowOutcome(
        can_advance=True,
        reason_code=SummaryReasonCode.NO_FACTS,
    )

    assert outcome.can_advance is True
    assert outcome.reason_code is SummaryReasonCode.NO_FACTS
    assert outcome.candidate_slots == ()
    assert outcome.canonical_count == 0
    assert outcome.quarantine_count == 0
    assert outcome.discard_count == 0
    assert outcome.mark_write_count == 0
    assert outcome.failed_count == 0
    assert outcome.skipped_idempotent_count == 0
    assert outcome.unknown_count == 0


@pytest.mark.parametrize(
    ("can_advance", "canonical_count"),
    [(False, 0), (True, 1)],
)
def test_no_facts_window_outcome_rejects_non_skip_shapes(
    can_advance: bool,
    canonical_count: int,
) -> None:
    """no-facts reason 不得掩盖未推进或发生写入的窗口。"""

    with pytest.raises(ValueError, match="no_facts"):
        WindowOutcome(
            can_advance=can_advance,
            canonical_count=canonical_count,
            reason_code=SummaryReasonCode.NO_FACTS,
        )


def test_unknown_reason_code_degrades_to_unknown() -> None:
    """未来 reason code 进入安全 DTO 时统一降级为 unknown。"""

    outcome = WindowOutcome(reason_code=cast(SummaryReasonCode, "future_reason_code"))

    assert outcome.reason_code is SummaryReasonCode.UNKNOWN
