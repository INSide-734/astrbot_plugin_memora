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
        update_memory_cb: Callable[..., Awaitable[bool]] | None = None,
        refresh_derived_cb: Callable[[int], Awaitable[bool]] | None = None,
        llm_caller: Callable[..., Awaitable[str | None]] | None = None,
        enabled: bool = False,
        min_recall_count: int = 5,
    ) -> None:
        """初始化候选 Store、读取回调与可选 LLM 调用器。"""

        self._store = store
        self._get_memory = get_memory_cb
        self._update_memory = update_memory_cb
        self._refresh_derived = refresh_derived_cb
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
        candidate = await self._store.begin_apply(
            candidate_id,
            expected_revision=candidate["source_revision"],
        )
        payload = self._build_apply_payload(candidate)
        applied = await update_memory_cb(
            candidate["memory_id"],
            payload,
            expected_revision=candidate["source_revision"],
        )
        if not applied:
            reason_code = str(
                getattr(update_memory_cb, "_last_write_reason_code", None)
                or "apply_failed"
            )
            await self._store.complete_apply(
                candidate_id,
                applied=False,
                reason_code=reason_code,
            )
            return {"applied": False, "reason_code": reason_code}
        updated = await self._store.complete_apply(
            candidate_id,
            applied=True,
            reason_code="applied",
        )
        return {"applied": True, "candidate": updated}

    @staticmethod
    def _build_apply_payload(candidate: dict[str, Any]) -> dict[str, Any]:
        """从候选旧 metadata 构造 canonical apply payload。"""

        metadata = dict(candidate["old_metadata"])
        metadata["reconsolidation_count"] = (
            int(metadata.get("reconsolidation_count", 0)) + 1
        )
        metadata["last_reconsolidated_at"] = time.time()
        metadata.pop("reconsolidation_history", None)
        return {
            "content": candidate["proposed_content"],
            "metadata": metadata,
        }

    async def recover_incomplete_applies(self) -> dict[str, int]:
        """在启动时依据 canonical 事实恢复未收口的 apply intent。

        当前正文已经等于候选提案时只补 Store 收口；仍是旧正文且 revision 未变时
        重试一次 CAS；其他 revision/正文组合进入失败状态，不覆盖后续编辑。

        Returns:
            已恢复和已阻断的 intent 数量。

        Raises:
            asyncio.CancelledError: 恢复被取消时继续传播。
        """

        if self._get_memory is None or self._update_memory is None:
            return {"recovered": 0, "blocked": 0}
        operations = await self._store.list_incomplete_applies()
        recovered = 0
        blocked = 0
        for operation in operations:
            candidate_id = str(operation["candidate_id"])
            try:
                candidate = await self._store.get_candidate(candidate_id)
                memory = (
                    await self._get_memory(candidate["memory_id"])
                    if candidate is not None
                    else None
                )
                current_revision = memory_revision(memory) if memory else None
                current_content = str(
                    (memory or {}).get("text") or (memory or {}).get("content") or ""
                )
                expected_revision = str(operation["expected_revision"])
                if candidate is None or current_revision is None:
                    await self._store.mark_apply_blocked(
                        candidate_id,
                        reason_code="source_not_found",
                    )
                    blocked += 1
                    continue
                if current_content == str(candidate["proposed_content"]):
                    await self._store.complete_apply(
                        candidate_id,
                        applied=True,
                        reason_code="recovered_applied",
                    )
                    recovered += 1
                    continue
                if current_revision != expected_revision or current_content != str(
                    candidate["old_content"]
                ):
                    await self._store.mark_apply_blocked(
                        candidate_id,
                        reason_code="source_revision_mismatch",
                    )
                    blocked += 1
                    continue
                applied = await self._update_memory(
                    candidate["memory_id"],
                    self._build_apply_payload(candidate),
                    expected_revision=expected_revision,
                )
                if applied:
                    await self._store.complete_apply(
                        candidate_id,
                        applied=True,
                        reason_code="recovered_applied",
                    )
                    recovered += 1
                else:
                    reason_code = str(
                        getattr(self._update_memory, "_last_write_reason_code", None)
                        or "apply_failed"
                    )
                    await self._store.complete_apply(
                        candidate_id,
                        applied=False,
                        reason_code=reason_code,
                    )
                    blocked += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error(
                    "[再巩固] apply 恢复失败，reason_code=apply_recovery_failed"
                )
                blocked += 1
        return {"recovered": recovered, "blocked": blocked}

    async def reject_candidate(
        self,
        candidate_id: str,
        *,
        reason_code: str = "manual_reject",
    ) -> dict[str, Any]:
        """拒绝 pending 候选并记录低敏动作审计。

        Args:
            candidate_id: 待拒绝候选 ID。
            reason_code: 面向维护和诊断的稳定拒绝原因码。

        Returns:
            状态迁移后的候选摘要。

        Raises:
            ReconsolidationCandidateNotFoundError: 候选不存在。
            ReconsolidationCandidateConflictError: 候选已被其他动作处理。
        """

        candidate = await self._store.get_candidate(candidate_id)
        if candidate is None:
            raise ReconsolidationCandidateNotFoundError(candidate_id)
        if candidate["status"] != "pending":
            raise ReconsolidationCandidateConflictError("candidate_status_changed")
        return await self._store.transition(
            candidate_id,
            expected_status="pending",
            new_status="rejected",
            reason_code=reason_code,
            action="reject",
        )

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
        await self._store.begin_rollback(
            candidate_id,
            expected_revision=current_revision,
        )
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
            await self._store.cancel_rollback(candidate_id)
            return {"restored": False, "reason_code": reason_code}
        if self._refresh_derived is not None:
            refreshed = await self._refresh_derived(candidate["memory_id"])
            if not refreshed:
                return {"restored": False, "reason_code": "derived_refresh_failed"}
        await self._store.complete_rollback(candidate_id)
        return {"restored": True, "reason_code": "restored"}

    async def recover_incomplete_rollbacks(self) -> dict[str, int]:
        """在启动时安全重放跨 Store 未完成回滚。

        仅当 canonical revision 仍等于开始值，或当前正文已经等于目标旧正文时
        才允许重放。后者会再次走正常更新入口，用于补刷 FTS、FAISS、graph 和
        evolution 派生状态；其他 revision/正文组合标记为 blocked。

        Returns:
            已恢复和已阻塞操作数，不包含候选 ID 或正文。

        Raises:
            asyncio.CancelledError: 启动恢复被取消时继续传播。
        """

        if self._get_memory is None or self._update_memory is None:
            return {"recovered": 0, "blocked": 0}
        try:
            operations = await self._store.list_incomplete_rollbacks()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                "[再巩固] 未完成回滚读取失败，reason_code=rollback_scan_failed"
            )
            return {"recovered": 0, "blocked": 1}

        recovered = 0
        blocked = 0
        for operation in operations:
            candidate_id = str(operation["candidate_id"])
            try:
                candidate = await self._store.get_candidate(candidate_id)
                memory = (
                    await self._get_memory(candidate["memory_id"])
                    if candidate is not None
                    else None
                )
                current_revision = memory_revision(memory) if memory else None
                current_content = str(
                    (memory or {}).get("text") or (memory or {}).get("content") or ""
                )
                expected_revision = str(operation["expected_revision"])
                if candidate is None or candidate.get("status") != "approved":
                    reason_code = "candidate_status_changed"
                elif not current_revision:
                    reason_code = "source_not_found"
                elif current_revision != expected_revision and current_content != str(
                    candidate["old_content"]
                ):
                    reason_code = "source_revision_mismatch"
                else:
                    restored = await self._update_memory(
                        candidate["memory_id"],
                        {
                            "content": candidate["old_content"],
                            "metadata": candidate["old_metadata"],
                        },
                        expected_revision=current_revision,
                    )
                    if restored:
                        if self._refresh_derived is not None:
                            refreshed = await self._refresh_derived(
                                candidate["memory_id"]
                            )
                            if not refreshed:
                                reason_code = "derived_refresh_failed"
                                await self._store.mark_rollback_blocked(
                                    candidate_id,
                                    reason_code=reason_code,
                                )
                                blocked += 1
                                continue
                        await self._store.complete_rollback(candidate_id)
                        recovered += 1
                        continue
                    reason_code = str(
                        getattr(self._update_memory, "_last_write_reason_code", None)
                        or "rollback_failed"
                    )
                await self._store.mark_rollback_blocked(
                    candidate_id,
                    reason_code=reason_code,
                )
                blocked += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error(
                    "[再巩固] 回滚恢复失败，reason_code=rollback_recovery_failed"
                )
                blocked += 1
        return {"recovered": recovered, "blocked": blocked}

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
