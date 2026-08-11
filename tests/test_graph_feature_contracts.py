"""graph 派生基础设施的 feature 所有权契约。"""

from core.features.memory.graph import (
    GraphEdge,
    GraphEntry,
    GraphReplaceResult,
    GraphStore,
)
from core.features.memory.infrastructure.validators import (
    IndexValidator,
    PersistenceHealthValidator,
)
from core.storage.graph_store import GraphStore as LegacyGraphStore
from core.validators.index_validator import IndexValidator as LegacyIndexValidator


def test_graph_feature_exports_and_legacy_infrastructure_imports() -> None:
    """graph 模型应由 feature 导出，旧基础设施路径复用唯一实现。"""

    assert LegacyGraphStore is GraphStore
    assert LegacyIndexValidator is IndexValidator
    assert GraphEdge is not None
    assert GraphEntry is not None
    assert GraphReplaceResult is not None
    assert PersistenceHealthValidator is not None
