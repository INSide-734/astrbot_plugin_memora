"""graph 派生基础设施的 feature 所有权契约。"""

from core.features.memory.graph import (
    GraphEdge,
    GraphEntry,
    GraphReplaceResult,
)
from core.features.memory.infrastructure.validators import (
    PersistenceHealthValidator,
)


def test_graph_feature_exports_owned_contracts() -> None:
    """graph 包应导出模型、Store 与验证器 owner。"""

    assert GraphEdge is not None
    assert GraphEntry is not None
    assert GraphReplaceResult is not None
    assert PersistenceHealthValidator is not None
