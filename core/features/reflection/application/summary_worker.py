"""单个持久化总结 claim 的来源校验与候选收口。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ....shared.summary_source import source_window_digest
from ...quality.application.gate_runtime import gate_snapshot_from_json
from ...recall.processors.json_parser import SummaryParseError
from ..domain.summary_models import (
    CandidateMetrics,
    ClaimedJob,
    SourceWindow,
    SummaryReasonCode,
    TopicCandidateMode,
    WindowOutcome,
)
from .candidate_writer import store_reflection_candidates
from .summary_worker_candidates import (
    SummaryWorkerCandidateMixin,
    _claim_fence,
)
from .summary_worker_reconcile import SummaryWorkerReconcileMixin
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
from .topic_candidate_selector_helper import baseline_selection, select_with_fallback

if TYPE_CHECKING:
    from ....shared.contracts import ReflectionWritePort
    from ...quality.application.memory_quality_gate import MemoryQualityGate
    from ...recall.processors.memory_processor import MemoryProcessor
    from ..domain.summary_ports import SummaryJobStorePort
    from .topic_batch_preparer import TopicBatchPreparer
    from .topic_candidate_selector import TopicCandidateSelector


class SummaryWorker(
    SummaryWorkerCandidateMixin,
    SummaryWorkerReconcileMixin,
    SummaryWorkerValidationMixin,
):
    """执行单个 claim，并只返回 Store 可原子收口的 WindowOutcome。"""

    def __init__(
        self,
        job_store: SummaryJobStorePort,
        processor: MemoryProcessor,
        quality_gate: MemoryQualityGate | None,
        memory_engine: ReflectionWritePort,
        batch_preparer: TopicBatchPreparer,
        candidate_selector: TopicCandidateSelector | None = None,
        config_manager: Any | None = None,
    ) -> None:
        """绑定 worker 所需的窄 Store port 与现有候选处理流水线。

        ``candidate_selector`` 允许缺省：无 selector 时总结主链不受影响，
        候选选择在执行期降级为 baseline。
        """

        self._job_store = job_store
        self._processor = processor
        self._quality_gate = quality_gate
        self._memory_engine = memory_engine
        self._batch_preparer = batch_preparer
        self._candidate_selector = candidate_selector
        self._config_manager = config_manager

    async def execute(self, claim: ClaimedJob) -> WindowOutcome:
        """校验固定来源、调用 selector、抽取候选、持久化 intent 并生成窗口结果。"""

        source = await self._read_source(claim)

        # 选择话题候选（失败降级为 baseline，不阻塞主链）
        start_time = time.monotonic()
        try:
            candidate_selection = await select_with_fallback(
                self._candidate_selector,
                source,
                claim,
                self._get_candidate_reuse_config(),
            )
            selector_latency_ms = (time.monotonic() - start_time) * 1000

            # 构造 metrics：以配置 mode 为准——observe 影子的
            # effective_mode 固定为 OFF（不注入 Prompt），但其候选与
            # 成本指标正是灰度观测数据，不能按 OFF 丢弃。
            mode_value = (
                candidate_selection.mode
                if isinstance(candidate_selection.mode, TopicCandidateMode)
                else TopicCandidateMode(candidate_selection.mode)
            )
            if mode_value == TopicCandidateMode.OFF:
                candidate_metrics = None
            else:
                # 计算候选数量和有来源证据的数量
                # n_candidates 按 selection 级标签计（含 observe 影子），
                # n_with_provenance 只统计 production_labels，保持口径区分
                n_candidates = len(candidate_selection.labels)
                n_with_provenance = len(candidate_selection.production_labels)
                # 规范化为 TopicCandidateMode 枚举
                mode = mode_value
                effective_mode = (
                    candidate_selection.effective_mode
                    if isinstance(
                        candidate_selection.effective_mode, TopicCandidateMode
                    )
                    else TopicCandidateMode(candidate_selection.effective_mode)
                )
                candidate_metrics = CandidateMetrics(
                    mode=mode,
                    effective_mode=effective_mode,
                    n_candidates=n_candidates,
                    n_with_provenance=n_with_provenance,
                    n_tokens=None,
                    selector_latency_ms=selector_latency_ms,
                    reason=candidate_selection.reason_code,
                )

        except asyncio.CancelledError:
            raise
        except Exception:
            selector_latency_ms = (time.monotonic() - start_time) * 1000
            candidate_metrics = CandidateMetrics(
                mode=TopicCandidateMode.OFF,
                effective_mode=TopicCandidateMode.OFF,
                n_candidates=0,
                n_with_provenance=0,
                n_tokens=None,
                selector_latency_ms=selector_latency_ms,
                reason="selector_failed",
            )
            config = self._get_candidate_reuse_config()

            candidate_selection = baseline_selection(
                mode=config.mode,
                reason_code="selector_failed",
            )
        snapshot_payload = self._snapshot_payload(claim)
        is_group_chat = self._is_group_chat(claim)
        messages = await self._prepare_base_batch(source, is_group_chat)
        memories = await self._process_messages(
            claim,
            messages,
            is_group_chat,
            snapshot_payload,
            candidate_selection,
        )
        if not memories:
            return WindowOutcome(
                can_advance=True,
                candidate_metrics=candidate_metrics,
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
                candidate_metrics=candidate_metrics,
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
                candidate_metrics=candidate_metrics,
            )
        if not await self._begin_candidate_writes(claim, intents):
            return self._unknown_outcome(
                intents,
                stage="candidate_intent",
                reason_code=SummaryReasonCode.LEDGER_UNRESOLVED,
                candidate_metrics=candidate_metrics,
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
                candidate_metrics=candidate_metrics,
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
                scope_key=claim.scope_key or None,
                privacy_level=claim.privacy_level,
                resolver_revision=claim.resolver_revision or None,
                chat_type=claim.chat_type,
                source_provenance_complete=(True if claim.scope_available else None),
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
                candidate_metrics=candidate_metrics,
            )
        expected_snapshots = tuple(map(_fixed_quality_key, candidates))
        return self._build_outcome(
            intents,
            results,
            expected_idempotency_keys=expected_snapshots,
            candidate_metrics=candidate_metrics,
        )

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
        candidate_selection,
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
                candidate_selection=candidate_selection,
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
