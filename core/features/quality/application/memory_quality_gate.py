"""编排记忆候选的 pre-canonical 质量门与人工处置。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import secrets
from dataclasses import dataclass
from typing import Any

from ...recall.processors.memory_grounding import MemoryGroundingValidator
from ..infrastructure.quarantine_store import MemoryQuarantineStore
from .gate_rule_engine import CandidateView, evaluate_disposition, evaluate_rules
from .gate_runtime import GateRuntime


@dataclass(frozen=True, slots=True)
class MemoryGateResult:
    """描述质量门对单条候选的路由结论。"""

    action: str
    candidate_id: str | None = None
    reason_codes: tuple[str, ...] = ()
    atoms: list | None = None


class QuarantineApprovalPendingError(RuntimeError):
    """canonical 已写入但 quarantine finalize 未完成，需要管理员 repair。"""

    def __init__(self, candidate_id: str, revision: int, approval_token: str) -> None:
        """记录 repair 所需的候选 revision 和不含候选 ID 语义的 token。"""

        super().__init__("quarantine_approval_finalize_pending")
        self.candidate_id = candidate_id
        self.revision = int(revision)
        self.approval_token = approval_token


class MemoryQualityGate:
    """隔离低质量候选，并仅在重新取证后写入 canonical memory。"""

    def __init__(
        self,
        store: MemoryQuarantineStore,
        *,
        memory_engine: Any,
        memory_processor: Any,
        conversation_manager: Any,
        grounding_validator: MemoryGroundingValidator | None = None,
        gate_runtime: GateRuntime | None = None,
    ) -> None:
        """绑定隔离 Store、canonical 写入入口、来源复核与门禁运行时。"""

        self.store = store
        self.memory_engine = memory_engine
        self.memory_processor = memory_processor
        self.conversation_manager = conversation_manager
        self.grounding_validator = grounding_validator or MemoryGroundingValidator()
        self.gate_runtime = gate_runtime

    async def route_candidate(
        self,
        candidate: dict[str, Any],
        *,
        session_id: str,
        persona_id: str | None,
        source_window: dict[str, Any],
        is_group_chat: bool,
        group_id: str | None = None,
        chat_type: str | None = None,
    ) -> MemoryGateResult:
        """允许可信候选继续写入，其余候选按门禁配置路由。"""

        metadata = candidate.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            candidate["metadata"] = metadata
        reason_codes = self._reason_codes(metadata)
        if not reason_codes:
            return MemoryGateResult(action="allow")

        if self.gate_runtime is not None:
            snapshot = self.gate_runtime.snapshot()
            if snapshot.enabled:
                profile = snapshot.resolve_profile(
                    chat_type or ("group" if is_group_chat else "private"),
                    group_id,
                    persona_id,
                )
                outcome = evaluate_rules(
                    CandidateView(
                        content=str(candidate.get("content") or ""),
                        summary=str(candidate.get("summary") or ""),
                        key_facts=tuple(metadata.get("key_facts") or ()),
                        topics=tuple(metadata.get("topics") or ()),
                        participants=tuple(metadata.get("participants") or ()),
                        importance=float(candidate.get("importance", 0.5)),
                        chat_type="group" if is_group_chat else "private",
                    ),
                    profile,
                )
                disposition = evaluate_disposition(
                    tuple(reason_codes), outcome, profile
                )
                # 应用重要性动作：set_importance 覆盖，否则累加 delta，最终 clamp [0,1]
                base_importance = float(candidate.get("importance", 0.5))
                if outcome.set_importance is not None:
                    candidate["importance"] = max(0.0, min(1.0, outcome.set_importance))
                elif outcome.importance_delta:
                    candidate["importance"] = max(
                        0.0, min(1.0, base_importance + outcome.importance_delta)
                    )
                if outcome.add_topics:
                    metadata["topics"] = list(
                        dict.fromkeys(
                            list(metadata.get("topics") or [])
                            + list(outcome.add_topics)
                        )
                    )[:5]
                if outcome.set_privacy is not None:
                    metadata["privacy_level"] = outcome.set_privacy
                if disposition == "discard":
                    return MemoryGateResult(
                        action="discard", reason_codes=tuple(reason_codes)
                    )
                if disposition == "mark_write":
                    metadata["gate_disposition"] = "mark_write"
                    metadata["gate_reason_codes"] = list(reason_codes)
                    metadata["quality_gate_action"] = "mark_write"
                    atoms: list = []
                    if not outcome.drop_atoms:
                        try:
                            atoms = self.memory_processor.classify_atoms_from_metadata(
                                metadata=metadata,
                                parent_importance=float(
                                    candidate.get("importance", 0.5)
                                ),
                                session_id=session_id,
                                persona_id=persona_id,
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            atoms = []
                    return MemoryGateResult(
                        action="mark_write",
                        reason_codes=tuple(reason_codes),
                        atoms=atoms,
                    )

        candidate_key = self._candidate_key(
            candidate,
            session_id=session_id,
            source_window=source_window,
        )
        # quarantine 表未单独存 group_id：并入 source_window 供批准路径解析 profile。
        staged_window = (
            {**source_window, "group_id": group_id} if group_id else dict(source_window)
        )
        stored = await self.store.stage_candidate(
            candidate_key=candidate_key,
            reason_codes=reason_codes,
            content=str(candidate.get("content") or ""),
            metadata=metadata,
            importance=float(candidate.get("importance", 0.5)),
            session_id=session_id,
            persona_id=persona_id,
            source_window=staged_window,
            is_group_chat=is_group_chat,
        )
        return MemoryGateResult(
            action="quarantined",
            candidate_id=stored["candidate_id"],
            reason_codes=tuple(stored["reason_codes"]),
        )

    async def approve(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        actor_id: str | None,
        content: str | None = None,
    ) -> dict[str, Any]:
        """保存可选修正并生成唯一 canonical；写入结果未知时保留 approving。"""

        current = await self.store.get_candidate(candidate_id)
        if current is None:
            raise KeyError("quarantine_candidate_not_found")
        if current["status"] == "approved":
            canonical_memory_id = current.get("canonical_memory_id")
            if canonical_memory_id is None:
                raise ValueError("quarantine_canonical_not_found")
            getter = getattr(self.memory_engine, "get_memory", None)
            if getter is None:
                return current
            canonical_result = getter(int(canonical_memory_id))
            canonical = (
                await canonical_result
                if inspect.isawaitable(canonical_result)
                else canonical_result
            )
            if canonical is None:
                raise ValueError("quarantine_canonical_not_found")
            return current

        corrected_metadata = None
        corrected_content = None
        if content is not None:
            corrected_content = str(content).strip()
            if not corrected_content:
                raise ValueError("quarantine_content_required")
            if len(corrected_content) > 2000:
                raise ValueError("quarantine_content_too_long")
            corrected_metadata = dict(current["metadata"])
            corrected_metadata["key_facts"] = [corrected_content]
            corrected_metadata["summary_quality"] = "reviewed"

        approval_token = secrets.token_urlsafe(32)
        approval_token_hash = hashlib.sha256(approval_token.encode("utf-8")).hexdigest()
        claimed = await self.store.claim_approval(
            candidate_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
            approval_token=approval_token,
            content=corrected_content,
            metadata=corrected_metadata,
        )
        try:
            source_window = claimed["source_window"]
            messages = await self.conversation_manager.get_messages_range(
                session_id=claimed["session_id"],
                start_index=int(source_window.get("start_index", 0)),
                end_index=int(source_window.get("end_index", 0)),
            )
            metadata = dict(claimed["metadata"])
            validation = self.grounding_validator.revalidate_stored_evidence(
                {
                    "content": claimed["content"],
                    "key_facts": metadata.get("key_facts", []),
                    "participants": metadata.get("participants", []),
                },
                messages,
                list(metadata.get("source_evidence") or []),
                is_group_chat=bool(claimed["is_group_chat"]),
            )
        except asyncio.CancelledError:
            await self.store.block_approval(
                candidate_id,
                expected_revision=claimed["revision"],
                actor_id=actor_id,
                reason_code="approval_cancelled_before_write",
            )
            raise
        except Exception:
            await self.store.block_approval(
                candidate_id,
                expected_revision=claimed["revision"],
                actor_id=actor_id,
                reason_code="grounding_revalidation_failed",
            )
            raise
        if validation.requires_judge:
            # 重验证需要 Judge 时走与自动路径同一解析；不可用仍 fail-closed 阻塞。
            profile = (
                self.gate_runtime.resolve_profile(
                    "group" if claimed["is_group_chat"] else "private",
                    source_window.get("group_id"),
                    claimed.get("persona_id"),
                )
                if self.gate_runtime is not None
                else None
            )
            if profile is None:
                validation = validation.with_unavailable_judge()
            else:
                try:
                    validation = await self.memory_processor.resolve_grounding_judge(
                        validation,
                        is_group_chat=bool(claimed["is_group_chat"]),
                        profile=profile,
                        topics=tuple(metadata.get("topics") or ()),
                        importance=float(claimed["importance"]),
                    )
                except asyncio.CancelledError:
                    # Judge 取消时先恢复为 blocked，再向上传播，不得遗留 approving。
                    await self.store.block_approval(
                        candidate_id,
                        expected_revision=claimed["revision"],
                        actor_id=actor_id,
                        reason_code="approval_cancelled_before_write",
                    )
                    raise
        if not validation.allowed:
            return await self.store.block_approval(
                candidate_id,
                expected_revision=claimed["revision"],
                actor_id=actor_id,
                reason_code=(
                    validation.reason_codes[0]
                    if validation.reason_codes
                    else "grounding_revalidation_failed"
                ),
            )

        metadata["grounding_status"] = "grounded"
        metadata["grounding_reason_codes"] = list(validation.reason_codes)
        metadata["source_evidence"] = validation.evidence
        metadata["quality_gate_action"] = "approved"
        metadata["quarantine_approved"] = True
        metadata["_quarantine_candidate_id"] = candidate_id
        metadata["_quarantine_approval_token_hash"] = approval_token_hash
        metadata["_quarantine_approval_status"] = "committed"
        try:
            atoms = self.memory_processor.classify_atoms_from_metadata(
                metadata=metadata,
                parent_importance=claimed["importance"],
                session_id=claimed["session_id"],
                persona_id=claimed["persona_id"],
            )
        except asyncio.CancelledError:
            await self.store.block_approval(
                candidate_id,
                expected_revision=claimed["revision"],
                actor_id=actor_id,
                reason_code="approval_cancelled_before_write",
            )
            raise
        except Exception:
            await self.store.block_approval(
                candidate_id,
                expected_revision=claimed["revision"],
                actor_id=actor_id,
                reason_code="atom_rebuild_failed",
            )
            raise
        try:
            canonical_memory_id = await self.memory_engine.add_memory(
                content=claimed["content"],
                session_id=claimed["session_id"],
                persona_id=claimed["persona_id"],
                importance=claimed["importance"],
                metadata=metadata,
                atoms=atoms,
            )
        except asyncio.CancelledError:
            # approving 表示 canonical 提交结果未知，禁止自动重试造成重复写入。
            raise
        except Exception as exc:
            # add_memory 可能在 canonical 提交后的日志或派生阶段失败，不能据此允许重试。
            raise QuarantineApprovalPendingError(
                candidate_id,
                claimed["revision"],
                approval_token,
            ) from exc
        try:
            return await self.store.finalize_approval(
                candidate_id,
                expected_revision=claimed["revision"],
                canonical_memory_id=int(canonical_memory_id),
                actor_id=actor_id,
                approval_token=approval_token,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise QuarantineApprovalPendingError(
                candidate_id,
                claimed["revision"],
                approval_token,
            ) from exc

    async def repair_approval(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        canonical_memory_id: int,
        approval_token: str | None = None,
        actor_id: str | None,
    ) -> dict[str, Any]:
        """核对 canonical 关联、digest、状态和正文后收口候选。

        ``approval_token`` 是旧版/同进程 repair 的兼容输入；跨重启场景
        可以省略它，改用 quarantine 行与 canonical metadata 中共同保存的
        candidate correlation 和 digest。
        """

        current = await self.store.get_candidate(candidate_id)
        if current is None:
            raise KeyError("quarantine_candidate_not_found")
        if current["status"] != "approving":
            raise ValueError("quarantine_status_conflict")
        if int(current["revision"]) != int(expected_revision):
            raise ValueError("quarantine_revision_conflict")
        canonical = await self.memory_engine.get_memory(int(canonical_memory_id))
        if canonical is None:
            raise ValueError("quarantine_canonical_not_found")
        metadata = canonical.get("metadata") if isinstance(canonical, dict) else None
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (TypeError, json.JSONDecodeError):
                metadata = None
        if not isinstance(metadata, dict):
            raise ValueError("quarantine_canonical_status_invalid")
        canonical_token_hash = metadata.get("_quarantine_approval_token_hash")
        stored_token_hash = current.get("approval_token_hash")
        if (
            not isinstance(canonical_token_hash, str)
            or not isinstance(stored_token_hash, str)
            or not secrets.compare_digest(canonical_token_hash, stored_token_hash)
        ):
            raise ValueError("quarantine_approval_token_invalid")
        canonical_candidate_id = metadata.get("_quarantine_candidate_id")
        if (
            canonical_candidate_id is not None
            and canonical_candidate_id != candidate_id
        ):
            raise ValueError("quarantine_candidate_correlation_invalid")
        if approval_token is None:
            if canonical_candidate_id != candidate_id:
                raise ValueError("quarantine_candidate_correlation_invalid")
        else:
            token = approval_token.strip()
            if not token:
                raise ValueError("quarantine_approval_token_required")
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if not secrets.compare_digest(canonical_token_hash, token_hash):
                raise ValueError("quarantine_approval_token_invalid")
        if metadata.get("_quarantine_approval_status") != "committed":
            raise ValueError("quarantine_canonical_status_invalid")
        canonical_content = str(canonical.get("text") or canonical.get("content") or "")
        if canonical_content != str(current["content"]):
            raise ValueError("quarantine_canonical_mismatch")
        if approval_token is None:
            return await self.store.finalize_repaired_approval_by_digest(
                candidate_id,
                expected_revision=expected_revision,
                canonical_memory_id=int(canonical_memory_id),
                actor_id=actor_id,
                approval_token_hash=stored_token_hash,
            )
        return await self.store.finalize_repaired_approval(
            candidate_id,
            expected_revision=expected_revision,
            canonical_memory_id=int(canonical_memory_id),
            actor_id=actor_id,
            approval_token=approval_token,
        )

    async def repair_blocked(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        actor_id: str | None,
        confirm_canonical_absent: bool,
    ) -> dict[str, Any]:
        """在管理员明确确认未写入时安全退回 blocked。"""

        if confirm_canonical_absent is not True:
            raise ValueError("quarantine_canonical_absence_confirmation_required")
        current = await self.store.get_candidate(candidate_id)
        if current is None:
            raise KeyError("quarantine_candidate_not_found")
        if current["status"] != "approving":
            raise ValueError("quarantine_status_conflict")
        if current.get("canonical_memory_id") is not None:
            raise ValueError("quarantine_canonical_presence_conflict")
        return await self.store.block_approval(
            candidate_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason_code="canonical_write_not_found_confirmed",
        )

    async def reject(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        actor_id: str | None,
    ) -> dict[str, Any]:
        """拒绝候选并保留 ConversationStore 中的原始消息证据。"""

        return await self.store.reject(
            candidate_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
        )

    @staticmethod
    def _reason_codes(metadata: dict[str, Any]) -> list[str]:
        """从处理器 metadata 生成稳定、去重的隔离原因码。"""

        reasons: list[str] = []
        if str(metadata.get("summary_quality") or "").casefold() == "low":
            reasons.append("summary_quality_low")
        if str(metadata.get("grounding_status") or "") != "grounded":
            raw_reasons = metadata.get("grounding_reason_codes") or []
            reasons.extend(
                str(reason)
                for reason in raw_reasons
                if isinstance(reason, str) and reason.strip()
            )
            if not raw_reasons:
                reasons.append("grounding_not_verified")
        return list(dict.fromkeys(reasons))

    @staticmethod
    def _candidate_key(
        candidate: dict[str, Any],
        *,
        session_id: str,
        source_window: dict[str, Any],
    ) -> str:
        """优先复用写入幂等键，否则从窗口边界与正文生成稳定摘要。"""

        metadata = candidate.get("metadata")
        if isinstance(metadata, dict):
            explicit = str(metadata.get("idempotency_key") or "").strip()
            if explicit:
                return explicit
        payload = {
            "session_id": str(session_id),
            "start_index": source_window.get("start_index"),
            "end_index": source_window.get("end_index"),
            "content": str(candidate.get("content") or ""),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"quality:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "MemoryGateResult",
    "MemoryQualityGate",
    "QuarantineApprovalPendingError",
]
