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
