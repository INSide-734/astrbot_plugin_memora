"""knowledge feature 的分层所有权与端口契约。"""

from collections.abc import Sequence

import core.features.knowledge as knowledge_feature
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
from core.shared.contracts import MemorySourceRef


def test_knowledge_feature_exports_domain_owner() -> None:
    """feature 包级入口应恒等导出知识领域模型的唯一实现。"""

    assert knowledge_feature.KnowledgeEntry is KnowledgeEntry
    assert knowledge_feature.KnowledgeType is KnowledgeType


def test_knowledge_feature_exports_store_owner() -> None:
    """feature 包级入口应恒等导出 Store 与排序字段契约。"""

    assert knowledge_feature.KnowledgeStore is KnowledgeStore
    assert knowledge_feature.KNOWLEDGE_SORT_COLUMNS is KNOWLEDGE_SORT_COLUMNS


def test_knowledge_feature_exports_manager_owner() -> None:
    """feature 包级入口应恒等导出知识 Manager。"""

    assert knowledge_feature.KnowledgeManager is KnowledgeManager


def test_knowledge_feature_exports_pipeline_owner() -> None:
    """feature 包级入口应恒等导出知识 proposal 管线。"""

    assert knowledge_feature.KnowledgeProposalPipeline is KnowledgeProposalPipeline


def test_knowledge_feature_exports_extractor_owner() -> None:
    """feature 包级入口应恒等导出知识抽取器。"""

    assert knowledge_feature.KnowledgeExtractor is KnowledgeExtractor


def test_knowledge_ports_accept_existing_implementations_structurally() -> None:
    """迁移端口应能接收现有 Store、source reader 和 extractor 实现。"""

    class SourceReader:
        """提供只返回空集合的来源读取测试替身。"""

        async def load_sources(
            self,
            memory_ids: Sequence[int],
            *,
            max_content_chars: int = 4_000,
        ) -> list[MemorySourceRef]:
            """返回空来源集合以验证端口结构兼容性。

            Args:
                memory_ids: 待读取的 canonical memory ID。
                max_content_chars: 单次读取允许的最大正文字符数。

            Returns:
                空来源集合。
            """

            return []

    store_port: KnowledgeStorePort = KnowledgeStore(":memory:")
    source_reader_port: KnowledgeSourceReaderPort = SourceReader()
    extractor_port: KnowledgeExtractorPort = KnowledgeExtractor()

    assert isinstance(store_port, KnowledgeStorePort)
    assert isinstance(source_reader_port, KnowledgeSourceReaderPort)
    assert isinstance(extractor_port, KnowledgeExtractorPort)
