"""graph 派生基础设施的 feature 所有权契约。"""

from core.features.memory.graph import (
    GraphEdge,
    GraphEntry,
    GraphNode,
    GraphReplaceResult,
    GraphStore,
)
from core.features.memory.infrastructure.validators import (
    IndexValidator,
    PersistenceHealthValidator,
)
from core.models.graph_models import GraphNode as LegacyGraphNode
from core.storage.graph_store import GraphStore as LegacyGraphStore
from core.validators.index_validator import IndexValidator as LegacyIndexValidator


def test_legacy_graph_imports_are_feature_implementations() -> None:
    """旧 graph/validator 路径只能导出 feature 的唯一实现。"""

    assert LegacyGraphNode is GraphNode
    assert LegacyGraphStore is GraphStore
    assert LegacyIndexValidator is IndexValidator
    assert GraphEdge is not None
    assert GraphEntry is not None
    assert GraphReplaceResult is not None
    assert PersistenceHealthValidator is not None
