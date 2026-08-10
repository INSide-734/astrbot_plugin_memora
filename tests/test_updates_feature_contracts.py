"""updates feature 与旧路径的唯一实现契约。"""

from core.features.updates import domain as feature_domain
from core.features.updates.application import manager as feature_manager
from core.managers import update_installer as legacy_installer
from core.managers import update_manager as legacy_manager


def test_legacy_update_types_reuse_feature_domain_types() -> None:
    """旧 update manager 路径只能导出 feature domain 的唯一类型。"""

    assert legacy_manager.UpdateError is feature_domain.UpdateError
    assert legacy_installer.RuntimeUpdateError is feature_domain.RuntimeUpdateError
    assert legacy_manager.UpdateRelease is feature_domain.UpdateRelease
    assert legacy_manager.DownloadedUpdate is feature_domain.DownloadedUpdate


def test_legacy_update_manager_reuses_feature_implementation() -> None:
    """旧 UpdateManager 路径只能导出 application service 的唯一实现。"""

    assert legacy_manager.UpdateManager is feature_manager.UpdateManager
