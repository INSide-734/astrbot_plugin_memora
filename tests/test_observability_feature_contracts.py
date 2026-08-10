"""observability feature 与旧路径的唯一实现契约。"""

from core.features.observability.application import (
    perf_tracker as feature_perf_tracker,
)
from core.features.observability.domain import recall_timing as feature_recall_timing
from core.features.observability.infrastructure import metrics as feature_metrics
from core.monitoring import metrics as legacy_metrics
from core.monitoring import perf_tracker as legacy_perf_tracker
from core.monitoring import recall_timing as legacy_recall_timing


def test_legacy_recall_timing_reuses_feature_domain_implementation() -> None:
    """旧 monitoring 路径只能导出 feature domain 的唯一实现。"""

    assert legacy_recall_timing.__all__ == feature_recall_timing.__all__
    assert (
        legacy_recall_timing.sanitize_recall_sample
        is feature_recall_timing.sanitize_recall_sample
    )
    assert legacy_recall_timing.TIMING_KEYS is feature_recall_timing.TIMING_KEYS
    assert legacy_recall_timing.COUNT_KEYS is feature_recall_timing.COUNT_KEYS
    assert legacy_recall_timing.BOOL_KEYS is feature_recall_timing.BOOL_KEYS
    assert legacy_recall_timing.STATUS_VALUES is feature_recall_timing.STATUS_VALUES


def test_legacy_perf_tracker_reuses_feature_application_implementation() -> None:
    """旧 monitoring 路径只能导出 feature application 的唯一实现。"""

    assert legacy_perf_tracker.__all__ == feature_perf_tracker.__all__
    assert legacy_perf_tracker.PerfTracker is feature_perf_tracker.PerfTracker


def test_legacy_metrics_reuses_feature_infrastructure_objects() -> None:
    """旧 monitoring 路径只能导出 feature infrastructure 的指标对象。"""

    assert legacy_metrics.__all__ == feature_metrics.__all__
    for name in feature_metrics.__all__:
        assert getattr(legacy_metrics, name) is getattr(feature_metrics, name)
