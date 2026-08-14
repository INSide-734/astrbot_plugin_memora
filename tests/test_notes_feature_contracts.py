"""notes feature 的分层所有权与端口契约。"""

from collections.abc import Sequence

import core.features.notes as notes_feature
from core.features.notes.application import NoteManager, NoteProposalPipeline
from core.features.notes.contracts import (
    NoteGeneratorPort,
    NoteSourceReaderPort,
    NoteStorePort,
)
from core.features.notes.domain.models import (
    Note,
    NoteStatus,
    NoteVersion,
)
from core.features.notes.infrastructure import NoteGenerator, NoteStore
from core.shared.contracts import MemorySourceRef


def test_notes_feature_exports_domain_owner() -> None:
    """feature 包级入口应恒等导出笔记领域模型的唯一实现。"""

    assert notes_feature.Note is Note
    assert notes_feature.NoteStatus is NoteStatus
    assert notes_feature.NoteVersion is NoteVersion


def test_notes_feature_exports_store_owner() -> None:
    """feature 包级入口应恒等导出笔记 Store。"""

    assert notes_feature.NoteStore is NoteStore


def test_notes_feature_exports_manager_owner() -> None:
    """feature 包级入口应恒等导出笔记 Manager。"""

    assert notes_feature.NoteManager is NoteManager


def test_notes_feature_exports_pipeline_owner() -> None:
    """feature 包级入口应恒等导出笔记 proposal 管线。"""

    assert notes_feature.NoteProposalPipeline is NoteProposalPipeline


def test_notes_feature_exports_generator_owner() -> None:
    """feature 包级入口应恒等导出笔记生成器。"""

    assert notes_feature.NoteGenerator is NoteGenerator


def test_note_ports_accept_existing_implementations_structurally() -> None:
    """迁移端口应能接收现有 Store、source reader 和 generator 实现。"""

    class SourceReader:
        """提供测试所需的 canonical source 读取形状。"""

        async def load_sources(
            self,
            memory_ids: Sequence[int],
            *,
            max_content_chars: int = 4_000,
        ) -> list[MemorySourceRef]:
            """返回指定 canonical source 的空测试集合。

            Args:
                memory_ids: 待读取的 canonical memory ID。
                max_content_chars: 单次读取允许的最大正文字符数。

            Returns:
                空来源集合。
            """

            return []

        async def load_all_sources(
            self,
            *,
            max_content_chars: int = 4_000,
        ) -> list[MemorySourceRef]:
            """返回全部 canonical source 的空测试集合。

            Args:
                max_content_chars: 单次读取允许的最大正文字符数。

            Returns:
                空来源集合。
            """

            return []

    store_port: NoteStorePort = NoteStore(":memory:")
    source_reader_port: NoteSourceReaderPort = SourceReader()
    generator_port: NoteGeneratorPort = NoteGenerator()

    assert isinstance(store_port, NoteStorePort)
    assert isinstance(source_reader_port, NoteSourceReaderPort)
    assert isinstance(generator_port, NoteGeneratorPort)
