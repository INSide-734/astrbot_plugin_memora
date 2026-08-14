"""召回候选归并、认知上下文与辅助召回适配。"""

from __future__ import annotations

import hashlib
from typing import Any

from astrbot.api import logger

from ...observability.application import runtime as observability
from .continuity import build_continuity_context
from .reconsolidation_dispatch import schedule_reconsolidation_proposal


class RecallContextMixin:
    """为 RecallHandler 提供候选归并和上下文增强。"""

    @staticmethod
    def _finalize_recall_candidates(
        candidates: list[Any],
        top_k: int,
    ) -> list[Any]:
        """对多来源召回候选去重，并执行注入数量上限。"""
        if top_k <= 0 or not candidates:
            return []

        source_priority = {
            "prospective": 3,
            "main": 2,
            "spontaneous": 1,
        }

        def candidate_key(item: Any) -> tuple[str, Any]:
            doc_id = getattr(item, "doc_id", None)
            if doc_id is not None:
                return ("doc", doc_id)
            metadata = getattr(item, "metadata", None) or {}
            source = metadata.get("recall_source", "main")
            content = str(getattr(item, "content", "") or "")
            digest = hashlib.sha256(f"{source}\0{content}".encode("utf-8")).hexdigest()
            return ("content", digest)

        def rank_tuple(item: Any) -> tuple[int, float]:
            metadata = getattr(item, "metadata", None) or {}
            source = metadata.get("recall_source", "main")
            return (
                source_priority.get(str(source), source_priority["main"]),
                float(getattr(item, "final_score", 0.0) or 0.0),
            )

        deduped: dict[tuple[str, Any], Any] = {}
        for item in candidates:
            key = candidate_key(item)
            existing = deduped.get(key)
            if existing is None or rank_tuple(item) > rank_tuple(existing):
                deduped[key] = item

        finalized = list(deduped.values())
        finalized.sort(key=rank_tuple, reverse=True)
        return finalized[:top_k]

    async def _build_cognitive_context(
        self,
        text: str,
        group_id: str,
        persona_id: str,
    ) -> str:
        """构建来自 v1.0+ 认知模块的可选只读上下文。"""
        parts: list[str] = []
        continuity_context = build_continuity_context(
            self._memory_engine,
            group_id,
        )
        if continuity_context:
            parts.append(continuity_context)
        try:
            if self._jargon_query_service is not None:
                explanation = await self._jargon_query_service.check_and_explain(
                    text, group_id
                )
                if explanation:
                    parts.append(explanation)
        except Exception:
            logger.debug("[召回流程] 黑话解释构建失败", exc_info=True)

        try:
            if self._expression_learner is not None:
                patterns = await self._expression_learner.format_patterns_for_prompt(
                    group_id=group_id,
                    persona_id=persona_id,
                    limit=3,
                )
                if patterns:
                    parts.append(patterns)
        except Exception:
            logger.debug("[召回流程] 表达模式格式化失败", exc_info=True)

        try:
            if self._affection_manager is not None:
                status = await self._affection_manager.get_group_affection_status(
                    group_id
                )
                mood = status.get("current_mood") if isinstance(status, dict) else None
                if mood:
                    parts.append(
                        "[互动状态]\n"
                        f"- 当前情绪: {mood.get('type', 'calm')} "
                        f"({mood.get('description', '')})"
                    )
        except Exception:
            logger.debug("[召回流程] 好感度上下文构建失败", exc_info=True)

        return "\n".join(parts)

    async def _maybe_propose_reconsolidation(
        self,
        memories: list[Any],
        query: str,
    ) -> None:
        """把最高分记忆的再巩固候选交给引擎生命周期任务所有者。"""

        await schedule_reconsolidation_proposal(
            self._memory_engine,
            memories,
            query,
        )

    async def _build_fallback_query(self, session_id: str) -> str | None:
        """从最近历史消息构建回退查询，用于空消息场景（如纯 @mention）。

        获取最近 5 条消息，取最近 3 条非空内容拼接为查询字符串。
        """
        try:
            recent = await self._conversation_manager.get_context(
                session_id,
                max_messages=5,
            )
            if not recent or len(recent) <= 1:
                return None
            parts: list[str] = []
            for msg in reversed(recent[1:]):
                content = msg.get("content", "")
                if content and content.strip():
                    parts.append(content.strip())
            return " ".join(parts[:3]) if parts else None
        except Exception:
            logger.debug(
                f"[{session_id}] 构建回退查询失败",
                exc_info=True,
            )
            return None

    @observability.monitored
    async def _maybe_spontaneous_recall(
        self,
        session_id: str | None,
        persona_id: str | None,
        chat_type: str,
        deadline_monotonic: float | None = None,
    ) -> list[Any]:
        """兼容旧调用边界，并委托独立组件执行受预算约束的自发回忆。"""

        return await self._auxiliary_recall.maybe_spontaneous_recall(
            session_id=session_id,
            persona_id=persona_id,
            chat_type=chat_type,
            deadline_monotonic=deadline_monotonic,
        )

    def _prospective_recall_enabled(self) -> bool:
        """读取标准前瞻召回开关，并兼容旧版回退配置。"""

        return self._auxiliary_recall.prospective_enabled()

    @observability.monitored
    async def _maybe_prospective_recall(
        self,
        session_id: str | None,
        persona_id: str | None,
        chat_type: str,
        deadline_monotonic: float | None = None,
    ) -> list[Any]:
        """兼容旧调用边界，并委托独立组件执行受预算约束的前瞻召回。"""

        return await self._auxiliary_recall.maybe_prospective_recall(
            session_id=session_id,
            persona_id=persona_id,
            chat_type=chat_type,
            deadline_monotonic=deadline_monotonic,
        )

    _memory_engine: Any
    _conversation_manager: Any
    _jargon_query_service: Any
    _expression_learner: Any
    _affection_manager: Any
    _auxiliary_recall: Any
