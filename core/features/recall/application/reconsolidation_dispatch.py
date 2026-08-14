"""再巩固候选的后台任务派发边界。"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger


async def schedule_reconsolidation_proposal(
    memory_engine: object,
    memories: list[Any],
    query: str,
) -> None:
    """为最高分记忆派发再巩固候选，不阻塞 LLM 请求关键路径。

    ``MemoryEngine`` 提供任务跟踪器时，候选协程由引擎接管并在关停时收敛；
    旧版测试替身没有该接口时保留直接等待的兼容路径。无论哪条路径，
    候选都只生成 pending proposal，不直接写入 canonical 记忆。
    """

    manager = getattr(memory_engine, "reconsolidation", None)
    if manager is None or not memories:
        return
    candidate = memories[0]
    if isinstance(candidate, dict):
        memory_id = candidate.get("id")
    else:
        memory_id = getattr(candidate, "doc_id", None)
    if not memory_id:
        return

    try:
        proposal = manager.maybe_propose(int(memory_id), context=query)
        tracker = getattr(memory_engine, "_create_tracked_task", None)
        if callable(tracker):
            try:
                tracked = tracker(proposal)
            except BaseException:
                _close_if_needed(proposal)
                raise
            if tracked is None:
                _close_if_needed(proposal)
            return
        # 兼容没有引擎任务所有权接口的旧测试替身。
        await proposal
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("[召回流程] 再巩固候选生成失败")


def _close_if_needed(proposal: Any) -> None:
    """在任务拒收或派发失败时关闭未使用的候选协程。"""

    close = getattr(proposal, "close", None)
    if callable(close):
        close()


__all__ = ["schedule_reconsolidation_proposal"]
