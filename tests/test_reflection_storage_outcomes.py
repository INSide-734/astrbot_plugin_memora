"""自动反思候选存储终态的聚合契约。"""

from __future__ import annotations

from core.features.reflection.domain import storage_outcomes as feature_outcomes
from core.handlers import reflection_storage_outcomes as legacy_outcomes

ReflectionStoreOutcome = feature_outcomes.ReflectionStoreOutcome
ReflectionStoreResult = feature_outcomes.ReflectionStoreResult
summarize_store_results = feature_outcomes.summarize_store_results


def test_summarize_store_results_counts_mutually_exclusive_outcomes() -> None:
    """四种终态必须互斥计数，失败项不得提交幂等键。"""

    results = [
        ReflectionStoreResult(ReflectionStoreOutcome.CANONICAL, "a"),
        ReflectionStoreResult(ReflectionStoreOutcome.QUARANTINED, "b"),
        ReflectionStoreResult(ReflectionStoreOutcome.FAILED, "failed-key"),
        ReflectionStoreResult(ReflectionStoreOutcome.SKIPPED_IDEMPOTENT, "c"),
    ]

    summary = summarize_store_results(results)

    assert summary.canonical_count == 1
    assert summary.quarantine_count == 1
    assert summary.failed_count == 1
    assert summary.skipped_idempotent_count == 1
    assert summary.completed_idempotency_keys == frozenset({"a", "b", "c"})


def test_summarize_store_results_accepts_empty_window() -> None:
    """空候选窗口应产生全零且无幂等键的稳定汇总。"""

    summary = summarize_store_results([])

    assert summary.canonical_count == 0
    assert summary.quarantine_count == 0
    assert summary.failed_count == 0
    assert summary.skipped_idempotent_count == 0
    assert summary.completed_idempotency_keys == frozenset()


def test_legacy_handler_path_reuses_feature_domain_objects() -> None:
    """旧 handlers 路径只能恒等导出 reflection feature 的领域对象。"""

    assert legacy_outcomes.__all__ == feature_outcomes.__all__
    for name in feature_outcomes.__all__:
        assert getattr(legacy_outcomes, name) is getattr(feature_outcomes, name)
