"""记忆演化任务的编排、校验、重试和后台 worker。"""

from __future__ import annotations

import asyncio
import hashlib
import random
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from ..models.memory_evolution import (
    DerivedApplyPlan,
    DerivedState,
    EvolutionProposal,
    MemoryProjectionProposal,
    MemoryRelationProposal,
    MemorySourceRef,
    ProjectionSourceView,
    ProjectionType,
    ProjectionView,
    RelationType,
    RelationView,
    RetrySpec,
    EvolutionSignal,
    JobClaim,
    JobSpec,
)


class EvolutionProposalRejected(ValueError):
    """表示 proposal 没有通过确定性安全校验。"""


class MemoryEvolutionManager:
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
    def __init__(self, store, gate, consolidator, config: Mapping[str, Any] | None = None):
        self.store = store
        self.gate = gate
        self.consolidator = consolidator
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

    @property
    def mode(self) -> str:
        return self.gate.mode

    async def start(self) -> None:
        """在非 disabled 模式启动单一后台 worker。"""

        if self.mode == "disabled" or self._worker_task is not None:
            return
        self._stopping = False
        await self.store.recover_expired_leases(datetime.now(timezone.utc))
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
            pass

    async def schedule_consider(self, source: MemorySourceRef):
        """在 canonical 写入成功后执行本地 gate 和 job enqueue。"""

        pending_jobs = await self.store.pending_count()
        signal = EvolutionSignal(
            memory_id=source.memory_id,
            revision_token=source.revision_token,
            importance=0.8,
            scope_key=source.scope_key,
            topic_keys=("memory",),
            entity_keys=(str(source.memory_id),),
            occurred_at=source.occurred_at,
            pending_jobs=pending_jobs,
            privacy_level=source.privacy_level,
            content=source.content,
        )
        decision = self.gate.consider(signal)
        if not decision.should_enqueue:
            return decision
        await self.store.enqueue_job(
            JobSpec(
                scope_key=source.scope_key,
                bucket_key=decision.bucket_key or "",
                source_ids=(source.memory_id,),
                idempotency_key=decision.idempotency_key or "",
                not_before=datetime.now(timezone.utc),
            )
        )
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
        except EvolutionProposalRejected as exc:
            await self.store.reject_job(claim.job_id, claim.worker_token, _reason(exc))
        except Exception as exc:
            await self._handle_retry(claim, exc)
        return True

    async def process_claim(self, claim: JobClaim) -> None:
        """处理单个 claim；业务拒绝和可恢复错误交给 run_once 分类。"""

        try:
            sources = await self.store.load_sources(
                claim.source_ids,
                max_content_chars=self.max_input_chars,
            )
            if not sources:
                raise EvolutionProposalRejected("source_not_found")
            proposal = await self.consolidator.propose(sources)
            plan = self._proposal_to_plan(proposal, sources)
            await self.store.apply_derived_plan(plan)
            await self.store.complete_job(claim.job_id, claim.worker_token)
        except asyncio.CancelledError:
            await self.store.restore_pending(claim.job_id, claim.worker_token)
            raise

    async def reconcile_recent_sources(self, sources: Iterable[MemorySourceRef]) -> int:
        """对近期 canonical source 补偿缺失的演化 job。"""

        scheduled = 0
        for source in sources:
            decision = await self.schedule_consider(source)
            if decision.should_enqueue:
                scheduled += 1
        return scheduled

    def get_status_snapshot(self) -> dict[str, Any]:
        """返回不含 query、prompt、正文和身份列表的本地状态快照。"""

        return {
            "mode": self.mode,
            "worker_running": self._worker_task is not None,
            "max_attempts": self.max_attempts,
            "lease_seconds": self.lease_seconds,
        }

    async def _worker_loop(self) -> None:
        while not self._stopping:
            did_work = await self.run_once()
            if not did_work:
                await asyncio.sleep(self.poll_interval_seconds)

    async def _handle_retry(self, claim: JobClaim, error: Exception) -> None:
        if claim.attempt_count >= self.max_attempts:
            await self.store.dead_job(claim.job_id, claim.worker_token, "max_attempts")
            return
        delay = self.retry_base_delay_seconds * (2 ** max(0, claim.attempt_count - 1))
        delay += random.uniform(0.0, 1.0)
        await self.store.retry_job(
            claim.job_id,
            claim.worker_token,
            RetrySpec(
                datetime.now(timezone.utc) + timedelta(seconds=delay),
                claim.attempt_count,
                _reason(error),
            ),
        )

    def _proposal_to_plan(
        self,
        proposal: EvolutionProposal,
        sources: list[MemorySourceRef],
    ) -> DerivedApplyPlan:
        aliases = {f"M{index}": source for index, source in enumerate(sources, start=1)}
        relations: list[RelationView] = []
        projections: list[ProjectionView] = []
        projection_sources: list[ProjectionSourceView] = []
        seen_edges: set[tuple[int, int, RelationType]] = set()

        for item in proposal.relations[: self.candidate_limit]:
            source = _alias(aliases, item.source_alias)
            target = _alias(aliases, item.target_alias)
            if source.memory_id == target.memory_id:
                raise EvolutionProposalRejected("self_relation")
            _ensure_compatible(source, target)
            edge = (source.memory_id, target.memory_id, item.relation_type)
            reverse = (target.memory_id, source.memory_id, item.relation_type)
            if edge in seen_edges or reverse in seen_edges:
                raise EvolutionProposalRejected("duplicate_or_cycle")
            seen_edges.add(edge)
            state = (
                DerivedState.ACTIVE
                if item.relation_type in self.auto_active_relation_types
                and item.relation_type in self._LOW_IMPACT
                else DerivedState.CANDIDATE
            )
            relation_id = _stable_id(
                "relation",
                source.memory_id,
                source.revision_token,
                target.memory_id,
                target.revision_token,
                item.relation_type.value,
            )
            relations.append(
                RelationView(
                    relation_id,
                    source.memory_id,
                    target.memory_id,
                    item.relation_type,
                    item.confidence,
                    source.scope_key,
                    _strictest_privacy(source, target),
                    state,
                    source.revision_token,
                    target.revision_token,
                    item.valid_from,
                    item.valid_to,
                )
            )

        for item in proposal.projections[: self.candidate_limit]:
            projection_sources_for_item = [_alias(aliases, alias) for alias in item.source_aliases]
            if len({source.memory_id for source in projection_sources_for_item}) != len(projection_sources_for_item):
                raise EvolutionProposalRejected("duplicate_projection_source")
            _ensure_scope_compatible(*projection_sources_for_item)
            projection_id = _stable_id(
                "projection",
                item.projection_type.value,
                *(source.memory_id for source in projection_sources_for_item),
            )
            projections.append(
                _projection_view(
                    projection_id,
                    item,
                    projection_sources_for_item,
                )
            )
            for ordinal, source in enumerate(projection_sources_for_item):
                role = "primary" if ordinal == 0 else "supporting"
                if item.projection_type is ProjectionType.CONFLICT_SET:
                    role = "primary" if ordinal == 0 else ("conflict_left" if ordinal == 1 else "conflict_right")
                projection_sources.append(
                    ProjectionSourceView(
                        projection_id,
                        source.memory_id,
                        source.revision_token,
                        role,
                        ordinal,
                    )
                )
        return DerivedApplyPlan(
            relations=tuple(relations),
            projections=tuple(projections),
            projection_sources=tuple(projection_sources),
            source_revisions={source.memory_id: source.revision_token for source in sources},
        )


def _alias(aliases: Mapping[str, MemorySourceRef], name: str) -> MemorySourceRef:
    source = aliases.get(name)
    if source is None:
        raise EvolutionProposalRejected("unknown_alias")
    return source


def _ensure_scope_compatible(first: MemorySourceRef, second: MemorySourceRef) -> None:
    if first.scope_key != second.scope_key:
        raise EvolutionProposalRejected("scope_mismatch")


def _ensure_compatible(first: MemorySourceRef, second: MemorySourceRef) -> None:
    _ensure_scope_compatible(first, second)


def _strictest_privacy(*sources: MemorySourceRef) -> str:
    order = {"public": 0, "shared": 1, "confidential": 2}
    return max(sources, key=lambda source: order[source.privacy_level]).privacy_level


def _projection_view(
    projection_id: str,
    item: MemoryProjectionProposal,
    sources: list[MemorySourceRef],
) -> ProjectionView:
    return ProjectionView(
        projection_id,
        item.projection_type,
        item.summary,
        tuple(source.memory_id for source in sources),
        sources[0].scope_key,
        _strictest_privacy(*sources),
        item.confidence,
        DerivedState.ACTIVE,
        item.valid_from,
        item.valid_to,
    )


def _stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def _parse_relation_types(values: Any) -> frozenset[RelationType]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return frozenset()
    result: set[RelationType] = set()
    for value in values:
        try:
            result.add(RelationType(value))
        except (TypeError, ValueError):
            continue
    return frozenset(result)


def _reason(error: Exception) -> str:
    if isinstance(error, EvolutionProposalRejected):
        return str(error) or "proposal_rejected"
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return "provider_timeout"
    if isinstance(error, ConnectionError):
        return "provider_unavailable"
    return "worker_error"


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


__all__ = ["EvolutionProposalRejected", "MemoryEvolutionManager"]
