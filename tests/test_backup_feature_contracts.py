"""backup feature 的唯一所有权与旧路径清理契约。"""

import core.features.backup as backup_feature
from core.features.backup import domain as feature_domain
from core.features.backup.application import manager as feature_manager
from core.features.backup.infrastructure import integrity as feature_integrity
from core.features.backup.infrastructure import snapshot as feature_snapshot


def test_backup_package_exports_feature_owned_contracts() -> None:
    """backup 根包应完整恒等导出四个实际所有者的公开对象。"""

    owners = (
        feature_domain,
        feature_manager,
        feature_snapshot,
        feature_integrity,
    )
    expected_exports = {name for owner in owners for name in owner.__all__}

    assert set(backup_feature.__all__) == expected_exports
    for owner in owners:
        for name in owner.__all__:
            assert getattr(backup_feature, name) is getattr(owner, name)
