"""diagnostics feature 与旧路径的唯一实现契约。"""

from core.diagnostics import event_store as legacy_event_store
from core.diagnostics import health_scorer as legacy_health_scorer
from core.features.diagnostics.application import health_scorer as feature_health_scorer
from core.features.diagnostics.infrastructure import event_store as feature_event_store


def test_legacy_diagnostic_event_store_reuses_feature_implementation() -> None:
    """旧 diagnostics 路径只能导出 feature infrastructure 的唯一实现。"""

    assert legacy_event_store.__all__ == feature_event_store.__all__
    assert (
        legacy_event_store.DiagnosticEventStore
        is feature_event_store.DiagnosticEventStore
    )


def test_legacy_health_scorer_reuses_feature_implementation() -> None:
    """旧 diagnostics 路径只能导出 feature application 的唯一实现。"""

    assert legacy_health_scorer.__all__ == feature_health_scorer.__all__
    assert legacy_health_scorer.HealthScorer is feature_health_scorer.HealthScorer
