"""Memory feature 所有权与旧路径兼容契约。"""

import subprocess
import sys

from core.features.memory import (
    AtomStore,
    MemoryAtom,
    SchemaManager,
    WriteOpJournal,
)
from core.features.memory.infrastructure.base_store import (
    BaseStore as FeatureInstanceBaseStore,
)
from core.managers.schema_manager import SchemaManager as LegacySchemaManager
from core.managers.write_op_journal import WriteOpJournal as LegacyWriteOpJournal
from core.models.memory_atom import MemoryAtom as LegacyMemoryAtom
from core.storage.atom_store import AtomStore as LegacyAtomStore
from core.storage.base_store import BaseStore as LegacyInstanceBaseStore


def test_memory_domain_owner_first_import_stays_lightweight() -> None:
    """全新解释器导入 memory 领域模块时不得提前加载 FAISS。"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "from core.features.memory.domain.revision import memory_revision; "
            "assert 'faiss' not in sys.modules; "
            "print(memory_revision.__module__)",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "core.features.memory.domain.revision"


def test_legacy_canonical_imports_are_feature_implementations() -> None:
    """旧 canonical 路径只能导出 memory feature 的唯一实现。"""

    assert LegacyMemoryAtom is MemoryAtom
    assert LegacyAtomStore is AtomStore
    assert LegacyInstanceBaseStore is FeatureInstanceBaseStore
    assert LegacySchemaManager is SchemaManager
    assert LegacyWriteOpJournal is WriteOpJournal


def test_graph_config_old_path_reuses_feature_owner() -> None:
    """根配置聚合器应恒等导出 graph feature 的唯一配置模型。"""

    from core.base.config_validator import GraphMemoryConfig as LegacyGraphMemoryConfig
    from core.features.memory.domain.graph_memory_config import GraphMemoryConfig

    assert LegacyGraphMemoryConfig is GraphMemoryConfig


def test_migration_config_old_path_reuses_memory_feature_owner() -> None:
    """根配置聚合器应恒等导出 memory feature 的迁移配置模型。"""

    from core.base.config_validator import MigrationSettings as LegacyMigrationSettings
    from core.features.memory.domain.migration_config import MigrationSettings

    assert LegacyMigrationSettings is MigrationSettings


def test_write_reliability_config_old_path_reuses_memory_feature_owner() -> None:
    """旧运行时配置路径应恒等导出 memory feature 的写入可靠性模型。"""

    from core.base.runtime_feature_config import (
        WriteReliabilityConfig as LegacyWriteReliabilityConfig,
    )
    from core.features.memory.domain.write_reliability_config import (
        WriteReliabilityConfig,
    )

    assert LegacyWriteReliabilityConfig is WriteReliabilityConfig


def test_memory_runtime_configs_old_path_reuses_memory_feature_owner() -> None:
    """其余 memory 运行时配置旧路径应恒等导出唯一领域模型。"""

    from core.base.runtime_feature_config import (
        AtomClassifierConfig as LegacyAtomClassifierConfig,
    )
    from core.base.runtime_feature_config import ExportConfig as LegacyExportConfig
    from core.base.runtime_feature_config import (
        PersonaDecayConfig as LegacyPersonaDecayConfig,
    )
    from core.features.memory.domain.atom_classifier_config import (
        AtomClassifierConfig,
    )
    from core.features.memory.domain.export_config import ExportConfig
    from core.features.memory.domain.persona_decay_config import PersonaDecayConfig

    assert LegacyAtomClassifierConfig is AtomClassifierConfig
    assert LegacyExportConfig is ExportConfig
    assert LegacyPersonaDecayConfig is PersonaDecayConfig
