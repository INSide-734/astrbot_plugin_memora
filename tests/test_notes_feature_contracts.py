"""notes feature 的领域模型所有权与旧路径兼容契约。"""

from core.features.notes.domain.models import (
    Note,
    NoteStatus,
    NoteVersion,
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


def test_legacy_note_model_imports_reuse_feature_types() -> None:
    """旧笔记模型路径只能导出 notes feature 的唯一实现。"""

    assert LegacyNote is Note
    assert LegacyNoteStatus is NoteStatus
    assert LegacyNoteVersion is NoteVersion
