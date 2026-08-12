"""evolution feature 的分层所有权与必要包级入口契约。"""

import subprocess
import sys

import core.features.evolution as evolution_feature
import core.features.evolution.application as evolution_application
from core.base.config_validator import (
    MemoryEvolutionConfig as LegacyMemoryEvolutionConfig,
)
from core.features.evolution.application import (
    contradiction_detector as feature_contradiction_detector,
)
from core.features.evolution.application import (
    derived_relation_expander as feature_relation_reader,
)
from core.features.evolution.application import (
    episode_clusterer as feature_episode_clusterer,
)
from core.features.evolution.application import (
    memory_consolidator as feature_consolidator,
)
from core.features.evolution.application import (
    memory_evolution_candidates as feature_candidates,
)
from core.features.evolution.application import (
    memory_evolution_gate as feature_gate,
)
from core.features.evolution.application import (
    memory_evolution_manager as feature_manager,
)
from core.features.evolution.application import (
    memory_evolution_projection as feature_projection,
)
from core.features.evolution.application import (
    projection_reader as feature_projection_reader,
)
from core.features.evolution.application import (
    semantic_compressor as feature_compressor,
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
    memory_evolution_review as feature_review,
)
from core.features.evolution.infrastructure import (
    memory_evolution_store as feature_store,
)
from core.shared.contracts import MemorySourceRef


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


def test_retrieval_first_import_resolves_evolution_readers_without_legacy_paths() -> (
    None
):
    """先导入 retrieval 时应解析 feature reader，且旧模块不可再导入。"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib.util; "
            "import core.retrieval as retrieval; "
            "from core.features.evolution.application import "
            "DerivedRelationExpander, ProjectionReader; "
            "assert not hasattr(retrieval, 'DerivedRelationExpander'); "
            "assert importlib.util.find_spec("
            "'core.retrieval.derived_relation_expander') is None; "
            "assert importlib.util.find_spec("
            "'core.retrieval.projection_reader') is None; "
            "print(DerivedRelationExpander.__module__, ProjectionReader.__module__)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == (
        "core.features.evolution.application.derived_relation_expander "
        "core.features.evolution.application.projection_reader"
    )


def test_evolution_readers_first_import_resolves_retrieval_without_cycle() -> None:
    """先导入 feature reader 时应能随后加载完整 retrieval 包。"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from core.features.evolution.application import "
            "DerivedRelationExpander, ProjectionReader; "
            "import core.retrieval as retrieval; "
            "from core.retrieval.rrf_fusion import HybridResult; "
            "assert retrieval.HybridResult is HybridResult; "
            "print(DerivedRelationExpander.__name__, ProjectionReader.__name__)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == (
        "DerivedRelationExpander ProjectionReader"
    )


def test_evolution_package_lazily_exports_each_feature_layer() -> None:
    """包级兼容入口应恒等导出领域、应用与基础设施符号。"""

    assert evolution_feature.EvolutionSignal is feature_models.EvolutionSignal
    assert evolution_feature.MemorySourceRef is MemorySourceRef
    assert (
        evolution_feature.MemoryEvolutionManager
        is feature_manager.MemoryEvolutionManager
    )
    assert evolution_feature.MemoryEvolutionGate is feature_gate.MemoryEvolutionGate
    assert (
        evolution_feature.MemoryEvolutionCandidateGenerator
        is feature_candidates.MemoryEvolutionCandidateGenerator
    )
    assert (
        evolution_feature.MemoryConsolidator is feature_consolidator.MemoryConsolidator
    )
    assert evolution_feature.MemoryEvolutionStore is feature_store.MemoryEvolutionStore


def test_evolution_foundation_services_use_feature_application_owner() -> None:
    """门控、候选、proposal 与 worker 必须由 application 唯一持有。"""

    assert evolution_application.MemoryEvolutionGate is feature_gate.MemoryEvolutionGate
    assert evolution_application.MemoryEvolutionCandidateGenerator is (
        feature_candidates.MemoryEvolutionCandidateGenerator
    )
    assert (
        evolution_application.MemoryConsolidator
        is feature_consolidator.MemoryConsolidator
    )
    assert feature_gate.MemoryEvolutionGate.__module__ == (
        "core.features.evolution.application.memory_evolution_gate"
    )
    assert feature_candidates.MemoryEvolutionCandidateGenerator.__module__ == (
        "core.features.evolution.application.memory_evolution_candidates"
    )
    assert feature_consolidator.MemoryConsolidator.__module__ == (
        "core.features.evolution.application.memory_consolidator"
    )
    assert feature_episode_clusterer.EpisodeClusterer.__module__ == (
        "core.features.evolution.application.episode_clusterer"
    )
    assert feature_contradiction_detector.ContradictionDetector.__module__ == (
        "core.features.evolution.application.contradiction_detector"
    )
    assert feature_manager.MemoryEvolutionManager.__module__ == (
        "core.features.evolution.application.memory_evolution_manager"
    )
    assert feature_projection.MemoryEvolutionProjectionProposalMixin.__module__ == (
        "core.features.evolution.application.memory_evolution_projection"
    )
    assert feature_compressor.SemanticCompressor.__module__ == (
        "core.features.evolution.application.semantic_compressor"
    )
    assert evolution_application.DerivedRelationExpander is (
        feature_relation_reader.DerivedRelationExpander
    )
    assert evolution_application.ProjectionReader is (
        feature_projection_reader.ProjectionReader
    )
    assert evolution_application.ProjectionBudget is (
        feature_projection_reader.ProjectionBudget
    )
    assert evolution_application.ProjectionReadStats is (
        feature_projection_reader.ProjectionReadStats
    )
    assert evolution_application.ProjectionScope is (
        feature_projection_reader.ProjectionScope
    )


def test_evolution_config_old_path_reuses_feature_owner() -> None:
    """根配置聚合器应恒等导出 evolution feature 的唯一配置模型。"""

    assert LegacyMemoryEvolutionConfig is MemoryEvolutionConfig


def test_evolution_domain_reuses_shared_canonical_source() -> None:
    """Evolution domain 必须复用 shared 的唯一 canonical 来源类型。"""

    assert feature_models.MemorySourceRef is MemorySourceRef
    assert feature_models.EvolutionSignal.__module__ == (
        "core.features.evolution.domain.models"
    )


def test_evolution_store_assembles_feature_owned_mixins() -> None:
    """Evolution Store 必须只组合 feature infrastructure 的职责 mixin。"""

    assert (
        feature_candidate_sources.MemoryEvolutionCandidateSourceMixin
        in feature_store.MemoryEvolutionStore.__mro__
    )
    assert (
        feature_review.MemoryEvolutionReviewMixin
        in feature_store.MemoryEvolutionStore.__mro__
    )
    assert (
        feature_derived.MemoryEvolutionDerivedMixin
        in feature_store.MemoryEvolutionStore.__mro__
    )
