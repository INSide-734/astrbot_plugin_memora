"""编排记忆候选的 pre-canonical 质量门与人工处置。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from ....shared.summary_source import source_window_digest
from ...recall.processors.memory_grounding import MemoryGroundingValidator
from ..infrastructure.quarantine_store import MemoryQuarantineStore
from .gate_rule_engine import CandidateView, evaluate_disposition, evaluate_rules
from .gate_runtime import GateRuntime, gate_snapshot_from_json
from .memory_quality_gate_actions import MemoryQualityGateActionsMixin


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


class MemoryQualityGate(MemoryQualityGateActionsMixin):
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

    def _conversation_store(self) -> Any:
        """返回负责消息来源与 epoch 的持久 Store。"""
        store = getattr(self.conversation_manager, "store", None)
        return store if store is not None else self.conversation_manager

    @asynccontextmanager
    async def _source_guard(self, session_id: str) -> AsyncGenerator[None, None]:
        """在来源锁内串行化批准流程与 reset/trim。"""
        store = self._conversation_store()
        lock_factory = getattr(store, "_summary_source_lock_for", None)
        if not callable(lock_factory):
            raise RuntimeError("summary_source_fence_unavailable")
        lock = lock_factory(session_id)
        if not isinstance(lock, asyncio.Lock):
            raise RuntimeError("summary_source_fence_unavailable")
        async with lock:
            yield

    @staticmethod
    def _has_stable_source(candidate: dict[str, Any]) -> bool:
        """判断候选是否携带新总结链的序号、epoch 或摘要证据。"""
        source_window = candidate.get("source_window")
        return isinstance(source_window, dict) and any(
            key in source_window
            for key in ("session_epoch", "source_digest", "start_seq", "end_seq")
        )

    async def _read_approval_source(
        self, claimed: dict[str, Any]
    ) -> tuple[dict[str, Any], list[Any]]:
        """读取批准候选来源；现代总结候选必须验证序号、epoch 与摘要。"""
        source_window = claimed.get("source_window")
        if not isinstance(source_window, dict):
            raise ValueError("source_window_invalid")
        session_id = claimed.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("source_window_invalid")
        if not self._has_stable_source(claimed):
            start_index = source_window.get("start_index")
            end_index = source_window.get("end_index")
            if (
                isinstance(start_index, bool)
                or not isinstance(start_index, int)
                or isinstance(end_index, bool)
                or not isinstance(end_index, int)
                or end_index <= start_index
            ):
                raise ValueError("source_window_invalid")
            expected_count = source_window.get("message_count")
            if expected_count is None:
                expected_count = end_index - start_index
            if (
                isinstance(expected_count, bool)
                or not isinstance(expected_count, int)
                or expected_count != end_index - start_index
            ):
                raise ValueError("source_window_invalid")
            manager_reader = getattr(
                self.conversation_manager, "get_messages_range", None
            )
            if callable(manager_reader):
                messages = manager_reader(
                    session_id,
                    start_index=start_index,
                    end_index=end_index,
                )
            else:
                reader = getattr(self._conversation_store(), "get_messages_range", None)
                if not callable(reader):
                    raise RuntimeError("stable_message_range_unavailable")
                messages = reader(
                    session_id,
                    offset=start_index,
                    limit=expected_count,
                )
            if inspect.isawaitable(messages):
                messages = await messages
            if (
                not isinstance(messages, (list, tuple))
                or len(messages) != expected_count
            ):
                raise ValueError("source_window_invalid")
            return source_window, list(messages)

        if source_window.get("session_id") not in (None, session_id):
            raise ValueError("source_window_invalid")
        start_seq = source_window.get("start_seq")
        end_seq = source_window.get("end_seq")
        if source_window.get(
            "start_index"
        ) is not None and start_seq != source_window.get("start_index"):
            raise ValueError("source_window_invalid")
        if source_window.get("end_index") is not None and end_seq != source_window.get(
            "end_index"
        ):
            raise ValueError("source_window_invalid")
        expected_count = source_window.get("message_count")
        if source_window.get("expected_count") is not None:
            if expected_count is not None and expected_count != source_window.get(
                "expected_count"
            ):
                raise ValueError("source_window_invalid")
            expected_count = source_window.get("expected_count")
        epoch = source_window.get("session_epoch")
        digest = source_window.get("source_digest")
        if (
            isinstance(start_seq, bool)
            or not isinstance(start_seq, int)
            or isinstance(end_seq, bool)
            or not isinstance(end_seq, int)
            or end_seq <= start_seq
            or isinstance(expected_count, bool)
            or not isinstance(expected_count, int)
            or expected_count != end_seq - start_seq
            or isinstance(epoch, bool)
            or not isinstance(epoch, int)
            or epoch <= 0
            or not isinstance(digest, str)
            or not digest.strip()
        ):
            raise ValueError("source_window_invalid")

        store = self._conversation_store()
        epoch_reader = getattr(store, "get_summary_epoch", None)
        if not callable(epoch_reader):
            raise RuntimeError("summary_epoch_unavailable")
        current_epoch = epoch_reader(session_id)
        if inspect.isawaitable(current_epoch):
            current_epoch = await current_epoch
        if isinstance(current_epoch, (tuple, list)):
            current_epoch = current_epoch[0] if current_epoch else None
        if (
            isinstance(current_epoch, bool)
            or not isinstance(current_epoch, int)
            or current_epoch != epoch
        ):
            raise RuntimeError("summary_epoch_fenced")

        reader = getattr(self.conversation_manager, "get_messages_seq_range", None)
        if not callable(reader):
            reader = getattr(store, "get_messages_seq_range", None)
        if not callable(reader):
            raise RuntimeError("stable_message_range_unavailable")
        messages = reader(
            session_id,
            start_seq,
            end_seq,
            expected_count=expected_count,
        )
        if inspect.isawaitable(messages):
            messages = await messages
        if not isinstance(messages, (list, tuple)):
            raise ValueError("source_window_invalid")
        messages = list(messages)
        try:
            actual_digest = source_window_digest(
                tuple(messages), tuple(range(start_seq + 1, end_seq + 1))
            )
        except (TypeError, ValueError) as error:
            raise ValueError("source_window_invalid") from error
        if actual_digest != digest.strip():
            raise ValueError("source_digest_mismatch")
        return source_window, messages

    @asynccontextmanager
    async def _repair_source_guard(
        self, candidate: dict[str, Any]
    ) -> AsyncGenerator[bool, None]:
        """为现代候选持有来源锁；旧候选保持既有修复入口。"""
        modern = self._has_stable_source(candidate)
        if not modern:
            yield False
            return
        session_id = candidate.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("source_window_invalid")
        async with self._source_guard(session_id):
            await self._read_approval_source(candidate)
            yield True

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
        scope_id: str | None = None,
        gate_snapshot_json: str | None = None,
    ) -> MemoryGateResult:
        """允许可信候选继续写入，其余候选按固定或当前门禁配置路由。"""

        metadata = candidate.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            candidate["metadata"] = metadata
        reason_codes = self._reason_codes(metadata)
        if not reason_codes:
            return MemoryGateResult(action="allow")

        snapshot = (
            gate_snapshot_from_json(gate_snapshot_json)
            if gate_snapshot_json is not None
            else self.gate_runtime.snapshot()
            if self.gate_runtime is not None
            else None
        )
        if gate_snapshot_json is not None and snapshot is None:
            raise ValueError("门禁快照无法恢复")
        if snapshot is not None and snapshot.enabled:
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
            disposition = evaluate_disposition(tuple(reason_codes), outcome, profile)
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
                        list(metadata.get("topics") or []) + list(outcome.add_topics)
                    )
                )[:5]
            if outcome.set_privacy is not None:
                metadata["privacy_level"] = outcome.set_privacy
            if disposition == "allow":
                return MemoryGateResult(
                    action="allow", reason_codes=tuple(reason_codes)
                )
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
                            parent_importance=float(candidate.get("importance", 0.5)),
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
        staged_window = {
            **source_window,
            "group_id": group_id,
            "scope_id": scope_id,
        }
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
            claimed_session_id = claimed.get("session_id")
            if not isinstance(claimed_session_id, str) or not claimed_session_id:
                raise ValueError("source_window_invalid")

            async def _validate_source() -> tuple[
                dict[str, Any], list[Any], dict[str, Any], Any
            ]:
                """读取来源并执行一次 grounding 重验证。"""
                validated_window, validated_messages = await self._read_approval_source(
                    claimed
                )
                validated_metadata = dict(claimed["metadata"])
                validated = self.grounding_validator.revalidate_stored_evidence(
                    {
                        "content": claimed["content"],
                        "key_facts": validated_metadata.get("key_facts", []),
                        "participants": validated_metadata.get("participants", []),
                    },
                    validated_messages,
                    list(validated_metadata.get("source_evidence") or []),
                    is_group_chat=bool(claimed["is_group_chat"]),
                )
                return (
                    validated_window,
                    validated_messages,
                    validated_metadata,
                    validated,
                )

            if self._has_stable_source(claimed):
                async with self._source_guard(claimed_session_id):
                    (
                        source_window,
                        messages,
                        metadata,
                        validation,
                    ) = await _validate_source()
            else:
                source_window, messages, metadata, validation = await _validate_source()
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
                except Exception:
                    await self.store.block_approval(
                        candidate_id,
                        expected_revision=claimed["revision"],
                        actor_id=actor_id,
                        reason_code="grounding_judge_failed",
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
        canonical_started = False
        try:
            async with self._repair_source_guard(claimed) as modern_source:
                # Judge/Atom 阶段可能耗时；现代候选写入前再次核对来源。
                if modern_source:
                    await self._read_approval_source(claimed)
                canonical_started = True
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
                    # canonical 提交结果未知，保持 approving 供显式 repair。
                    raise
                except Exception as exc:
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
        except asyncio.CancelledError:
            if not canonical_started:
                await self.store.block_approval(
                    candidate_id,
                    expected_revision=claimed["revision"],
                    actor_id=actor_id,
                    reason_code="approval_cancelled_before_write",
                )
            raise
        except QuarantineApprovalPendingError:
            raise
        except Exception:
            await self.store.block_approval(
                candidate_id,
                expected_revision=claimed["revision"],
                actor_id=actor_id,
                reason_code="source_fence_failed",
            )
            raise

    async def repair_approval(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        canonical_memory_id: int,
        approval_token: str | None = None,
        actor_id: str | None,
    ) -> dict[str, Any]:
        """核对 canonical 关联、来源证据和正文后收口候选。

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
        async with self._repair_source_guard(current) as modern_source:
            canonical = await self.memory_engine.get_memory(int(canonical_memory_id))
            if canonical is None:
                raise ValueError("quarantine_canonical_not_found")
            metadata = (
                canonical.get("metadata") if isinstance(canonical, dict) else None
            )
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (TypeError, json.JSONDecodeError):
                    metadata = None
            if not isinstance(metadata, dict):
                raise ValueError("quarantine_canonical_status_invalid")
            if modern_source:
                source_window = current["source_window"]
                for field, source_field in (
                    ("source_epoch", "session_epoch"),
                    ("source_digest", "source_digest"),
                    ("source_fence_generation", "worker_generation"),
                    ("source_fence", "source_fence"),
                ):
                    expected = source_window.get(source_field)
                    if (
                        not isinstance(expected, (str, int))
                        or isinstance(expected, bool)
                        or metadata.get(field) != expected
                    ):
                        raise ValueError("quarantine_source_correlation_invalid")
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
            canonical_content = str(
                canonical.get("text") or canonical.get("content") or ""
            )
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
