"""evolution feature 的领域模型所有权与旧路径兼容契约。"""

from core.features.evolution.domain import models as feature_models
from core.models import memory_evolution as legacy_models


def test_legacy_evolution_model_imports_reuse_feature_types() -> None:
    """旧模型路径只能导出 evolution feature 的唯一实现。"""

    assert legacy_models.__all__ == feature_models.__all__
    for name in feature_models.__all__:
        assert getattr(legacy_models, name) is getattr(feature_models, name)
