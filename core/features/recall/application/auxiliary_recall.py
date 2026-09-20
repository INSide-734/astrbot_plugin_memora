"""受主召回剩余软预算约束的自发与前瞻辅助召回。"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from astrbot.api import logger

from ....shared.memory_status import is_memory_recallable
from ...memory.application.fact_text_alignment import normalize_fact
from ...memory.application.retrieval_timing import RetrievalTimingSink
from ...memory.graph.domain.models import GraphQueryScope
from ...memory.infrastructure.atom_source_integrity import (
    _metadata_dict,
    current_canonical_facts,
)
from ...quality.application.gate_disposition_filter import is_mark_write
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
        query_scope: GraphQueryScope | None = None,
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
                query_scope=query_scope,
                require_user_evidence=True,
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
        """在剩余预算内查询即将到期且有用户来源证据的 PLANNED 原子。"""

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
            facts_by_parent = await _await_with_deadline(
                lambda: self._load_parent_facts(
                    planned_atoms,
                    session_id=session_id,
                    persona_id=persona_id,
                    chat_type=chat_type,
                ),
                deadline_monotonic,
            )
            if facts_by_parent is None:
                return []
            results: list[HybridResult] = []
            for atom in planned_atoms:
                fact_text = self._current_fact_text(atom, facts_by_parent)
                if not fact_text:
                    # 父 canonical 已改写或事实表示不可判定：丢弃该信号，
                    # 模型可见正文只允许来自 canonical 事实。
                    continue
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
                        content=f"[待办] {fact_text}",
                        metadata=metadata,
                    )
                )
            return results
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("前瞻记忆扫描失败", exc_info=True)
            return []

    async def _load_parent_facts(
        self,
        atoms: list[Any],
        *,
        session_id: str | None = None,
        persona_id: str | None = None,
        chat_type: str | None = None,
    ) -> dict[int, dict[str, str]]:
        """批量读取这些 Atom 的父 canonical 事实集合，并复核当前可读边界。

        每个父记忆读取一次 canonical 详情；**读取时**还要按当前 canonical 状态
        复核一次可读性，闭合「查询与加载之间父被归档/删除/改写/换作用域」的
        TOCTOU 窗口（``get_memory`` 是裸 ID 读取，本身没有状态门）：

        1. 父行缺失或 metadata 不可解析 → 丢弃；
        2. ``is_memory_recallable`` 为假（归档、休眠、删除、总结来源 orphan 与
           未知状态；``replacement_pending`` 暂态行按 ``deleted`` 状态写入，
           同属不可召回）→ 丢弃；
        3. ``is_mark_write`` 暂态行 → 丢弃；
        4. 行声明了 session/persona 且与本次请求冲突 → 丢弃；群聊丢弃
           ``confidential`` 父行（缺字段按既有口径视为 ``shared``）；
        5. 事实表示仍须与当前正文对齐（``current_canonical_facts``，沿用事实
           文本单 owner 判定）。

        任一不满足只丢弃该父来源并留 debug 计数（不回显正文/事实/身份），
        由 ``_current_fact_text`` 判为不可注入。
        """

        facts_by_parent: dict[int, dict[str, str]] = {}
        dropped = 0
        for memory_id in sorted(
            {int(getattr(atom, "parent_memory_id", 0) or 0) for atom in atoms}
        ):
            if memory_id <= 0:
                continue
            document = await self._memory_engine.get_memory(memory_id)
            metadata = _metadata_dict((document or {}).get("metadata"))
            if not document or not self._parent_readable_now(
                metadata,
                session_id=session_id,
                persona_id=persona_id,
                chat_type=chat_type,
            ):
                dropped += 1
                continue
            facts_by_parent[memory_id] = current_canonical_facts(
                document.get("text"),
                metadata,
            )
        if dropped:
            logger.debug("[前瞻召回] 丢弃不可读父来源：count=%d", dropped)
        return facts_by_parent

    @staticmethod
    def _parent_readable_now(
        metadata: dict[str, Any],
        *,
        session_id: str | None,
        persona_id: str | None,
        chat_type: str | None,
    ) -> bool:
        """按当前 canonical 状态与本次请求边界判断父来源是否可读。

        复用既有判定口径：``is_memory_recallable`` 与 ``is_mark_write``；再按请求
        的 session/persona 与群聊隐私口径过滤。行未声明 session/persona 时按
        「无冲突」处理：既不放行明确冲突的行，也不误杀未声明作用域的旧行。
        """

        if not is_memory_recallable(metadata) or is_mark_write(metadata):
            return False
        row_session = metadata.get("session_id")
        if session_id is not None and row_session is not None:
            if str(row_session) != session_id:
                return False
        row_persona = metadata.get("persona_id")
        if persona_id is not None and row_persona is not None:
            if str(row_persona) != persona_id:
                return False
        if chat_type == "group" and metadata.get("privacy_level", "shared") == (
            "confidential"
        ):
            return False
        return True

    @staticmethod
    def _current_fact_text(
        atom: Any, facts_by_parent: dict[int, dict[str, str]]
    ) -> str:
        """返回 Atom 内容对应的当前 canonical 事实原文；不满足校验时返回空串。"""

        lookup = facts_by_parent.get(int(getattr(atom, "parent_memory_id", 0) or 0))
        if not lookup:
            return ""
        content = getattr(atom, "content", "")
        if not isinstance(content, str):
            return ""
        return lookup.get(normalize_fact(content), "")


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
