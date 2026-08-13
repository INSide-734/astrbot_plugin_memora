"""MemoryEngine canonical 提交后的自动知识派生钩子。"""

from __future__ import annotations

import asyncio

from astrbot.api import logger


class MemoryEngineKnowledgeHooksMixin:
    """为 MemoryEngine 提供失败隔离、可追踪的知识 proposal 调度。"""

    def _schedule_knowledge_proposal_after_write(self, memory_id: int) -> None:
        """在 canonical 提交后创建受生命周期管理的知识任务。"""

        pipeline = getattr(self, "knowledge_proposal_pipeline", None)
        if pipeline is None:
            return
        self._create_tracked_task(
            self._apply_knowledge_proposal_after_write(int(memory_id))
        )

    async def _apply_knowledge_proposal_after_write(self, memory_id: int) -> None:
        """应用单条知识 proposal；普通失败不得回滚 canonical 主写。"""

        pipeline = getattr(self, "knowledge_proposal_pipeline", None)
        if pipeline is None:
            return
        try:
            await pipeline.apply_for_memory(int(memory_id))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[知识] canonical 提交后的自动知识应用失败，异常类型=%s",
                exc.__class__.__name__,
            )


__all__ = ["MemoryEngineKnowledgeHooksMixin"]
