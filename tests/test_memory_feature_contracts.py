"""Memory feature 所有权与旧路径兼容契约。"""

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


def test_legacy_canonical_imports_are_feature_implementations() -> None:
    """旧 canonical 路径只能导出 memory feature 的唯一实现。"""

    assert LegacyMemoryAtom is MemoryAtom
    assert LegacyAtomStore is AtomStore
    assert LegacyInstanceBaseStore is FeatureInstanceBaseStore
    assert LegacySchemaManager is SchemaManager
    assert LegacyWriteOpJournal is WriteOpJournal
