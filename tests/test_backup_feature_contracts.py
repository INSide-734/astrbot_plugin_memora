"""backup feature 的领域模型与旧路径兼容契约。"""

from core.features.backup import domain as feature_domain
from core.managers import backup_models as legacy_models


def test_legacy_backup_models_reuse_feature_domain_types() -> None:
    """旧 backup_models 路径只能导出 feature domain 的唯一实现。"""

    assert legacy_models.__all__ == feature_domain.__all__
    for name in feature_domain.__all__:
        assert getattr(legacy_models, name) is getattr(feature_domain, name)
