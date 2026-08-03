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
        payload = self._build_apply_payload(candidate)
        candidate = await self._store.begin_apply(
            candidate_id,
            expected_revision=candidate["source_revision"],
            target_metadata=payload["metadata"],
        )
        applied = await update_memory_cb(
            candidate["memory_id"],
            payload,
            expected_revision=candidate["source_revision"],
        )
        if not applied:
            reason_code = self._write_reason_code(update_memory_cb, "apply_failed")
            await self._store.complete_apply(
                candidate_id,
                applied=False,
                reason_code=reason_code,
            )
            return {"applied": False, "reason_code": reason_code}
        updated = await self._finalize_applied_candidate(
            candidate_id,
            candidate,
            target_metadata=payload["metadata"],
            reason_code="applied",
        )
        if updated is None:
            return {"applied": False, "reason_code": "apply_result_unverified"}
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
        metadata.pop("updated_at", None)
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
                current_metadata = self._memory_metadata(memory)
                expected_revision = str(operation["expected_revision"])
                target_metadata = operation.get("target_metadata")
                if candidate is None or current_revision is None:
                    await self._store.mark_apply_blocked(
                        candidate_id,
                        reason_code="source_not_found",
                    )
                    blocked += 1
                    continue
                if not isinstance(target_metadata, dict) or not target_metadata:
                    await self._store.mark_apply_blocked(
                        candidate_id,
                        reason_code="apply_target_missing",
                    )
                    blocked += 1
                    continue
                if current_content == str(
                    candidate["proposed_content"]
                ) and self._metadata_matches(current_metadata, target_metadata):
                    if current_revision == expected_revision:
                        await self._store.mark_apply_blocked(
                            candidate_id,
                            reason_code="apply_revision_not_advanced",
                        )
                        blocked += 1
                        continue
                    await self._store.complete_apply(
                        candidate_id,
                        applied=True,
                        reason_code="recovered_applied",
                        applied_revision=current_revision,
                        applied_metadata=current_metadata,
                    )
                    recovered += 1
                    continue
                if (
                    current_revision != expected_revision
                    or current_content != str(candidate["old_content"])
                    or not self._metadata_matches(
                        current_metadata,
                        candidate["old_metadata"],
                    )
                ):
                    await self._store.mark_apply_blocked(
                        candidate_id,
                        reason_code="source_revision_mismatch",
                    )
                    blocked += 1
                    continue
                applied = await self._update_memory(
                    candidate["memory_id"],
                    {
                        "content": candidate["proposed_content"],
                        "metadata": target_metadata,
                    },
                    expected_revision=expected_revision,
                )
                if applied:
                    updated = await self._finalize_applied_candidate(
                        candidate_id,
                        candidate,
                        target_metadata=target_metadata,
                        reason_code="recovered_applied",
                    )
                    if updated is None:
                        blocked += 1
                    else:
                        recovered += 1
                else:
                    reason_code = self._write_reason_code(
                        self._update_memory,
                        "apply_failed",
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

    async def _finalize_applied_candidate(
        self,
        candidate_id: str,
        candidate: dict[str, Any],
        *,
        target_metadata: dict[str, Any],
        reason_code: str,
    ) -> dict[str, Any] | None:
        """核验 apply 结果；未知提交状态保留 pending intent 供启动对账。"""

        if self._get_memory is None:
            await self._store.mark_apply_recovery_required(
                candidate_id,
                reason_code="apply_verification_unavailable",
            )
            return None
        memory = await self._get_memory(candidate["memory_id"])
        revision = memory_revision(memory) if memory else ""
        content = self._memory_content(memory)
        metadata = self._memory_metadata(memory)
        if (
            not revision
            or revision == str(candidate["source_revision"])
            or content != str(candidate["proposed_content"])
            or not self._metadata_matches(metadata, target_metadata)
        ):
            await self._store.mark_apply_recovery_required(
                candidate_id,
                reason_code="apply_result_unverified",
            )
            return None
        return await self._store.complete_apply(
            candidate_id,
            applied=True,
            reason_code=reason_code,
            applied_revision=revision,
            applied_metadata=metadata,
        )

    @staticmethod
    def _write_reason_code(
        update_memory_cb: Callable[..., Awaitable[bool]],
        fallback: str,
    ) -> str:
        """从真实 bound method 所属引擎读取同步写入原因码。"""

        owner = getattr(update_memory_cb, "__self__", None)
        getter = getattr(owner, "get_last_write_reason_code", None)
        reason = getter() if callable(getter) else None
        if not reason:
            reason = getattr(update_memory_cb, "_last_write_reason_code", None)
        return str(reason or fallback)

    @staticmethod
    def _memory_content(memory: dict[str, Any] | None) -> str:
        """从 canonical 读取结果提取正文。"""

        return str((memory or {}).get("text") or (memory or {}).get("content") or "")

    @staticmethod
    def _memory_metadata(memory: dict[str, Any] | None) -> dict[str, Any]:
        """从 canonical 读取结果解析独立 metadata 字典。"""

        metadata = (memory or {}).get("metadata", {})
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, json.JSONDecodeError):
                return {}
        return dict(metadata) if isinstance(metadata, dict) else {}

    @staticmethod
    def _metadata_matches(
        current: dict[str, Any],
        expected: dict[str, Any],
    ) -> bool:
        """比较语义 metadata，忽略每次 canonical 写都会推进的时间字段。"""

        current_copy = dict(current)
        expected_copy = dict(expected)
        current_copy.pop("updated_at", None)
        expected_copy.pop("updated_at", None)
        return current_copy == expected_copy

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
        """把已批准候选回滚到旧状态；仅接受 apply 后原始 revision。

        Returns:
            ``{"restored": bool, "reason_code": str}``。
        """

        candidate = await self._store.get_candidate(candidate_id)
        if candidate is None:
            raise ReconsolidationCandidateNotFoundError(candidate_id)
        if candidate["status"] != "approved":
            raise ReconsolidationCandidateConflictError("candidate_status_changed")
        applied_revision = str(candidate.get("applied_revision") or "").strip()
        applied_metadata = candidate.get("applied_metadata")
        if not applied_revision or not isinstance(applied_metadata, dict):
            return {"restored": False, "reason_code": "apply_revision_missing"}
        memory = await get_memory_cb(candidate["memory_id"])
        current_revision = memory_revision(memory) if memory else None
        if not current_revision:
            return {"restored": False, "reason_code": "source_not_found"}
        if (
            current_revision != applied_revision
            or self._memory_content(memory) != str(candidate["proposed_content"])
            or not self._metadata_matches(
                self._memory_metadata(memory),
                applied_metadata,
            )
        ):
            return {
                "restored": False,
                "reason_code": "source_revision_mismatch",
            }
        await self._store.begin_rollback(
            candidate_id,
            expected_revision=applied_revision,
        )
        restored = await update_memory_cb(
            candidate["memory_id"],
            {
                "content": candidate["old_content"],
                "metadata": candidate["old_metadata"],
            },
            expected_revision=applied_revision,
        )
        if not restored:
            reason_code = self._write_reason_code(
                update_memory_cb,
                "rollback_failed",
            )
            await self._store.cancel_rollback(candidate_id)
            return {"restored": False, "reason_code": reason_code}
        restored_memory = await get_memory_cb(candidate["memory_id"])
        restored_revision = memory_revision(restored_memory) if restored_memory else ""
        if (
            not restored_revision
            or restored_revision == applied_revision
            or self._memory_content(restored_memory) != str(candidate["old_content"])
            or not self._metadata_matches(
                self._memory_metadata(restored_memory),
                candidate["old_metadata"],
            )
        ):
            await self._store.mark_rollback_blocked(
                candidate_id,
                reason_code="rollback_result_unverified",
            )
            return {
                "restored": False,
                "reason_code": "rollback_result_unverified",
            }
        if self._refresh_derived is not None:
            refreshed = await self._refresh_derived(candidate["memory_id"])
            if not refreshed:
                # canonical 已经恢复，但派生索引尚未收口；保留 pending intent，
                # 让后续生命周期按当前旧正文重试刷新，不能伪装成安全终态。
                return {"restored": False, "reason_code": "derived_refresh_failed"}
        await self._store.complete_rollback(candidate_id)
        return {"restored": True, "reason_code": "restored"}

    async def recover_incomplete_rollbacks(self) -> dict[str, int]:
        """在启动时安全重放跨 Store 未完成回滚。

        revision 未变且仍是 apply 快照时才允许重放 canonical CAS；若当前状态
        已精确等于回滚目标，只补刷派生并收口。其他 revision、正文或 metadata
        组合一律 blocked，不覆盖后续编辑。

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
                current_content = self._memory_content(memory)
                current_metadata = self._memory_metadata(memory)
                expected_revision = str(operation["expected_revision"])
                if candidate is None or candidate.get("status") != "approved":
                    reason_code = "candidate_status_changed"
                elif not current_revision:
                    reason_code = "source_not_found"
                elif str(
                    candidate.get("applied_revision") or ""
                ) != expected_revision or not isinstance(
                    candidate.get("applied_metadata"), dict
                ):
                    reason_code = "source_revision_mismatch"
                elif current_revision == expected_revision:
                    if current_content != str(
                        candidate["proposed_content"]
                    ) or not self._metadata_matches(
                        current_metadata,
                        candidate["applied_metadata"],
                    ):
                        reason_code = "source_revision_mismatch"
                        await self._store.mark_rollback_blocked(
                            candidate_id,
                            reason_code=reason_code,
                        )
                        blocked += 1
                        continue
                    restored = await self._update_memory(
                        candidate["memory_id"],
                        {
                            "content": candidate["old_content"],
                            "metadata": candidate["old_metadata"],
                        },
                        expected_revision=expected_revision,
                    )
                    if restored:
                        memory = await self._get_memory(candidate["memory_id"])
                        current_revision = memory_revision(memory) if memory else None
                        current_content = self._memory_content(memory)
                        current_metadata = self._memory_metadata(memory)
                        if (
                            not current_revision
                            or current_revision == expected_revision
                            or current_content != str(candidate["old_content"])
                            or not self._metadata_matches(
                                current_metadata,
                                candidate["old_metadata"],
                            )
                        ):
                            reason_code = "rollback_result_unverified"
                        elif await self._refresh_and_complete_rollback(
                            candidate_id,
                            candidate["memory_id"],
                        ):
                            recovered += 1
                            continue
                        else:
                            reason_code = "derived_refresh_failed"
                    else:
                        reason_code = self._write_reason_code(
                            self._update_memory,
                            "rollback_failed",
                        )
                elif current_content == str(
                    candidate["old_content"]
                ) and self._metadata_matches(
                    current_metadata,
                    candidate["old_metadata"],
                ):
                    if await self._refresh_and_complete_rollback(
                        candidate_id,
                        candidate["memory_id"],
                    ):
                        recovered += 1
                        continue
                    reason_code = "derived_refresh_failed"
                else:
                    reason_code = "source_revision_mismatch"
                if reason_code != "derived_refresh_failed":
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

    async def _refresh_and_complete_rollback(
        self,
        candidate_id: str,
        memory_id: int,
    ) -> bool:
        """刷新当前 canonical 派生数据并原子收口 rollback intent。"""

        if self._refresh_derived is not None:
            refreshed = await self._refresh_derived(memory_id)
            if not refreshed:
                return False
        await self._store.complete_rollback(candidate_id)
        return True

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
