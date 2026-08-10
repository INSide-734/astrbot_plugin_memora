"""observability feature 与旧路径的唯一实现契约。"""

from core.features.observability.application import (
    memory_write_timing as feature_memory_write_timing,
)
from core.features.observability.application import (
    perf_tracker as feature_perf_tracker,
)
from core.features.observability.application import (
    quality_scorer as feature_quality_scorer,
)
from core.features.observability.domain import recall_timing as feature_recall_timing
from core.features.observability.infrastructure import (
    debug_reporter as feature_debug_reporter,
)
from core.features.observability.infrastructure import (
    instrumentation as feature_instrumentation,
)
from core.features.observability.infrastructure import metrics as feature_metrics
from core.monitoring import debug_reporter as legacy_debug_reporter
from core.monitoring import instrumentation as legacy_instrumentation
from core.monitoring import memory_write_timing as legacy_memory_write_timing
from core.monitoring import metrics as legacy_metrics
from core.monitoring import perf_tracker as legacy_perf_tracker
from core.monitoring import quality_scorer as legacy_quality_scorer
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


def test_legacy_quality_scorer_reuses_feature_application_implementation() -> None:
    """旧 monitoring 路径只能导出 feature application 的质量评分实现。"""

    assert legacy_quality_scorer.__all__ == feature_quality_scorer.__all__
    for name in feature_quality_scorer.__all__:
        assert getattr(legacy_quality_scorer, name) is getattr(
            feature_quality_scorer,
            name,
        )


def test_legacy_metrics_reuses_feature_infrastructure_objects() -> None:
    """旧 monitoring 路径只能导出 feature infrastructure 的指标对象。"""

    assert legacy_metrics.__all__ == feature_metrics.__all__
    for name in feature_metrics.__all__:
        assert getattr(legacy_metrics, name) is getattr(feature_metrics, name)


def test_legacy_debug_reporter_reuses_feature_infrastructure_objects() -> None:
    """旧 monitoring 路径只能导出 feature infrastructure 的调试记录器。"""

    assert legacy_debug_reporter.__all__ == feature_debug_reporter.__all__
    for name in feature_debug_reporter.__all__:
        assert getattr(legacy_debug_reporter, name) is getattr(
            feature_debug_reporter,
            name,
        )


def test_legacy_memory_write_timing_reuses_feature_application_objects() -> None:
    """旧 monitoring 路径只能导出 feature application 的写入计时对象。"""

    assert legacy_memory_write_timing.__all__ == feature_memory_write_timing.__all__
    for name in feature_memory_write_timing.__all__:
        assert getattr(legacy_memory_write_timing, name) is getattr(
            feature_memory_write_timing,
            name,
        )


def test_legacy_instrumentation_reuses_feature_infrastructure_objects() -> None:
    """旧 monitoring 路径只能导出 feature infrastructure 的插桩对象。"""

    assert legacy_instrumentation.__all__ == feature_instrumentation.__all__
    for name in feature_instrumentation.__all__:
        assert getattr(legacy_instrumentation, name) is getattr(
            feature_instrumentation,
            name,
        )
