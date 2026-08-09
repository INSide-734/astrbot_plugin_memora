"""notes feature 的领域模型所有权与旧路径兼容契约。"""

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
from core.managers.note_manager import NoteManager as LegacyNoteManager
from core.managers.note_proposal_pipeline import (
    NoteProposalPipeline as LegacyNoteProposalPipeline,
)
from core.models.note_models import (
    Note as LegacyNote,
)
from core.models.note_models import (
    NoteStatus as LegacyNoteStatus,
)
from core.models.note_models import (
    NoteVersion as LegacyNoteVersion,
)
from core.processors.note_generator import NoteGenerator as LegacyNoteGenerator
from core.storage.note_store import NoteStore as LegacyNoteStore


def test_legacy_note_model_imports_reuse_feature_types() -> None:
    """旧笔记模型路径只能导出 notes feature 的唯一实现。"""

    assert LegacyNote is Note
    assert LegacyNoteStatus is NoteStatus
    assert LegacyNoteVersion is NoteVersion


def test_legacy_note_store_import_reuses_feature_implementation() -> None:
    """旧笔记 Store 路径只能导出 notes infrastructure 的唯一实现。"""

    assert LegacyNoteStore is NoteStore


def test_legacy_note_manager_import_reuses_feature_implementation() -> None:
    """旧笔记 Manager 路径只能导出 notes application 的唯一实现。"""

    assert LegacyNoteManager is NoteManager


def test_legacy_note_pipeline_import_reuses_feature_implementation() -> None:
    """旧笔记 pipeline 路径只能导出 notes application 的唯一实现。"""

    assert LegacyNoteProposalPipeline is NoteProposalPipeline


def test_legacy_note_generator_import_reuses_feature_implementation() -> None:
    """旧 processor 路径只能导出 notes infrastructure 的唯一实现。"""

    assert LegacyNoteGenerator is NoteGenerator


def test_note_ports_accept_existing_implementations_structurally() -> None:
    """迁移端口应能接收现有 Store、source reader 和 generator 实现。"""

    class SourceReader:
        """提供测试所需的 canonical source 读取形状。"""

        async def load_sources(self, memory_ids, *, max_content_chars=4_000):
            """返回指定 canonical source 的空测试集合。"""

            return []

        async def load_all_sources(self, *, max_content_chars=4_000):
            """返回全部 canonical source 的空测试集合。"""

            return []

    assert isinstance(NoteStore(":memory:"), NoteStorePort)
    assert isinstance(SourceReader(), NoteSourceReaderPort)
    assert isinstance(NoteGenerator(), NoteGeneratorPort)
