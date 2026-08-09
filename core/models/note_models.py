"""笔记模型的旧路径兼容导出。"""

from ..features.notes.domain.models import Note, NoteStatus, NoteVersion

__all__ = ["Note", "NoteVersion", "NoteStatus"]
