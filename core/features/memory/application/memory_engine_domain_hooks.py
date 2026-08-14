"""MemoryEngine canonical 提交后的领域 proposal 总调度。"""

from __future__ import annotations

from .memory_engine_knowledge_hooks import MemoryEngineKnowledgeHooksMixin
from .memory_engine_note_hooks import MemoryEngineNoteHooksMixin
from .memory_engine_profile_hooks import MemoryEngineProfileHooksMixin


class MemoryEngineDomainHooksMixin(
    MemoryEngineProfileHooksMixin,
    MemoryEngineKnowledgeHooksMixin,
    MemoryEngineNoteHooksMixin,
):
    """集中调度画像、知识和笔记的可选写后 proposal。"""

    def _schedule_domain_proposals_after_write(self, memory_id: int) -> None:
        """按固定顺序调度全部已装配的领域 proposal 管线。"""

        self._schedule_profile_proposal_after_write(memory_id)
        self._schedule_knowledge_proposal_after_write(memory_id)
        self._schedule_note_proposal_after_write(memory_id)


__all__ = ["MemoryEngineDomainHooksMixin"]
