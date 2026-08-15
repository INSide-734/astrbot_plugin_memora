"""记忆演化任务的编排、校验、重试和后台 worker。"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from ..domain import (
    EvolutionProposal,
    EvolutionSignal,
    JobClaim,
    JobSpec,
    MemorySourceRef,
    RelationType,
    RetrySpec,
)
from .memory_evolution_manager_helpers import (
    _as_float,
    _as_int,
    _is_retryable_error,
    _parse_relation_types,
    _reason,
    _same_source_revisions,
)
from .memory_evolution_plan_builder import MemoryEvolutionPlanBuilderMixin
from .memory_evolution_projection import (
    EvolutionProposalRejected,
    MemoryEvolutionProjectionProposalMixin,
)


class EvolutionLeaseLost(RuntimeError):
    """表示当前 worker 已失去 job lease，不能继续写入派生结果。"""


class MemoryEvolutionManager(
    MemoryEvolutionPlanBuilderMixin,
    MemoryEvolutionProjectionProposalMixin,
):
    """编排本地 evolution job，并把通过校验的 proposal 原子应用到 Store。"""

    _HIGH_IMPACT = frozenset(
        {
            RelationType.UPDATES,
            RelationType.PREFERENCE_CHANGE,
            RelationType.CAUSES,
            RelationType.CONTRADICTS,
            RelationType.SUPERSEDES,
        }
    )
    _LOW_IMPACT = frozenset(
        {RelationType.SAME_EPISODE, RelationType.SUPPORTS, RelationType.RELATED}
    )
    _SOURCE_INVALIDATION_REASONS = frozenset(
        {
            "source_inactive",
            "source_memory_not_found",
            "source_privacy_mismatch",
            "source_revision_mismatch",
            "source_scope_mismatch",
        }
    )

    def __init__(
        self,
        store,
        gate,
        consolidator,
        config: Mapping[str, Any] | None = None,
        *,
        candidate_generator=None,
    ):
        """绑定 Store、门控、Provider consolidator 和可选本地候选生成器。"""

        self.store = store
        self.gate = gate
        self.consolidator = consolidator
        self.candidate_generator = candidate_generator
        config = config or {}
        self.max_attempts = max(1, _as_int(config.get("max_attempts"), 3))
        self.lease_seconds = max(1, _as_int(config.get("lease_seconds"), 120))
        self.retry_base_delay_seconds = max(
            1, _as_int(config.get("retry_base_delay_seconds"), 10)
        )
        self.poll_interval_seconds = max(
            0.05, _as_float(config.get("poll_interval_seconds"), 0.5)
        )
        self.max_input_chars = max(1, _as_int(config.get("max_input_chars"), 12_000))
        self.candidate_limit = max(1, _as_int(config.get("candidate_limit"), 16))
        self.confidence_threshold = min(
            1.0,
            max(0.0, _as_float(config.get("trigger_threshold"), 0.7)),
        )
        configured_active = config.get(
            "auto_active_relation_types",
            [item.value for item in self._LOW_IMPACT],
        )
        self.auto_active_relation_types = _parse_relation_types(configured_active)
        self.require_review_for_high_impact = bool(
            config.get("require_review_for_high_impact", True)
        )
        self._worker_task: asyncio.Task | None = None
        self._stopping = False
        self._accepted = 0
        self._rejected = 0
        self._retry = 0
        self._dead = 0
        self._reason_codes: dict[str, int] = {}

    @property
    def mode(self) -> str:
        """返回门控归一化后的运行模式。"""

        return self.gate.mode

    async def start(self) -> None:
        """在非 disabled 模式启动单一后台 worker。"""

        if self.mode == "disabled" or self._worker_task is not None:
            return
        self._stopping = False
        try:
            await self.store.cleanup_orphaned_derived()
        except asyncio.CancelledError:
            raise
        except Exception:
            # 清理属于派生维护；失败时保留 canonical 可读并继续恢复 worker。
            self._record_reason("orphan_cleanup_failed")
        try:
            await self.store.recover_expired_leases(datetime.now(timezone.utc))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._record_reason("lease_recovery_failed")
        self._worker_task = asyncio.create_task(
            self._worker_loop(),
            name="memory-evolution-worker",
        )

    async def stop(self) -> None:
        """取消 worker，并等待其释放当前 job lease。"""

        self._stopping = True
        task = self._worker_task
        self._worker_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise

    async def schedule_consider(
        self,
        source: MemorySourceRef,
        *,
        replay: bool = False,
    ):
        """在 canonical 写入成功后执行本地 gate 和 job enqueue。"""

        pending_jobs = await self.store.pending_count()
        signal = EvolutionSignal(
            memory_id=source.memory_id,
            revision_token=source.revision_token,
            importance=0.8,
            scope_key=source.scope_key,
            topic_keys=source.topic_keys or ("memory",),
            entity_keys=(source.subject_key or str(source.memory_id),),
            occurred_at=source.occurred_at,
            pending_jobs=pending_jobs,
            privacy_level=source.privacy_level,
            content=source.content,
        )
        decision = (
            self.gate.consider(signal, replay=True)
            if replay
            else self.gate.consider(signal)
        )
        if not decision.should_enqueue:
            return decision
        sources = [source]
        if self.candidate_generator is not None:
            sources = await self.store.load_candidate_sources(
                source,
                limit=min(6, self.candidate_limit + 1),
                max_content_chars=self.max_input_chars,
            )
        spec = JobSpec(
            scope_key=source.scope_key,
            bucket_key=decision.bucket_key or "",
            source_ids=tuple(item.memory_id for item in sources),
            idempotency_key=decision.idempotency_key or "",
            not_before=datetime.now(timezone.utc),
            source_revisions={item.memory_id: item.revision_token for item in sources},
        )
        if replay:
            await self.store.requeue_job(spec)
        else:
            await self.store.enqueue_job(spec)
        return decision

    async def run_once(self) -> bool:
        """领取并处理一个 job；没有可执行 job 时返回 False。"""

        claim = await self.store.claim_job(
            datetime.now(timezone.utc), self.lease_seconds
        )
        if claim is None:
            return False
        try:
            await self.process_claim(claim)
        except asyncio.CancelledError:
            raise
        except EvolutionLeaseLost:
            self._record_reason("job_lease_lost")
        except EvolutionProposalRejected as exc:
            reason_code = _reason(exc)
            if reason_code in {"source_revision_changed", "source_not_found"}:
                transitioned = await self.store.invalidate_job(
                    claim.job_id,
                    claim.worker_token,
                    reason_code,
                )
            else:
                transitioned = await self.store.reject_job(
                    claim.job_id,
                    claim.worker_token,
                    reason_code,
                )
            if not transitioned:
                self._record_reason("job_lease_lost")
                return True
            self._rejected += 1
            self._record_reason(reason_code)
        except ValueError as exc:
            reason_code = _reason(exc)
            if reason_code in self._SOURCE_INVALIDATION_REASONS:
                transitioned = await self.store.invalidate_job(
                    claim.job_id,
                    claim.worker_token,
                    reason_code,
                )
                if not transitioned:
                    self._record_reason("job_lease_lost")
                    return True
                self._rejected += 1
                self._record_reason(reason_code)
            else:
                await self._handle_retry(claim, exc)
        except Exception as exc:
            await self._handle_retry(claim, exc)
        return True

    async def process_claim(self, claim: JobClaim) -> None:
        """处理单个 claim；业务拒绝和可恢复错误交给 run_once 分类。"""

        renewal_task = asyncio.create_task(
            self._renew_claim_lease(claim),
            name="memory-evolution-lease-renewal",
        )
        try:
            sources = await self.store.load_sources(
                claim.source_ids,
                max_content_chars=self.max_input_chars,
            )
            if not sources:
                raise EvolutionProposalRejected("source_not_found")
            if any(source.scope_key != claim.scope_key for source in sources):
                raise EvolutionProposalRejected("scope_mismatch")
            if claim.source_revisions:
                loaded_revisions = {
                    source.memory_id: source.revision_token for source in sources
                }
                if loaded_revisions != claim.source_revisions:
                    raise EvolutionProposalRejected("source_revision_changed")
            proposal = EvolutionProposal()
            if self.candidate_generator is not None:
                proposal = await self.candidate_generator.propose(
                    sources,
                    limit=self.candidate_limit,
                )
            if not proposal.relations and not proposal.projections:
                proposal = await self.consolidator.propose(sources)
            if not isinstance(proposal, EvolutionProposal):
                raise EvolutionProposalRejected("proposal_schema_invalid")
            fresh_sources = await self.store.load_sources(
                claim.source_ids,
                max_content_chars=self.max_input_chars,
            )
            if not _same_source_revisions(sources, fresh_sources):
                raise EvolutionProposalRejected("source_revision_changed")
            plan = self._proposal_to_plan(proposal, fresh_sources)
            plan = replace(plan, origin_job_id=claim.job_id)
            renewed = await self.store.renew_lease(
                claim.job_id,
                claim.worker_token,
                datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds),
            )
            if not renewed:
                raise EvolutionLeaseLost("job_lease_lost")
            await self.store.apply_derived_plan(plan)
            completed = await self.store.complete_job(
                claim.job_id,
                claim.worker_token,
            )
            if not completed:
                raise EvolutionLeaseLost("job_lease_lost")
            self._accepted += 1
        except asyncio.CancelledError:
            try:
                await self.store.restore_pending(claim.job_id, claim.worker_token)
            except Exception:
                self._record_reason("cancel_restore_failed")
            raise
        finally:
            renewal_task.cancel()
            try:
                await renewal_task
            except asyncio.CancelledError:
                pass

    async def reconcile_recent_sources(
        self,
        sources: Iterable[MemorySourceRef],
        *,
        replay: bool = False,
    ) -> int:
        """对近期 canonical source 补偿缺失的演化 job。"""

        scheduled = 0
        for source in sources:
            decision = await self.schedule_consider(source, replay=replay)
            if decision.should_enqueue:
                scheduled += 1
        return scheduled

    async def rebuild_from_canonical(self) -> dict[str, Any]:
        """失效旧派生并从当前 canonical source revision 重新排队演化任务。

        该协调器只负责清理和排队，不凭空生成 relation/projection。canonical
        读取失败或派生维护失败时返回稳定降级结果；取消信号仍继续向上传播。
        """

        try:
            invalidated = await self.store.invalidate_all_derived()
            sources = await self.store.load_all_sources(
                max_content_chars=self.max_input_chars
            )
            # mark_write 低置信记忆不进入演化，重建补偿同样排除。
            mark_write_ids = await self.store.load_mark_write_ids()
            sources = [
                source for source in sources if source.memory_id not in mark_write_ids
            ]
            scheduled = await self.reconcile_recent_sources(sources, replay=True)
            return {
                "success": True,
                "canonical_sources": len(sources),
                "scheduled_jobs": scheduled,
                "relations_invalidated": int(
                    invalidated.get("relations_invalidated", 0)
                ),
                "projections_invalidated": int(
                    invalidated.get("projections_invalidated", 0)
                ),
                "projection_sources_removed": int(
                    invalidated.get("projection_sources_removed", 0)
                ),
                "reason_code": "derived_rebuild_scheduled",
            }
        except asyncio.CancelledError:
            raise
        except Exception:
            self._record_reason("derived_rebuild_failed")
            return {
                "success": False,
                "canonical_sources": 0,
                "scheduled_jobs": 0,
                "reason_code": "derived_rebuild_failed",
            }

    def get_status_snapshot(self) -> dict[str, Any]:
        """返回不含 query、prompt、正文和身份列表的本地状态快照。"""

        return {
            "mode": self.mode,
            "state_counts": {
                "completed": self._accepted,
                "rejected": self._rejected,
                "retry_wait": self._retry,
                "dead": self._dead,
            },
            "queue_lag_seconds": None,
            "type_counts": {},
            "accepted": self._accepted,
            "rejected": self._rejected,
            "retry": self._retry,
            "dead": self._dead,
            "reason_codes": dict(self._reason_codes),
            "token_totals": {"input": 0, "output": 0},
            "latency_buckets": {},
        }

    async def _worker_loop(self) -> None:
        """轮询并处理演化任务，隔离普通轮询失败。"""

        while not self._stopping:
            try:
                did_work = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._record_reason("worker_poll_failed")
                await asyncio.sleep(self.poll_interval_seconds)
                continue
            if not did_work:
                await asyncio.sleep(self.poll_interval_seconds)

    async def _renew_claim_lease(self, claim: JobClaim) -> None:
        """定期续租当前 job，直到 lease 丢失或续租失败。"""

        interval = max(0.05, self.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await self.store.renew_lease(
                    claim.job_id,
                    claim.worker_token,
                    datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds),
                )
            except Exception:
                return
            if not renewed:
                return

    async def _handle_retry(self, claim: JobClaim, error: Exception) -> None:
        """按异常类别将 job 转为 retry_wait 或 dead。"""

        reason_code = _reason(error)
        if not _is_retryable_error(error):
            transitioned = await self.store.dead_job(
                claim.job_id,
                claim.worker_token,
                reason_code,
            )
            if not transitioned:
                self._record_reason("job_lease_lost")
                return
            self._dead += 1
            self._record_reason(reason_code)
            return
        if claim.attempt_count >= self.max_attempts:
            reason_code = "max_attempts"
            transitioned = await self.store.dead_job(
                claim.job_id,
                claim.worker_token,
                reason_code,
            )
            if not transitioned:
                self._record_reason("job_lease_lost")
                return
            self._dead += 1
            self._record_reason(reason_code)
            return
        delay = self.retry_base_delay_seconds * (2 ** max(0, claim.attempt_count - 1))
        delay += random.uniform(0.0, 1.0)
        transitioned = await self.store.retry_job(
            claim.job_id,
            claim.worker_token,
            RetrySpec(
                datetime.now(timezone.utc) + timedelta(seconds=delay),
                claim.attempt_count,
                reason_code,
            ),
        )
        if not transitioned:
            self._record_reason("job_lease_lost")
            return
        self._retry += 1
        self._record_reason(reason_code)

    def _record_reason(self, reason_code: str) -> None:
        """累加单个隐私安全 reason code 的状态计数。"""

        self._reason_codes[reason_code] = self._reason_codes.get(reason_code, 0) + 1


__all__ = [
    "EvolutionLeaseLost",
    "EvolutionProposalRejected",
    "MemoryEvolutionManager",
]
