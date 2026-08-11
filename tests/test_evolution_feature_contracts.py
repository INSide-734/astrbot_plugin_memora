"""evolution feature 的领域、应用与基础设施所有权兼容契约。"""

import subprocess
import sys

import core.features.evolution as evolution_feature
from core.base.config_validator import (
    MemoryEvolutionConfig as LegacyMemoryEvolutionConfig,
)
from core.features.evolution.application import (
    memory_evolution_manager as feature_manager,
)
from core.features.evolution.application import (
    memory_evolution_projection as feature_projection,
)
from core.features.evolution.domain import MemoryEvolutionConfig
from core.features.evolution.domain import models as feature_models
from core.features.evolution.infrastructure import (
    memory_evolution_candidate_sources as feature_candidate_sources,
)
from core.features.evolution.infrastructure import (
    memory_evolution_derived as feature_derived,
)
from core.features.evolution.infrastructure import (
    memory_evolution_derived_helpers as feature_derived_helpers,
)
from core.features.evolution.infrastructure import (
    memory_evolution_review as feature_review,
)
from core.features.evolution.infrastructure import (
    memory_evolution_source_helpers as feature_source_helpers,
)
from core.features.evolution.infrastructure import (
    memory_evolution_store as feature_store,
)
from core.managers import memory_evolution_manager as legacy_manager
from core.managers import memory_evolution_projection as legacy_projection
from core.models import memory_evolution as legacy_models
from core.storage import (
    memory_evolution_candidate_sources as legacy_candidate_sources,
)
from core.storage import memory_evolution_derived as legacy_derived
from core.storage import memory_evolution_derived_helpers as legacy_derived_helpers
from core.storage import memory_evolution_review as legacy_review
from core.storage import memory_evolution_source_helpers as legacy_source_helpers
from core.storage import memory_evolution_store as legacy_store

_INFRASTRUCTURE_MODULE_PAIRS = (
    (legacy_candidate_sources, feature_candidate_sources),
    (legacy_derived, feature_derived),
    (legacy_derived_helpers, feature_derived_helpers),
    (legacy_review, feature_review),
    (legacy_source_helpers, feature_source_helpers),
    (legacy_store, feature_store),
)

_APPLICATION_MODULE_PAIRS = (
    (legacy_manager, feature_manager),
    (legacy_projection, feature_projection),
)


def test_evolution_domain_owner_first_import_stays_lightweight() -> None:
    """全新解释器导入 evolution 领域模块时不得加载运行组件。"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "from core.features.evolution.domain.models import EvolutionSignal; "
            "assert 'core.features.evolution.application' not in sys.modules; "
            "assert 'core.features.evolution.infrastructure' not in sys.modules; "
            "assert 'faiss' not in sys.modules; "
            "print(EvolutionSignal.__module__)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "core.features.evolution.domain.models"


def test_evolution_package_lazily_exports_each_feature_layer() -> None:
    """包级兼容入口应恒等导出领域、应用与基础设施符号。"""

    assert evolution_feature.EvolutionSignal is feature_models.EvolutionSignal
    assert (
        evolution_feature.MemoryEvolutionManager
        is feature_manager.MemoryEvolutionManager
    )
    assert evolution_feature.MemoryEvolutionStore is feature_store.MemoryEvolutionStore


def test_evolution_config_old_path_reuses_feature_owner() -> None:
    """根配置聚合器应恒等导出 evolution feature 的唯一配置模型。"""

    assert LegacyMemoryEvolutionConfig is MemoryEvolutionConfig


def test_legacy_evolution_model_imports_reuse_feature_types() -> None:
    """旧模型路径只能导出 evolution feature 的唯一实现。"""

    assert legacy_models.__all__ == feature_models.__all__
    for name in feature_models.__all__:
        assert getattr(legacy_models, name) is getattr(feature_models, name)


def test_legacy_evolution_infrastructure_reuses_feature_implementations() -> None:
    """旧 Store 路径只能导出 evolution infrastructure 的唯一实现。"""

    for legacy_module, feature_module in _INFRASTRUCTURE_MODULE_PAIRS:
        assert legacy_module.__all__ == feature_module.__all__
        for name in feature_module.__all__:
            assert getattr(legacy_module, name) is getattr(feature_module, name)
    assert legacy_derived._serialized_write is feature_derived._serialized_write


def test_legacy_evolution_application_reuses_feature_implementations() -> None:
    """旧 Manager 路径只能导出 evolution application 的唯一实现。"""

    for legacy_module, feature_module in _APPLICATION_MODULE_PAIRS:
        assert legacy_module.__all__ == feature_module.__all__
        for name in feature_module.__all__:
            assert getattr(legacy_module, name) is getattr(feature_module, name)
