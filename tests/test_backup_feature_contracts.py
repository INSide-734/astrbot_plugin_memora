"""backup feature 的领域模型与旧路径兼容契约。"""

from core.features.backup import domain as feature_domain
from core.features.backup.application import manager as feature_manager
from core.features.backup.infrastructure import integrity as feature_integrity
from core.features.backup.infrastructure import snapshot as feature_snapshot
from core.managers import backup_manager as legacy_manager
from core.managers import backup_models as legacy_models
from core.managers import backup_reference_integrity as legacy_integrity
from core.managers import backup_snapshot as legacy_snapshot


def test_legacy_backup_models_reuse_feature_domain_types() -> None:
    """旧 backup_models 路径只能导出 feature domain 的唯一实现。"""

    assert legacy_models.__all__ == feature_domain.__all__
    for name in feature_domain.__all__:
        assert getattr(legacy_models, name) is getattr(feature_domain, name)


def test_legacy_backup_snapshot_reuses_feature_implementation() -> None:
    """旧 backup_snapshot 路径只能导出 feature infrastructure 的唯一实现。"""

    assert legacy_snapshot.__all__ == feature_snapshot.__all__
    for name in feature_snapshot.__all__:
        assert getattr(legacy_snapshot, name) is getattr(feature_snapshot, name)


def test_legacy_backup_integrity_reuses_feature_implementation() -> None:
    """旧 integrity 路径只能导出 feature infrastructure 的唯一实现。"""

    assert legacy_integrity.__all__ == feature_integrity.__all__
    for name in feature_integrity.__all__:
        assert getattr(legacy_integrity, name) is getattr(feature_integrity, name)


def test_legacy_backup_manager_reuses_feature_implementation() -> None:
    """旧 Manager 路径只能导出 backup application 的唯一实现。"""

    assert legacy_manager.__all__ == feature_manager.__all__
    for name in feature_manager.__all__:
        assert getattr(legacy_manager, name) is getattr(feature_manager, name)
