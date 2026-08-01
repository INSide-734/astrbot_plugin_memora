"""记忆再巩固候选 — 召回只生成候选，人工确认后 CAS 应用，可回滚。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.api import logger

from ..storage.reconsolidation_store import (
    ReconsolidationCandidateConflictError,
    ReconsolidationCandidateNotFoundError,
    ReconsolidationStore,
)
from .memory_engine_evolution_hooks import memory_revision

_EVIDENCE_TYPE = "llm_revision"


class ReconsolidationManager:
    """把 LLM 修订包装成可审阅候选，不直接修改 canonical。

    默认关闭；启用后召回钩子只调用 ``maybe_propose()`` 生成 pending 候选，
    应用与回滚必须显式调用并携带 ``expected_revision`` CAS。
    """

    _RECONSOLIDATION_PROMPT = (
        "你是记忆编辑器。根据最近上下文对以下长期记忆做轻微修正（保持原意）。"
        "如记忆已准确，直接返回原内容。\n\n"
        "原始记忆: {original}\n"
        "近期上下文: {context}\n\n"
        "返回修正后记忆（仅返回内容，无额外文字）："
    )

    def __init__(
        self,
        store: ReconsolidationStore,
        *,
        get_memory_cb: Callable[..., Awaitable[dict[str, Any] | None]] | None = None,
        llm_caller: Callable[..., Awaitable[str | None]] | None = None,
        enabled: bool = False,
        min_recall_count: int = 5,
    ) -> None:
        """初始化候选 Store、读取回调与可选 LLM 调用器。"""

        self._store = store
        self._get_memory = get_memory_cb
        self._llm = llm_caller
        self._enabled = enabled
        self._min_recall_count = max(1, int(min_recall_count))

    async def maybe_propose(
        self,
        memory_id: int,
        context: str = "",
    ) -> dict[str, Any] | None:
        """为一条召回记忆生成再巩固候选；不写 canonical、不存上下文正文。

        Args:
            memory_id: canonical 记忆整数 ID。
            context: 本次请求的查询文本（仅用于 LLM 提示，不持久化）。

        Returns:
            新候选摘要；功能关闭、缺回调、样本不足、LLM 失败或内容未变化时返回 None。
        """

        if (
            not self._enabled
            or self._store is None
            or self._get_memory is None
            or self._llm is None
        ):
            return None
        memory = await self._get_memory(memory_id)
        if not memory:
            return None
        text = memory.get("text") or memory.get("content") or ""
        if not text.strip():
            return None
        metadata = memory.get("metadata", {}) or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        if int(metadata.get("access_count", 0) or 0) < self._min_recall_count:
            return None
        if not context.strip():
            return None
        source_revision = memory_revision(memory)
        if not source_revision:
            return None
        revised = await self._call_llm(text, context)
        if revised is None or revised == text.strip():
            return None
        candidate = await self._store.stage_candidate(
            memory_id=memory_id,
            source_revision=source_revision,
            old_content=text,
            old_metadata=metadata,
            proposed_content=revised,
            change_summary="LLM 修订候选",
            evidence_type=_EVIDENCE_TYPE,
        )
        return {
            "candidate_id": candidate["candidate_id"],
            "memory_id": candidate["memory_id"],
            "status": candidate["status"],
        }

    async def apply_candidate(
        self,
        candidate_id: str,
        update_memory_cb: Callable[..., Awaitable[bool]],
    ) -> dict[str, Any]:
        """按来源 revision CAS 应用候选；stale 时拒绝并标记 rejected。

        Args:
            candidate_id: 待应用候选 ID。
            update_memory_cb: 等价于 ``MemoryEngine.update_memory`` 的回调。

        Returns:
            ``{"applied": bool, "reason_code": str, ...}``。
        """

        candidate = await self._store.get_candidate(candidate_id)
        if candidate is None:
            raise ReconsolidationCandidateNotFoundError(candidate_id)
        if candidate["status"] != "pending":
            raise ReconsolidationCandidateConflictError("candidate_status_changed")
        new_metadata = dict(candidate["old_metadata"])
        new_metadata["reconsolidation_count"] = (
            int(new_metadata.get("reconsolidation_count", 0)) + 1
        )
        new_metadata["last_reconsolidated_at"] = time.time()
        new_metadata.pop("reconsolidation_history", None)
        applied = await update_memory_cb(
            candidate["memory_id"],
            {
                "content": candidate["proposed_content"],
                "metadata": new_metadata,
            },
            expected_revision=candidate["source_revision"],
        )
        if not applied:
            reason_code = str(
                getattr(update_memory_cb, "_last_write_reason_code", None)
                or "apply_failed"
            )
            await self._store.transition(
                candidate_id,
                expected_status="pending",
                new_status="rejected",
                reason_code=reason_code,
                action="reject",
            )
            return {"applied": False, "reason_code": reason_code}
        updated = await self._store.transition(
            candidate_id,
            expected_status="pending",
            new_status="approved",
            reason_code="applied",
            action="apply",
        )
        return {"applied": True, "candidate": updated}

    async def rollback_candidate(
        self,
        candidate_id: str,
        *,
        get_memory_cb: Callable[..., Awaitable[dict[str, Any] | None]],
        update_memory_cb: Callable[..., Awaitable[bool]],
    ) -> dict[str, Any]:
        """把已批准候选回滚到旧正文；按当前 revision CAS，避免覆盖新编辑。

        Returns:
            ``{"restored": bool, "reason_code": str}``。
        """

        candidate = await self._store.get_candidate(candidate_id)
        if candidate is None:
            raise ReconsolidationCandidateNotFoundError(candidate_id)
        if candidate["status"] != "approved":
            raise ReconsolidationCandidateConflictError("candidate_status_changed")
        memory = await get_memory_cb(candidate["memory_id"])
        current_revision = memory_revision(memory) if memory else None
        if not current_revision:
            return {"restored": False, "reason_code": "source_not_found"}
        restored = await update_memory_cb(
            candidate["memory_id"],
            {
                "content": candidate["old_content"],
                "metadata": candidate["old_metadata"],
            },
            expected_revision=current_revision,
        )
        if not restored:
            reason_code = str(
                getattr(update_memory_cb, "_last_write_reason_code", None)
                or "rollback_failed"
            )
            return {"restored": False, "reason_code": reason_code}
        await self._store.transition(
            candidate_id,
            expected_status="approved",
            new_status="rolled_back",
            reason_code="restored",
            action="rollback",
        )
        return {"restored": True, "reason_code": "restored"}

    async def _call_llm(self, text: str, context: str) -> str | None:
        """调用 LLM 生成修订文本；失败或输出过短返回 None。"""

        try:
            prompt = self._RECONSOLIDATION_PROMPT.format(
                original=text,
                context=context[:500],
            )
            result = await self._llm(prompt)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[再巩固] LLM 候选生成失败")
            return None
        value = str(result or "").strip()
        return value if len(value) >= 10 else None


__all__ = [
    "ReconsolidationCandidateConflictError",
    "ReconsolidationCandidateNotFoundError",
    "ReconsolidationManager",
]
