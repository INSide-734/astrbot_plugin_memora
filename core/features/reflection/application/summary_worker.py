"""单个持久化总结 claim 的来源校验与候选收口。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from ..domain.storage_outcomes import ReflectionStoreOutcome, ReflectionStoreResult
from ..domain.summary_models import (
    CandidateDisposition,
    CandidateIntent,
    CandidateLedgerStatus,
    ClaimedJob,
    SourceWindow,
    SummaryFailure,
    SummaryReasonCode,
    WindowOutcome,
    source_window_digest,
)
from .candidate_writer import (
    build_reflection_idempotency_key,
    store_reflection_candidates,
)

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


class SummaryWorkerFailure(RuntimeError):
    """携带固定失败分类，不保存异常正文。"""

    def __init__(
        self,
        failed_stage: str,
        reason_code: SummaryReasonCode,
        *,
        retryable: bool,
        exception_type: str = "",
    ) -> None:
        """保存 worker 可提交的固定失败字段。"""

        super().__init__(reason_code.value)
        self.failed_stage = failed_stage
        self.reason_code = reason_code
        self.retryable = retryable
        self.exception_type = exception_type

    def to_failure(self) -> SummaryFailure:
        """转换为不含异常正文的持久化失败 DTO。"""

        return SummaryFailure(
            failed_stage=self.failed_stage,
            reason_code=self.reason_code,
            exception_type=self.exception_type,
            retryable=self.retryable,
        )


def _supports_keyword(call: Callable[..., object], keyword: str) -> bool:
    """判断分阶段接入的协作 API 是否显式接受固定快照参数。"""

    try:
        parameters = inspect.signature(call).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD or parameter.name == keyword
        for parameter in parameters
    )


async def _canonical_hook_already_owned(_memory_id: int) -> None:
    """保持候选写入器调用形状；演化由 MemoryEngine 写后钩子唯一调度。"""


class _FixedQualityGate:
    """向现有 candidate writer 回放同一固定快照下的门禁结果。"""

    def __init__(self, results: Mapping[int, object]) -> None:
        """按候选对象身份保存已验证的闭集门禁结果。"""

        self._results = dict(results)

    async def route_candidate(
        self,
        candidate: dict[str, Any],
        **_context: object,
    ) -> object:
        """返回预先求值的门禁结果；候选重写或重排时立即失败。"""

        result = self._results.get(id(candidate))
        if result is None:
            raise RuntimeError("fixed_gate_result_missing")
        return result


class SummaryWorker:
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
        completed_keys = await self._find_completed_keys(candidates)
        fixed_quality_gate, gate_reason = await self._route_quality(
            claim,
            candidates,
            completed_keys,
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
                completed_idempotency_keys=completed_keys,
                session_id=claim.session_id,
                persona_id=claim.persona_id,
                start_index=claim.start_seq,
                end_index=claim.end_seq,
                is_group_chat=is_group_chat,
                group_id=claim.group_id,
                gate_snapshot_json=claim.gate_snapshot_json,
                before_side_effect=lambda: self._claim_is_active(claim),
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
        return self._build_outcome(intents, results)

    async def _route_quality(
        self,
        claim: ClaimedJob,
        candidates: Sequence[dict[str, Any]],
        completed_keys: set[str],
        snapshot_payload: Mapping[str, object],
    ) -> tuple[object | None, SummaryReasonCode | None]:
        """用同一固化快照预求值质量门，并拒绝闭集外 action。"""
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
            "message_count": claim.expected_count,
        }
        results: dict[int, object] = {}
        for candidate in candidates:
            key = str(candidate.get("metadata", {}).get("idempotency_key") or "")
            if key in completed_keys:
                continue
            if not await self._claim_is_active(claim):
                raise SummaryWorkerFailure(
                    "claim_fence",
                    SummaryReasonCode.CLAIM_LOST,
                    retryable=False,
                )
            try:
                result = await gate.route_candidate(
                    candidate,
                    session_id=claim.session_id,
                    persona_id=claim.persona_id,
                    source_window=source_window,
                    is_group_chat=self._is_group_chat(claim),
                    group_id=claim.group_id,
                    chat_type=claim.chat_type,
                    **snapshot_kwargs,
                )
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
            results[id(candidate)] = result
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
        """解析 job 固化的有界 JSON 对象；不可恢复时阻塞窗口。"""
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
        if not {"enabled", "default_profile", "profiles", "bindings"} <= set(payload):
            raise SummaryWorkerFailure(
                "gate_snapshot",
                SummaryReasonCode.BLOCKED,
                retryable=False,
                exception_type="IncompleteSnapshot",
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
                **snapshot_kwargs,
            )
        except asyncio.CancelledError:
            raise
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
                    slot_key=_STORE_SLOT_PLACEHOLDER,
                )
            )
        return candidates, tuple(intents)

    async def _find_completed_keys(
        self,
        candidates: Sequence[dict[str, Any]],
    ) -> set[str]:
        """用 canonical 幂等索引识别崩溃后已写成功的候选，避免重复写。"""

        finder = getattr(
            self._memory_engine,
            "find_memory_id_by_idempotency_key",
            None,
        )
        if not callable(finder):
            return set()
        finder_call = cast(Callable[[str], Awaitable[int | None]], finder)
        completed: set[str] = set()
        try:
            for candidate in candidates:
                key = str(candidate["metadata"]["idempotency_key"])
                if await finder_call(key) is not None:
                    completed.add(key)
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
    ) -> WindowOutcome:
        """穷举候选动作并把未知动作收敛为不可推进的 unknown。"""

        if len(results) != len(intents):
            return self._unknown_outcome(
                intents,
                stage="candidate_write",
                reason_code=SummaryReasonCode.INVALID_SLOT,
            )
        counts = {disposition: 0 for disposition in CandidateDisposition}
        final_intents: list[CandidateIntent] = []
        unknown_count = 0
        for intent, result in zip(intents, results, strict=True):
            disposition = (
                _RESULT_DISPOSITIONS.get(result.outcome)
                if isinstance(result, ReflectionStoreResult)
                else None
            )
            if disposition is None:
                unknown_count += 1
                final_intents.append(
                    replace(intent, status=CandidateLedgerStatus.UNKNOWN)
                )
                continue
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
                )
            )
        failed_count = counts[CandidateDisposition.FAILED]
        can_advance = failed_count == 0 and unknown_count == 0
        reason_code = (
            SummaryReasonCode.COMPLETED
            if can_advance
            else (
                SummaryReasonCode.INVALID_ACTION
                if unknown_count
                else SummaryReasonCode.UNKNOWN
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
            unknown_count=max(1, len(intents)),
            candidate_slots=tuple(
                replace(intent, status=CandidateLedgerStatus.UNKNOWN)
                for intent in intents
            ),
            failed_stage=stage,
            reason_code=reason_code,
        )

    @staticmethod
    def _fixed_snapshot_kwargs(
        call: Callable[..., object],
        claim: ClaimedJob,
        payload: Mapping[str, object],
        *,
        required: bool,
    ) -> dict[str, Any]:
        """向已完成快照接入的 API 传递同一 job 快照，否则保守阻塞。"""

        fixed = bool(claim.gate_revision or payload)
        if _supports_keyword(call, "gate_snapshot_json"):
            kwargs: dict[str, Any] = {
                "gate_snapshot_json": claim.gate_snapshot_json,
            }
            if _supports_keyword(call, "gate_revision"):
                kwargs["gate_revision"] = claim.gate_revision
            return kwargs
        if _supports_keyword(call, "gate_snapshot"):
            kwargs = {"gate_snapshot": payload}
            if _supports_keyword(call, "gate_revision"):
                kwargs["gate_revision"] = claim.gate_revision
            return kwargs
        if fixed and required:
            raise SummaryWorkerFailure(
                "gate_snapshot",
                SummaryReasonCode.BLOCKED,
                retryable=False,
                exception_type="SnapshotAdapterUnavailable",
            )
        return {}

    @staticmethod
    def _is_group_chat(claim: ClaimedJob) -> bool:
        """只使用已固化 chat_type/group_id 判定群聊，不猜测 session 标识。"""

        if claim.chat_type not in (None, "", "private", "group"):
            raise SummaryWorkerFailure(
                "identity_scope",
                SummaryReasonCode.BLOCKED,
                retryable=False,
            )
        return claim.chat_type == "group" or bool(claim.group_id)


__all__ = ["SummaryWorker", "SummaryWorkerFailure"]
