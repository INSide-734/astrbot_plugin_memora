"""backfill feature 与旧路径的唯一实现契约。"""

from core.features.backfill.application import scheduler as feature_scheduler
from core.schedulers import backfill_scheduler as legacy_scheduler


def test_legacy_backfill_scheduler_reuses_feature_implementation() -> None:
    """旧 scheduler 路径只能导出 backfill application 的唯一实现。"""

    assert legacy_scheduler.__all__ == feature_scheduler.__all__
    for name in feature_scheduler.__all__:
        assert getattr(legacy_scheduler, name) is getattr(feature_scheduler, name)
