"""笔记 feature 的公开边界。"""

from .application import NoteManager, NoteProposalPipeline
from .contracts import NoteGeneratorPort, NoteSourceReaderPort, NoteStorePort
from .domain import Note, NoteStatus, NoteVersion
from .infrastructure import NoteGenerator, NoteStore

__all__ = [
    "Note",
    "NoteGenerator",
    "NoteGeneratorPort",
    "NoteManager",
    "NoteProposalPipeline",
    "NoteSourceReaderPort",
    "NoteStatus",
    "NoteStore",
    "NoteStorePort",
    "NoteVersion",
]
