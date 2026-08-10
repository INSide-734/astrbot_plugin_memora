"""decay feature 与旧路径的唯一实现契约。"""

from core.features.decay.application import operations as feature_operations
from core.managers import decay_operations as legacy_operations
from core.schedulers import decay_scheduler as legacy_scheduler


def test_legacy_decay_operations_reuse_feature_implementation() -> None:
    """旧 manager 路径只能导出 decay application 的唯一实现。"""

    assert legacy_operations.__all__ == feature_operations.__all__
    assert (
        legacy_operations.DecayOperationsMixin
        is feature_operations.DecayOperationsMixin
    )
    assert (
        legacy_operations._normalize_batch_metadata
        is feature_operations._normalize_batch_metadata
    )


def test_legacy_decay_scheduler_reuses_feature_implementation() -> None:
    """旧 scheduler 路径只能导出 decay application 的唯一实现。"""

    from core.features.decay.application import scheduler as feature_scheduler

    assert legacy_scheduler.__all__ == feature_scheduler.__all__
    assert legacy_scheduler.DecayScheduler is feature_scheduler.DecayScheduler
