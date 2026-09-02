"""单个持久化总结 claim 的来源校验与候选收口。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from ....shared.summary_source import source_window_digest
from ...quality.application.gate_runtime import gate_snapshot_from_json
from ...recall.processors.json_parser import SummaryParseError
from ..domain.storage_outcomes import ReflectionStoreOutcome, ReflectionStoreResult
from ..domain.summary_models import (
    CandidateDisposition,
    CandidateIntent,
    CandidateLedgerStatus,
    ClaimedJob,
    SourceWindow,
    SummaryReasonCode,
    WindowOutcome,
)
from .candidate_writer import (
    build_reflection_idempotency_key,
    store_reflection_candidates,
)
from .summary_worker_reconcile import SummaryWorkerReconcileMixin
from .summary_worker_support import (
    FixedQualityGate as _FixedQualityGate,
)
from .summary_worker_support import (
    SummaryWorkerFailure,
)
from .summary_worker_support import (
    canonical_hook_already_owned as _canonical_hook_already_owned,
)
from .summary_worker_support import (
    fixed_quality_key as _fixed_quality_key,
)
from .summary_worker_validation import SummaryWorkerValidationMixin

if TYPE_CHECKING:
    from ....shared.contracts import ReflectionWritePort
    from ...quality.application.memory_quality_gate import MemoryQualityGate
    from ...recall.processors.memory_processor import MemoryProcessor
    from ..domain.summary_ports import SummaryJobStorePort
    from .topic_batch_preparer import TopicBatchPreparer


_RESULT_DISPOSITIONS = {
    ReflectionStoreOutcome.CANONICAL: CandidateDisposition.CANONICAL,
    ReflectionStoreOutcome.QUARANTINED: CandidateDisposition.QUARANTINED,
    ReflectionStoreOutcome.DISCARDED: CandidateDisposition.DISCARD,
    ReflectionStoreOutcome.MARK_WRITE: CandidateDisposition.MARK_WRITE,
    ReflectionStoreOutcome.SKIPPED_IDEMPOTENT: CandidateDisposition.SKIPPED_IDEMPOTENT,
    ReflectionStoreOutcome.FAILED: CandidateDisposition.FAILED,
}
_STORE_SLOT_PLACEHOLDER = "store-owned"


def _claim_fence(claim: ClaimedJob) -> str:
    """根据 claim 的 epoch、generation 和 token 生成不透明来源 fence。"""
    return hashlib.sha256(
        f"{claim.session_epoch}:{claim.worker_generation}:{claim.claim_token}".encode()
    ).hexdigest()


class SummaryWorker(SummaryWorkerReconcileMixin, SummaryWorkerValidationMixin):
    """执行单个 claim，并只返回 Store 可原子收口的 WindowOutcome。"""

    def __init__(
        self,
        job_store: SummaryJobStorePort,
        processor: MemoryProcessor,
        quality_gate: MemoryQualityGate | None,
        memory_engine: ReflectionWritePort,
        batch_preparer: TopicBatchPreparer,
    ) -> None:
        """绑定 worker 所需的窄 Store port 与现有候选处理流水线。"""

        self._job_store = job_store
        self._processor = processor
        self._quality_gate = quality_gate
        self._memory_engine = memory_engine
        self._batch_preparer = batch_preparer

    async def _claim_is_active(self, claim: ClaimedJob) -> bool:
        """在外部副作用前确认 claim、epoch 和 token 仍有效。"""
        checker = getattr(self._job_store, "claim_is_active", None)
        if not callable(checker):
            checker = getattr(self._job_store, "_claim_matches", None)
        if not callable(checker):
            return False
        result = checker(claim)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    async def execute(self, claim: ClaimedJob) -> WindowOutcome:
        """校验固定来源、抽取候选、持久化 intent 并生成窗口结果。"""

        source = await self._read_source(claim)
        snapshot_payload = self._snapshot_payload(claim)
        is_group_chat = self._is_group_chat(claim)
        messages = await self._prepare_base_batch(source, is_group_chat)
        memories = await self._process_messages(
            claim,
            messages,
            is_group_chat,
            snapshot_payload,
        )
        if not memories:
            return WindowOutcome(
                can_advance=True,
                reason_code=SummaryReasonCode.NO_FACTS,
            )
        candidates, intents = self._prepare_candidates(claim, memories)
        try:
            begun = await self._job_store.begin_candidate_intents(claim, intents)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise SummaryWorkerFailure(
                "candidate_intent",
                SummaryReasonCode.STORE_UNAVAILABLE,
                retryable=True,
                exception_type=error.__class__.__name__,
            ) from error
        if not begun:
            return self._unknown_outcome(
                intents,
                stage="candidate_intent",
                reason_code=SummaryReasonCode.LEDGER_UNRESOLVED,
            )
        completed_canonical_ids = await self._find_completed_keys(candidates)

        owner_reconciled = await self._reconcile_discovered_owners(
            claim, candidates, intents, completed_canonical_ids
        )
        if not owner_reconciled:
            return self._unknown_outcome(
                intents,
                stage="candidate_reconcile",
                reason_code=SummaryReasonCode.LEDGER_UNRESOLVED,
            )
        if not await self._begin_candidate_writes(claim, intents):
            return self._unknown_outcome(
                intents,
                stage="candidate_intent",
                reason_code=SummaryReasonCode.LEDGER_UNRESOLVED,
            )
        fixed_quality_gate, gate_reason = await self._route_quality(
            claim,
            candidates,
            completed_canonical_ids,
            snapshot_payload,
        )
        if gate_reason is not None:
            return self._unknown_outcome(
                intents,
                stage="quality_gate",
                reason_code=gate_reason,
            )
        try:
            results = await store_reflection_candidates(
                candidates,
                completed_idempotency_keys=completed_canonical_ids,
                session_id=claim.session_id,
                persona_id=claim.persona_id,
                start_index=claim.start_seq,
                end_index=claim.end_seq,
                is_group_chat=self._is_group_chat(claim),
                group_id=claim.group_id,
                scope_id=claim.scope_id,
                session_epoch=claim.session_epoch,
                worker_generation=claim.worker_generation,
                source_digest=claim.source_digest,
                claim_fence=_claim_fence(claim),
                job_id=claim.job_id,
                claim_token=claim.claim_token,
                gate_snapshot_json=claim.gate_snapshot_json,
                before_side_effect=lambda: self._claim_is_active(claim),
                run_claim_side_effect=lambda operation: self.run_claim_side_effect(
                    claim, operation
                ),
                memory_engine=self._memory_engine,
                memory_quality_gate=fixed_quality_gate,
                schedule_evolution_after_write=_canonical_hook_already_owned,
            )
        except asyncio.CancelledError:
            raise
        except SummaryWorkerFailure:
            raise
        except Exception:
            return self._unknown_outcome(
                intents,
                stage="candidate_write",
                reason_code=SummaryReasonCode.LEDGER_UNRESOLVED,
            )
        expected_snapshots = tuple(map(_fixed_quality_key, candidates))
        return self._build_outcome(
            intents,
            results,
            expected_idempotency_keys=expected_snapshots,
        )

    async def _begin_candidate_writes(
        self, claim: ClaimedJob, intents: Sequence[CandidateIntent]
    ) -> bool:
        """在任何质量门或 canonical 副作用前持久化所有候选 writing 状态。"""

        begin_write = getattr(self._job_store, "begin_candidate_write", None)
        if not callable(begin_write):
            return False
        for intent in intents:
            try:
                begun = begin_write(claim, intent)
                if inspect.isawaitable(begun):
                    begun = await begun
            except asyncio.CancelledError:
                raise
            except Exception:
                return False
            if begun is not True:
                return False
        return True

    async def _route_quality(
        self,
        claim: ClaimedJob,
        candidates: Sequence[dict[str, Any]],
        completed_keys: Mapping[str, int],
        snapshot_payload: Mapping[str, object],
    ) -> tuple[object | None, SummaryReasonCode | None]:
        """用同一固化快照预求值质量门，并拒绝候选快照变化。"""
        gate = self._quality_gate
        if gate is None:
            return None, None
        snapshot_kwargs = self._fixed_snapshot_kwargs(
            gate.route_candidate,
            claim,
            snapshot_payload,
            required=True,
        )
        source_window = {
            "session_id": claim.session_id,
            "start_index": claim.start_seq,
            "end_index": claim.end_seq,
            "start_seq": claim.start_seq,
            "end_seq": claim.end_seq,
            "message_count": claim.expected_count,
            "scope_id": claim.scope_id,
            "session_epoch": claim.session_epoch,
            "source_digest": claim.source_digest,
            "worker_generation": claim.worker_generation,
            "source_fence": _claim_fence(claim),
        }
        results: dict[tuple[str, str], object] = {}
        for candidate in candidates:
            try:
                snapshot_key = _fixed_quality_key(candidate)
            except (TypeError, ValueError):
                return None, SummaryReasonCode.LEDGER_UNRESOLVED
            if snapshot_key[0] in completed_keys:
                continue
            if not await self._claim_is_active(claim):
                raise SummaryWorkerFailure(
                    "claim_fence",
                    SummaryReasonCode.CLAIM_LOST,
                    retryable=False,
                )
            try:

                async def _route_candidate() -> object:
                    """在同一 claim/source fence 内执行质量门和隔离写入。"""
                    return await gate.route_candidate(
                        candidate,
                        session_id=claim.session_id,
                        persona_id=claim.persona_id,
                        source_window=source_window,
                        is_group_chat=self._is_group_chat(claim),
                        group_id=claim.group_id,
                        scope_id=claim.scope_id,
                        chat_type=claim.chat_type,
                        **snapshot_kwargs,
                    )

                result = await self.run_claim_side_effect(claim, _route_candidate)
            except asyncio.CancelledError:
                raise
            except Exception:
                return None, SummaryReasonCode.LEDGER_UNRESOLVED
            if getattr(result, "action", None) not in {
                "allow",
                "quarantined",
                "discard",
                "mark_write",
            }:
                return None, SummaryReasonCode.INVALID_ACTION
            try:
                if _fixed_quality_key(candidate) != snapshot_key:
                    return None, SummaryReasonCode.LEDGER_UNRESOLVED
            except (TypeError, ValueError):
                return None, SummaryReasonCode.LEDGER_UNRESOLVED
            results[snapshot_key] = result
        return _FixedQualityGate(results), None

    async def _read_source(self, claim: ClaimedJob) -> SourceWindow:
        """读取并再次核对 claim 拥有的精确来源范围与摘要。"""
        try:
            source = await self._job_store.read_claimed_window(claim)
        except asyncio.CancelledError:
            raise
        except (TypeError, ValueError) as error:
            raise SummaryWorkerFailure(
                "source_read",
                SummaryReasonCode.SOURCE_INCOMPLETE,
                retryable=False,
                exception_type=error.__class__.__name__,
            ) from error
        except Exception as error:
            raise SummaryWorkerFailure(
                "source_read",
                SummaryReasonCode.STORE_UNAVAILABLE,
                retryable=True,
                exception_type=error.__class__.__name__,
            ) from error
        if not isinstance(source, SourceWindow):
            raise SummaryWorkerFailure(
                "source_read",
                SummaryReasonCode.SOURCE_INCOMPLETE,
                retryable=False,
                exception_type="TypeError",
            )
        if (
            source.session_id != claim.session_id
            or source.start_seq != claim.start_seq
            or source.end_seq != claim.end_seq
            or source.expected_count != claim.expected_count
        ):
            raise SummaryWorkerFailure(
                "source_validate",
                SummaryReasonCode.SOURCE_INCOMPLETE,
                retryable=False,
            )
        digest = source_window_digest(source.messages, source.message_seqs)
        if source.source_digest != claim.source_digest or digest != claim.source_digest:
            raise SummaryWorkerFailure(
                "source_validate",
                SummaryReasonCode.SOURCE_DIGEST_MISMATCH,
                retryable=False,
            )
        return source

    @staticmethod
    def _snapshot_payload(claim: ClaimedJob) -> Mapping[str, object]:
        """解析并核对 job 固化的可恢复 GateSnapshot。"""
        try:
            payload = json.loads(claim.gate_snapshot_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise SummaryWorkerFailure(
                "gate_snapshot",
                SummaryReasonCode.BLOCKED,
                retryable=False,
                exception_type=error.__class__.__name__,
            ) from error
        if not isinstance(payload, dict) or not payload:
            raise SummaryWorkerFailure(
                "gate_snapshot",
                SummaryReasonCode.BLOCKED,
                retryable=False,
                exception_type="MissingSnapshot",
            )
        if not {
            "enabled",
            "default_profile",
            "profiles",
            "bindings",
            "revision",
        } <= set(payload):
            raise SummaryWorkerFailure(
                "gate_snapshot",
                SummaryReasonCode.BLOCKED,
                retryable=False,
                exception_type="IncompleteSnapshot",
            )
        revision = payload.get("revision")
        if (
            not isinstance(revision, str)
            or not revision.strip()
            or revision != claim.gate_revision
        ):
            raise SummaryWorkerFailure(
                "gate_snapshot",
                SummaryReasonCode.BLOCKED,
                retryable=False,
                exception_type="SnapshotRevisionMismatch",
            )
        snapshot = gate_snapshot_from_json(claim.gate_snapshot_json)
        if snapshot is None or snapshot.revision != claim.gate_revision:
            raise SummaryWorkerFailure(
                "gate_snapshot",
                SummaryReasonCode.BLOCKED,
                retryable=False,
                exception_type="SnapshotUnrecoverable",
            )
        return payload

    async def _prepare_base_batch(
        self,
        source: SourceWindow,
        is_group_chat: bool,
    ) -> list[Any]:
        """调用现有 batch preparer，并合并为不继承请求预算的唯一基础批次。"""

        try:
            batches = await self._batch_preparer.prepare_batches(
                list(source.messages),
                is_group_chat,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise SummaryWorkerFailure(
                "batch_prepare",
                SummaryReasonCode.UNKNOWN,
                retryable=True,
                exception_type=error.__class__.__name__,
            ) from error
        if not isinstance(batches, Sequence) or not batches:
            raise SummaryWorkerFailure(
                "batch_prepare",
                SummaryReasonCode.SOURCE_INCOMPLETE,
                retryable=False,
            )
        flattened: list[Any] = []
        for batch in batches:
            if not isinstance(batch, Sequence):
                raise SummaryWorkerFailure(
                    "batch_prepare",
                    SummaryReasonCode.SOURCE_INCOMPLETE,
                    retryable=False,
                )
            flattened.extend(batch)
        if tuple(flattened) != source.messages:
            raise SummaryWorkerFailure(
                "batch_prepare",
                SummaryReasonCode.SOURCE_INCOMPLETE,
                retryable=False,
            )
        return flattened

    async def _process_messages(
        self,
        claim: ClaimedJob,
        messages: list[Any],
        is_group_chat: bool,
        snapshot_payload: Mapping[str, object],
    ) -> list[dict[str, Any]]:
        """使用固定身份和可恢复门禁快照执行唯一基础 Processor 调用。"""

        try:
            snapshot_kwargs = self._fixed_snapshot_kwargs(
                self._processor.process_conversation,
                claim,
                snapshot_payload,
                required=True,
            )
            result = await self._processor.process_conversation(
                messages=messages,
                is_group_chat=is_group_chat,
                persona_id=claim.persona_id,
                group_id=claim.group_id,
                llm_max_retries=1,
                strict_summary=True,
                **snapshot_kwargs,
            )
        except asyncio.CancelledError:
            raise
        except SummaryParseError as error:
            raise SummaryWorkerFailure(
                "memory_extract",
                SummaryReasonCode.SUMMARY_INVALID,
                retryable=True,
                exception_type=error.__class__.__name__,
            ) from error
        except SummaryWorkerFailure:
            raise
        except Exception as error:
            raise SummaryWorkerFailure(
                "memory_extract",
                SummaryReasonCode.UNKNOWN,
                retryable=True,
                exception_type=error.__class__.__name__,
            ) from error
        if not isinstance(result, list):
            raise SummaryWorkerFailure(
                "memory_extract",
                SummaryReasonCode.INVALID_ACTION,
                retryable=False,
                exception_type="TypeError",
            )
        return result

    @staticmethod
    def _prepare_candidates(
        claim: ClaimedJob,
        memories: Sequence[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], tuple[CandidateIntent, ...]]:
        """规范候选顺序、生成稳定幂等键与 Store-owned slot intent。"""

        candidates: list[dict[str, Any]] = []
        intents: list[CandidateIntent] = []
        for slot, raw_memory in enumerate(memories):
            if not isinstance(raw_memory, dict):
                raise SummaryWorkerFailure(
                    "candidate_prepare",
                    SummaryReasonCode.INVALID_SLOT,
                    retryable=False,
                    exception_type="TypeError",
                )
            candidate = dict(raw_memory)
            content = candidate.get("content")
            if not isinstance(content, str) or not content.strip():
                raise SummaryWorkerFailure(
                    "candidate_prepare",
                    SummaryReasonCode.INVALID_ACTION,
                    retryable=False,
                )
            metadata_value = candidate.get("metadata")
            if metadata_value is None:
                metadata: dict[str, Any] = {}
            elif isinstance(metadata_value, dict):
                metadata = dict(metadata_value)
            else:
                raise SummaryWorkerFailure(
                    "candidate_prepare",
                    SummaryReasonCode.INVALID_ACTION,
                    retryable=False,
                    exception_type="TypeError",
                )
            metadata["source_epoch"] = claim.session_epoch
            metadata["source_digest"] = claim.source_digest
            metadata["source_fence_generation"] = claim.worker_generation
            metadata["source_fence"] = _claim_fence(claim)
            raw_batch_index = metadata.get("batch_index", 0) or 0
            if isinstance(raw_batch_index, bool):
                raise SummaryWorkerFailure(
                    "candidate_prepare",
                    SummaryReasonCode.INVALID_SLOT,
                    retryable=False,
                    exception_type="TypeError",
                )
            try:
                batch_index = int(raw_batch_index)
            except (TypeError, ValueError) as error:
                raise SummaryWorkerFailure(
                    "candidate_prepare",
                    SummaryReasonCode.INVALID_SLOT,
                    retryable=False,
                    exception_type=error.__class__.__name__,
                ) from error
            if batch_index < 0:
                raise SummaryWorkerFailure(
                    "candidate_prepare",
                    SummaryReasonCode.INVALID_SLOT,
                    retryable=False,
                )
            idempotency_key = build_reflection_idempotency_key(
                session_id=claim.session_id,
                session_epoch=claim.session_epoch,
                start_index=claim.start_seq,
                end_index=claim.end_seq,
                batch_index=batch_index,
                memory_index=slot,
                content=content,
            )
            metadata["idempotency_key"] = idempotency_key
            candidate["metadata"] = metadata
            content_digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
            candidates.append(candidate)
            intents.append(
                CandidateIntent(
                    slot=slot,
                    content_digest=content_digest,
                    idempotency_key=idempotency_key,
                    slot_key=_STORE_SLOT_PLACEHOLDER,
                )
            )
        return candidates, tuple(intents)

    async def _find_completed_keys(
        self,
        candidates: Sequence[dict[str, Any]],
    ) -> dict[str, int]:
        """用 canonical 幂等索引识别崩溃后已写成功的候选及其 ID。"""

        finder = getattr(
            self._memory_engine,
            "find_memory_id_by_idempotency_key",
            None,
        )
        if not callable(finder):
            return {}
        finder_call = cast(Callable[[str], Awaitable[int | None]], finder)
        completed: dict[str, int] = {}
        try:
            for candidate in candidates:
                key = str(candidate["metadata"]["idempotency_key"])
                owner = await finder_call(key)
                if owner is None:
                    continue
                if isinstance(owner, bool) or not isinstance(owner, int) or owner <= 0:
                    raise ValueError("canonical_owner_invalid")
                completed[key] = owner
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise SummaryWorkerFailure(
                "candidate_reconcile",
                SummaryReasonCode.LEDGER_UNRESOLVED,
                retryable=False,
                exception_type=error.__class__.__name__,
            ) from error
        return completed

    def _build_outcome(
        self,
        intents: Sequence[CandidateIntent],
        results: Sequence[ReflectionStoreResult],
        *,
        expected_idempotency_keys: Sequence[tuple[str, str]] | None = None,
    ) -> WindowOutcome:
        """映射候选写入结果及 canonical ID，并将不一致收敛为 unknown。"""

        if len(results) != len(intents):
            return self._unknown_outcome(
                intents,
                stage="candidate_write",
                reason_code=SummaryReasonCode.INVALID_SLOT,
            )
        if expected_idempotency_keys is not None and len(
            expected_idempotency_keys
        ) != len(intents):
            return self._unknown_outcome(
                intents,
                stage="candidate_reconcile",
                reason_code=SummaryReasonCode.LEDGER_UNRESOLVED,
            )
        counts = {disposition: 0 for disposition in CandidateDisposition}
        final_intents: list[CandidateIntent] = []
        unknown_count = 0
        ledger_unresolved = False
        required_ids = {
            CandidateDisposition.CANONICAL,
            CandidateDisposition.MARK_WRITE,
            CandidateDisposition.SKIPPED_IDEMPOTENT,
        }
        for index, (intent, result) in enumerate(zip(intents, results, strict=True)):
            disposition = (
                _RESULT_DISPOSITIONS.get(result.outcome)
                if isinstance(result, ReflectionStoreResult)
                else None
            )
            expected_key = (
                expected_idempotency_keys[index][0]
                if expected_idempotency_keys is not None
                else None
            )
            expected_digest = (
                expected_idempotency_keys[index][1]
                if expected_idempotency_keys is not None
                else None
            )
            canonical_id = (
                result.canonical_id
                if isinstance(result, ReflectionStoreResult)
                else None
            )
            valid_id = (
                canonical_id is not None
                and not isinstance(canonical_id, bool)
                and isinstance(canonical_id, int)
                and canonical_id > 0
            )
            valid = disposition is not None
            mapping_inconsistent = False
            if expected_digest is not None and expected_digest != intent.content_digest:
                valid = False
                mapping_inconsistent = True
            if (
                disposition is not None
                and expected_key is not None
                and disposition is not CandidateDisposition.FAILED
                and (
                    not isinstance(result, ReflectionStoreResult)
                    or result.idempotency_key != expected_key
                )
            ):
                valid = False
                mapping_inconsistent = True
            if (
                disposition is CandidateDisposition.FAILED
                and expected_key is not None
                and isinstance(result, ReflectionStoreResult)
                and result.idempotency_key
                and result.idempotency_key != expected_key
            ):
                valid = False
                mapping_inconsistent = True
            if disposition in required_ids:
                if not valid_id:
                    valid = False
                    mapping_inconsistent = True
            elif valid and canonical_id is not None:
                valid = False
                mapping_inconsistent = True
            if valid and intent.canonical_id is not None:
                if canonical_id != intent.canonical_id:
                    valid = False
                    mapping_inconsistent = True
            ledger_unresolved = ledger_unresolved or mapping_inconsistent
            if not valid:
                unknown_count += 1
                final_intents.append(
                    replace(
                        intent,
                        disposition=None,
                        status=CandidateLedgerStatus.UNKNOWN,
                    )
                )
                continue
            assert disposition is not None
            counts[disposition] += 1
            final_intents.append(
                replace(
                    intent,
                    disposition=disposition,
                    status=(
                        CandidateLedgerStatus.FAILED
                        if disposition is CandidateDisposition.FAILED
                        else CandidateLedgerStatus.COMMITTED
                    ),
                    canonical_id=canonical_id,
                )
            )
        failed_count = counts[CandidateDisposition.FAILED]
        can_advance = failed_count == 0 and unknown_count == 0
        reason_code = (
            SummaryReasonCode.COMPLETED
            if can_advance
            else (
                SummaryReasonCode.LEDGER_UNRESOLVED
                if ledger_unresolved
                else (
                    SummaryReasonCode.INVALID_ACTION
                    if unknown_count
                    else SummaryReasonCode.UNKNOWN
                )
            )
        )
        return WindowOutcome(
            can_advance=can_advance,
            canonical_count=counts[CandidateDisposition.CANONICAL],
            quarantine_count=counts[CandidateDisposition.QUARANTINED],
            discard_count=counts[CandidateDisposition.DISCARD],
            mark_write_count=counts[CandidateDisposition.MARK_WRITE],
            failed_count=failed_count,
            skipped_idempotent_count=counts[CandidateDisposition.SKIPPED_IDEMPOTENT],
            unknown_count=unknown_count,
            candidate_slots=tuple(final_intents),
            failed_stage=None if can_advance else "candidate_write",
            reason_code=reason_code,
        )

    @staticmethod
    def _unknown_outcome(
        intents: Sequence[CandidateIntent],
        *,
        stage: str,
        reason_code: SummaryReasonCode,
    ) -> WindowOutcome:
        """把 ledger、slot 或副作用不确定性固定为不可推进结果。"""

        return WindowOutcome(
            can_advance=False,
            unknown_count=len(intents),
            candidate_slots=tuple(
                replace(intent, status=CandidateLedgerStatus.UNKNOWN)
                for intent in intents
            ),
            failed_stage=stage,
            reason_code=reason_code,
        )


__all__ = ["SummaryWorker", "SummaryWorkerFailure"]
