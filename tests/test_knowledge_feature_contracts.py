"""knowledge feature 的领域模型所有权与旧路径兼容契约。"""

from core.features.knowledge.application import (
    KnowledgeManager,
    KnowledgeProposalPipeline,
)
from core.features.knowledge.contracts import (
    KnowledgeExtractorPort,
    KnowledgeSourceReaderPort,
    KnowledgeStorePort,
)
from core.features.knowledge.domain.models import KnowledgeEntry, KnowledgeType
from core.features.knowledge.infrastructure import KnowledgeExtractor
from core.features.knowledge.infrastructure.knowledge_store import (
    KNOWLEDGE_SORT_COLUMNS,
    KnowledgeStore,
)
from core.managers.knowledge_manager import (
    KnowledgeManager as LegacyKnowledgeManager,
)
from core.managers.knowledge_proposal_pipeline import (
    KnowledgeProposalPipeline as LegacyKnowledgeProposalPipeline,
)
from core.models.knowledge_models import (
    KnowledgeEntry as LegacyKnowledgeEntry,
)
from core.models.knowledge_models import KnowledgeType as LegacyKnowledgeType
from core.processors.knowledge_extractor import (
    KnowledgeExtractor as LegacyKnowledgeExtractor,
)
from core.storage.knowledge_store import (
    KNOWLEDGE_SORT_COLUMNS as LegacyKnowledgeSortColumns,
)
from core.storage.knowledge_store import KnowledgeStore as LegacyKnowledgeStore


def test_legacy_knowledge_model_imports_reuse_feature_types() -> None:
    """旧知识模型路径只能导出 knowledge feature 的唯一实现。"""

    assert LegacyKnowledgeEntry is KnowledgeEntry
    assert LegacyKnowledgeType is KnowledgeType


def test_legacy_knowledge_store_import_reuses_feature_implementation() -> None:
    """旧知识 Store 路径只能导出 knowledge infrastructure 的唯一实现。"""

    assert LegacyKnowledgeStore is KnowledgeStore
    assert LegacyKnowledgeSortColumns is KNOWLEDGE_SORT_COLUMNS


def test_legacy_knowledge_manager_import_reuses_feature_implementation() -> None:
    """旧知识 Manager 路径只能导出 knowledge application 的唯一实现。"""

    assert LegacyKnowledgeManager is KnowledgeManager


def test_legacy_knowledge_pipeline_import_reuses_feature_implementation() -> None:
    """旧知识 pipeline 路径只能导出 knowledge application 的唯一实现。"""

    assert LegacyKnowledgeProposalPipeline is KnowledgeProposalPipeline


def test_legacy_knowledge_extractor_import_reuses_feature_implementation() -> None:
    """旧 processor 路径只能导出 knowledge infrastructure 的唯一实现。"""

    assert LegacyKnowledgeExtractor is KnowledgeExtractor


def test_knowledge_ports_accept_existing_implementations_structurally() -> None:
    """迁移端口应能接收现有 Store、source reader 和 extractor 实现。"""

    class SourceReader:
        async def load_sources(self, memory_ids, *, max_content_chars=4_000):
            return []

    assert isinstance(KnowledgeStore(":memory:"), KnowledgeStorePort)
    assert isinstance(SourceReader(), KnowledgeSourceReaderPort)
    assert isinstance(KnowledgeExtractor(), KnowledgeExtractorPort)
