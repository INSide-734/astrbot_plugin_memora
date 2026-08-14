"""MemoryEngine canonical 提交后的自动画像派生钩子。"""

from __future__ import annotations

import asyncio

from astrbot.api import logger


class MemoryEngineProfileHooksMixin:
    """为 MemoryEngine 提供失败隔离的画像 proposal 调度。"""

    def _schedule_profile_proposal_after_write(self, memory_id: int) -> None:
        """在 canonical 提交后创建受生命周期跟踪的画像任务。"""

        pipeline = getattr(self, "profile_proposal_pipeline", None)
        if pipeline is None:
            return
        self._create_tracked_task(
            self._apply_profile_proposal_after_write(int(memory_id))
        )

    async def _apply_profile_proposal_after_write(self, memory_id: int) -> None:
        """应用单条画像 proposal；普通失败不得回滚 canonical 主写。"""

        pipeline = getattr(self, "profile_proposal_pipeline", None)
        if pipeline is None:
            return
        try:
            await pipeline.apply_for_memory(int(memory_id))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[画像] canonical 提交后的自动画像应用失败，异常类型=%s",
                exc.__class__.__name__,
            )


__all__ = ["MemoryEngineProfileHooksMixin"]
