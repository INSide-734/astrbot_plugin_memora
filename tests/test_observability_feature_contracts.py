"""observability feature 与旧路径的唯一实现契约。"""

from core.features.observability.domain import recall_timing as feature_recall_timing
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
