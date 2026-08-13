"""受主召回剩余软预算约束的自发与前瞻辅助召回。"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from astrbot.api import logger

from ...memory.application.retrieval_timing import RetrievalTimingSink
from ...retrieval.rrf_fusion import HybridResult

T = TypeVar("T")

_SPONTANEOUS_SEEDS = (
    "重要的事情",
    "开心的回忆",
    "最近发生的事",
    "之前的对话",
    "难忘的经历",
)


class AuxiliaryRecall:
    """执行不影响主检索计时的低优先级辅助召回。"""

    def __init__(self, config_manager: Any, memory_engine: Any) -> None:
        """保存配置与记忆引擎依赖。"""

        self._config_manager = config_manager
        self._memory_engine = memory_engine

    def prospective_enabled(self) -> bool:
        """读取标准前瞻召回开关，并兼容旧版回退配置。"""

        enabled = self._config_manager.get(
            "recall_engine.prospective_recall_enabled",
            None,
        )
        if enabled is None:
            enabled = self._config_manager.get("prospective.enabled", True)
        return bool(enabled)

    async def maybe_spontaneous_recall(
        self,
        *,
        session_id: str | None,
        persona_id: str | None,
        chat_type: str,
        deadline_monotonic: float | None,
    ) -> list[Any]:
        """在剩余预算内按低概率执行独立计时的宽泛记忆搜索。"""

        if _deadline_exhausted(deadline_monotonic):
            return []
        if not self._config_manager.get(
            "recall_engine.spontaneous_recall_enabled",
            True,
        ):
            return []
        probability = float(
            self._config_manager.get(
                "recall_engine.spontaneous_recall_probability",
                0.06,
            )
        )
        if random.random() >= probability:
            return []

        seed_query = random.choice(_SPONTANEOUS_SEEDS)
        spontaneous_k = int(
            self._config_manager.get("recall_engine.spontaneous_recall_k", 2)
        )
        timing_sink = RetrievalTimingSink()

        async def search() -> list[Any]:
            """执行与主召回计时隔离的辅助搜索。"""

            return await self._memory_engine.search_memories(
                query=seed_query,
                k=spontaneous_k,
                session_id=session_id,
                persona_id=persona_id,
                chat_type=chat_type,
                timing_sink=timing_sink,
                deadline_monotonic=deadline_monotonic,
            )

        try:
            results = await _await_with_deadline(search, deadline_monotonic)
            if results is None:
                return []
            for result in results:
                metadata = result.metadata or {}
                metadata["recall_source"] = "spontaneous"
                result.metadata = metadata
            return results
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("自发回忆检索失败", exc_info=True)
            return []

    async def maybe_prospective_recall(
        self,
        *,
        session_id: str | None,
        persona_id: str | None,
        chat_type: str,
        deadline_monotonic: float | None,
    ) -> list[Any]:
        """在剩余预算内查询即将到期的 PLANNED 原子。"""

        if _deadline_exhausted(deadline_monotonic) or not self.prospective_enabled():
            return []
        engine = self._memory_engine
        if not hasattr(engine, "atom_store") or engine.atom_store is None:
            return []

        lookahead_hours = float(
            self._config_manager.get(
                "recall_engine.prospective_lookahead_hours",
                24.0,
            )
        )
        prospective_k = int(
            self._config_manager.get("recall_engine.prospective_recall_k", 3)
        )

        async def query_planned() -> list[Any]:
            """按可信聊天作用域读取即将到期的计划原子。"""

            return await engine.atom_store.query_upcoming_planned(
                lookahead_sec=lookahead_hours * 3600.0,
                session_id=session_id,
                persona_id=persona_id,
                chat_type=chat_type,
                limit=prospective_k,
            )

        try:
            planned_atoms = await _await_with_deadline(
                query_planned,
                deadline_monotonic,
            )
            if not planned_atoms:
                return []
            results: list[HybridResult] = []
            for atom in planned_atoms:
                metadata = atom.metadata or {}
                metadata["recall_source"] = "prospective"
                metadata["atom_type"] = "planned"
                metadata["event_time"] = atom.event_time
                results.append(
                    HybridResult(
                        doc_id=atom.parent_memory_id,
                        final_score=0.9,
                        rrf_score=0.9,
                        bm25_score=None,
                        vector_score=None,
                        content=f"[待办] {atom.content}",
                        metadata=metadata,
                    )
                )
            return results
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("前瞻记忆扫描失败", exc_info=True)
            return []


def _deadline_exhausted(deadline_monotonic: float | None) -> bool:
    """判断绝对单调截止时间是否已经耗尽。"""

    return deadline_monotonic is not None and deadline_monotonic <= time.perf_counter()


async def _await_with_deadline(
    factory: Callable[[], Awaitable[T]],
    deadline_monotonic: float | None,
) -> T | None:
    """在剩余绝对预算内执行辅助 I/O；超时返回空降级信号。"""

    if deadline_monotonic is None:
        return await factory()
    remaining = max(0.0, deadline_monotonic - time.perf_counter())
    if remaining <= 0.0:
        return None

    task = asyncio.create_task(factory())
    try:
        return await asyncio.wait_for(task, timeout=remaining)
    except TimeoutError:
        await asyncio.gather(task, return_exceptions=True)
        logger.debug("辅助召回超过 LLM 前软预算，已跳过")
        return None
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise


__all__ = ["AuxiliaryRecall"]
