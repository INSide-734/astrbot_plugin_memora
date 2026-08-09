"""knowledge feature 的领域模型所有权与旧路径兼容契约。"""

from core.features.knowledge.domain.models import KnowledgeEntry, KnowledgeType
from core.models.knowledge_models import (
    KnowledgeEntry as LegacyKnowledgeEntry,
)
from core.models.knowledge_models import KnowledgeType as LegacyKnowledgeType


def test_legacy_knowledge_model_imports_reuse_feature_types() -> None:
    """旧知识模型路径只能导出 knowledge feature 的唯一实现。"""

    assert LegacyKnowledgeEntry is KnowledgeEntry
    assert LegacyKnowledgeType is KnowledgeType
