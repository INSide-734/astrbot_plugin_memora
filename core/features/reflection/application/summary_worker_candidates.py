"""总结 Worker 的候选执行、来源 fencing 和终态收口支持。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

from ..domain.config import CandidateReuseConfig
from ..domain.storage_outcomes import ReflectionStoreOutcome, ReflectionStoreResult
from ..domain.summary_models import (
    CandidateDisposition,
    CandidateIntent,
    CandidateLedgerStatus,
    CandidateMetrics,
    ClaimedJob,
    SummaryReasonCode,
    WindowOutcome,
)
from .candidate_writer import (
    build_reflection_idempotency_key,
)
from .summary_worker_support import (
    FixedQualityGate as _FixedQualityGate,
)
from .summary_worker_support import (
    SummaryWorkerFailure,
)
from .summary_worker_support import (
    fixed_quality_key as _fixed_quality_key,
)

_RESULT_DISPOSITIONS = {
    ReflectionStoreOutcome.CANONICAL: CandidateDisposition.CANONICAL,
    ReflectionStoreOutcome.QUARANTINED: CandidateDisposition.QUARANTINED,
    ReflectionStoreOutcome.DISCARDED: CandidateDisposition.DISCARD,
    ReflectionStoreOutcome.MARK_WRITE: CandidateDisposition.MARK_WRITE,
    # 合并消费候选 slot 但不新增 canonical：ledger 使用显式 merged 处置，
    # 以便同窗口多个槽位合法共享同一 owner。
    ReflectionStoreOutcome.MERGED: CandidateDisposition.MERGED,
    ReflectionStoreOutcome.SKIPPED_IDEMPOTENT: CandidateDisposition.SKIPPED_IDEMPOTENT,
    ReflectionStoreOutcome.FAILED: CandidateDisposition.FAILED,
}
_STORE_SLOT_PLACEHOLDER = "store-owned"


def _claim_fence(claim: ClaimedJob) -> str:
    """根据 claim 的 epoch、generation 和 token 生成不透明来源 fence。"""
    fence = f"{claim.session_epoch}:{claim.worker_generation}:{claim.claim_token}"
    return hashlib.sha256(fence.encode()).hexdigest()


def count_rejected_facts(candidates: Sequence[dict[str, Any]]) -> int:
    """统计本窗口被逐事实准入拒绝的事实数（只读安全标量）。

    每条候选的 ``fact_dispositions`` 覆盖该候选的全部事实；部分拒绝时处理器还会
    产出只携带被拒事实的隔离载荷副本，其处置数与 ``key_facts`` 逐一相等，跳过它
    可避免同一拒绝被计数两次。计数只用于诊断，不影响任何写入终态。
    """

    rejected = 0
    for candidate in candidates:
        metadata = candidate.get("metadata") if isinstance(candidate, dict) else None
        if not isinstance(metadata, Mapping):
            continue
        dispositions = metadata.get("fact_dispositions")
        facts = metadata.get("key_facts")
        if not isinstance(dispositions, list) or not isinstance(facts, list):
            continue
        if len(dispositions) == len(facts):
            continue
        rejected += sum(
            1
            for item in dispositions
            if isinstance(item, Mapping) and str(item.get("status") or "") != "grounded"
        )
    return rejected


class SummaryWorkerCandidateMixin:
    """提供候选配置、质量路由、幂等写入和 ledger 终态收口。"""

    def _get_candidate_reuse_config(self) -> CandidateReuseConfig:
        """获取候选复用配置快照，异常时保持默认 observe。"""
        if self._config_manager is None:
            return CandidateReuseConfig(mode="observe")
        try:
            snapshot = self._config_manager.get_config_snapshot()[0]
            topic_segmentation = snapshot.get("topic_segmentation")
            if not isinstance(topic_segmentation, dict):
                return CandidateReuseConfig(mode="observe")
            candidate_reuse = topic_segmentation.get("candidate_reuse")
            if isinstance(candidate_reuse, dict):
                return CandidateReuseConfig.model_validate(candidate_reuse)
        except Exception:
            pass
        return CandidateReuseConfig(mode="observe")

    async def _claim_is_active(self, claim: ClaimedJob) -> bool:
        """在候选质量门或写入副作用前确认 claim 仍有效。"""
        checker = getattr(self._job_store, "claim_is_active", None)
        if not callable(checker):
            checker = getattr(self._job_store, "_claim_matches", None)
        if not callable(checker):
            return False
        result = checker(claim)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

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
    ) -> tuple[object | None, SummaryReasonCode | None, str | None]:
        """用同一固化快照预求值质量门，并拒绝候选快照变化。"""
        gate = self._quality_gate
        if gate is None:
            return None, None, None
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
        if claim.scope_available:
            source_window.update(
                {
                    "scope_key": claim.scope_key,
                    "privacy_level": claim.privacy_level,
                    "resolver_revision": claim.resolver_revision,
                    "source_provenance_complete": True,
                }
            )
        results: dict[tuple[str, str], object] = {}
        for candidate in candidates:
            try:
                snapshot_key = _fixed_quality_key(candidate)
            except (TypeError, ValueError) as error:
                return (
                    None,
                    SummaryReasonCode.LEDGER_UNRESOLVED,
                    error.__class__.__name__,
                )
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
            except Exception as error:
                return (
                    None,
                    SummaryReasonCode.LEDGER_UNRESOLVED,
                    error.__class__.__name__,
                )
            if getattr(result, "action", None) not in {
                "allow",
                "quarantined",
                "discard",
                "mark_write",
            }:
                return None, SummaryReasonCode.INVALID_ACTION, "unknown"
            try:
                if _fixed_quality_key(candidate) != snapshot_key:
                    return None, SummaryReasonCode.LEDGER_UNRESOLVED, "unknown"
            except (TypeError, ValueError) as error:
                return (
                    None,
                    SummaryReasonCode.LEDGER_UNRESOLVED,
                    error.__class__.__name__,
                )
            results[snapshot_key] = result
        return _FixedQualityGate(results), None, None

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
            if claim.scope_available:
                metadata["scope_key"] = claim.scope_key
                metadata["privacy_level"] = claim.privacy_level
                metadata["resolver_revision"] = claim.resolver_revision
                metadata["source_provenance_complete"] = True
            else:
                for field_name in (
                    "scope_key",
                    "privacy_level",
                    "resolver_revision",
                    "source_provenance_complete",
                ):
                    metadata.pop(field_name, None)
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
    ) -> tuple[dict[str, int], dict[str, int]]:
        """识别已写入或已合并的候选。

        返回 ``(canonical owners, merged owners)``。合并只更新既有 canonical 的
        metadata，不建立 canonical 幂等映射，因此重放必须先按 merged 键证明
        owner；暂存/拒绝 owner 不得推进窗口 cursor，两者都为空时由调用方决定
        是否重新写入。
        """

        merged_finder = getattr(
            self._memory_engine,
            "find_memory_id_by_merged_idempotency_key",
            None,
        )
        canonical_finder = getattr(
            self._memory_engine,
            "find_memory_id_by_idempotency_key",
            None,
        )
        if not callable(merged_finder) and not callable(canonical_finder):
            return {}, {}
        verifier = getattr(self._memory_engine, "is_memory_source_accepted", None)
        completed: dict[str, int] = {}
        merged: dict[str, int] = {}
        try:
            for candidate in candidates:
                key = str(candidate["metadata"]["idempotency_key"])
                for finder, target in (
                    (merged_finder, merged),
                    (canonical_finder, completed),
                ):
                    if not callable(finder):
                        continue
                    finder_call = cast(Callable[[str], Awaitable[int | None]], finder)
                    owner = await finder_call(key)
                    if owner is None:
                        continue
                    if (
                        isinstance(owner, bool)
                        or not isinstance(owner, int)
                        or owner <= 0
                    ):
                        raise ValueError("canonical_owner_invalid")
                    if callable(verifier):
                        accepted = verifier(owner)
                        if inspect.isawaitable(accepted):
                            accepted = await accepted
                        if accepted is not True:
                            continue
                    target[key] = owner
                    break
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise SummaryWorkerFailure(
                "candidate_reconcile",
                SummaryReasonCode.LEDGER_UNRESOLVED,
                retryable=False,
                exception_type=error.__class__.__name__,
            ) from error
        return completed, merged

    def _build_outcome(
        self,
        intents: Sequence[CandidateIntent],
        results: Sequence[ReflectionStoreResult],
        *,
        expected_idempotency_keys: Sequence[tuple[str, str]] | None = None,
        candidate_metrics: CandidateMetrics | None = None,
        facts_rejected_count: int = 0,
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
            CandidateDisposition.MERGED,
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
            merged_count=counts[CandidateDisposition.MERGED],
            facts_rejected_count=facts_rejected_count,
            failed_count=failed_count,
            skipped_idempotent_count=counts[CandidateDisposition.SKIPPED_IDEMPOTENT],
            unknown_count=unknown_count,
            candidate_slots=tuple(final_intents),
            failed_stage=None if can_advance else "candidate_write",
            candidate_metrics=candidate_metrics,
            reason_code=reason_code,
        )

    @staticmethod
    def _unknown_outcome(
        intents: Sequence[CandidateIntent],
        *,
        stage: str,
        reason_code: SummaryReasonCode,
        exception_type: str | None = "unknown",
        candidate_metrics: CandidateMetrics | None = None,
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
            candidate_metrics=candidate_metrics,
            reason_code=reason_code,
            exception_type=exception_type,
        )


__all__ = ["SummaryWorkerCandidateMixin", "_claim_fence", "count_rejected_facts"]
