"""持久化记忆总结任务的有界调度与 worker 编排。"""

from __future__ import annotations

import asyncio
import inspect
import math
import uuid
from collections.abc import Callable, Sequence
from contextvars import Context
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..domain.summary_models import (
    ClaimedJob,
    SummaryEnqueueResult,
    SummaryFailure,
    SummaryReasonCode,
    SummaryTaskSnapshot,
    SummaryWindowContext,
)
from .summary_worker import SummaryWorker, SummaryWorkerFailure

if TYPE_CHECKING:
    from ....shared.contracts import ReflectionWritePort
    from ....shared.summary_llm_limiter import SummaryLlmLimiter
    from ...quality.application.memory_quality_gate import MemoryQualityGate
    from ...recall.processors.memory_processor import MemoryProcessor
    from ..domain.summary_ports import SummaryJobStorePort
    from .topic_batch_preparer import TopicBatchPreparer


def _utc_now() -> datetime:
    """返回供生产调度使用的 UTC 墙钟。"""

    return datetime.now(timezone.utc)


def _supports_keyword(call: Callable[..., object], keyword: str) -> bool:
    """检查替身或真实 Store 是否接受可选关键字参数。"""
    try:
        parameters = inspect.signature(call).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD or parameter.name == keyword
        for parameter in parameters
    )


class SummaryScheduler:
    """统一规划、领取并执行持久化记忆总结窗口。"""

    def __init__(
        self,
        job_store: SummaryJobStorePort,
        processor: MemoryProcessor,
        quality_gate: MemoryQualityGate | None,
        memory_engine: ReflectionWritePort,
        batch_preparer: TopicBatchPreparer,
        *,
        max_parallel_summary_tasks: int = 4,
        max_parallel_summary_tasks_per_session: int = 2,
        clock: Callable[[], datetime | float] | None = None,
        limiter: SummaryLlmLimiter | None = None,
        scheduler_id: str | None = None,
        lease_seconds: int = 300,
        retry_poll_seconds: float = 1.0,
        startup_context_factory: Callable[[str, int, int], SummaryWindowContext]
        | None = None,
    ) -> None:
        """绑定窄 Store port、总结流水线和两层并发配置。

        ``limiter`` 只保留组合根装配关系；物理 permit 必须由处理器的
        LLMClient 获取，调度器绝不围绕整个窗口再次获取。
        """

        if not 1 <= max_parallel_summary_tasks <= 16:
            raise ValueError("全局总结并发上限必须位于 1..16")
        if not 1 <= max_parallel_summary_tasks_per_session <= 8:
            raise ValueError("单会话总结并发上限必须位于 1..8")
        if not math.isfinite(float(lease_seconds)) or lease_seconds <= 0:
            raise ValueError("总结任务租约必须是正有限数")
        if not math.isfinite(float(retry_poll_seconds)) or retry_poll_seconds <= 0:
            raise ValueError("总结任务轮询间隔必须是正有限数")
        if scheduler_id is not None and not str(scheduler_id).strip():
            raise ValueError("scheduler_id 不能为空")

        self._job_store = job_store
        self._worker = SummaryWorker(
            job_store,
            processor,
            quality_gate,
            memory_engine,
            batch_preparer,
        )
        self._max_parallel = max_parallel_summary_tasks
        self._max_parallel_per_session = max_parallel_summary_tasks_per_session
        self._clock = clock or _utc_now
        self._limiter = limiter
        self._scheduler_id = str(scheduler_id or uuid.uuid4().hex)
        set_clock = getattr(job_store, "set_summary_clock", None)
        if callable(set_clock):
            set_clock(self._now)
        self._lease_seconds = float(lease_seconds)
        self._retry_poll_seconds = float(retry_poll_seconds)
        self._startup_context_factory = startup_context_factory

        self._condition = asyncio.Condition()
        self._lifecycle_lock = asyncio.Lock()
        self._wake_generation = 0
        self._loop_task: asyncio.Task[None] | None = None
        self._workers: dict[asyncio.Task[None], ClaimedJob] = {}
        self._round_robin_cursor: str | None = None
        self._known_sessions: set[str] = set()
        self._target_parallelism = 0
        self._accepting_enqueues = True
        self._claiming = False
        self._closed = False

    @property
    def active_parallelism(self) -> int:
        """返回当前仍持有效租约的 worker 数量。"""

        now = self._now().timestamp()
        return sum(
            1
            for task, claim in self._workers.items()
            if not task.done() and claim.lease_until > now
        )

    @property
    def target_parallelism(self) -> int:
        """返回最近一次安全投影计算出的目标并发数。"""

        return self._target_parallelism

    async def start(self) -> None:
        """恢复过期 claim 并幂等启动领取循环。"""

        async with self._lifecycle_lock:
            if self._closed or self._loop_task is not None:
                return
            claim_ready = getattr(self._job_store, "claim_ready", None)
            if not inspect.iscoroutinefunction(claim_ready):
                return
            recovered = self._job_store.recover_expired_claims(self._now())
            if inspect.isawaitable(recovered):
                await recovered
            migrate_legacy = getattr(self._job_store, "recover_legacy_pending", None)
            if callable(migrate_legacy):
                migrated = migrate_legacy()
                if inspect.isawaitable(migrated):
                    await migrated
            plan_frontiers = getattr(self._job_store, "plan_existing_frontiers", None)
            if callable(plan_frontiers) and self._startup_context_factory is not None:
                planned = plan_frontiers(self._startup_context_factory)
                if inspect.isawaitable(planned):
                    await planned
            self._claiming = True
            coroutine = self._claim_loop()
            try:
                self._loop_task = asyncio.create_task(
                    coroutine,
                    name="memora-summary-scheduler",
                    context=Context(),
                )
            except Exception:
                coroutine.close()
                self._claiming = False
                raise
        await self.notify()

    async def close(self) -> None:
        """停止入队和领取，取消 worker，并对已有 token 尽力执行 CAS requeue。"""

        async with self._lifecycle_lock:
            if self._closed and self._loop_task is None and not self._workers:
                return
            self._closed = True
            self._accepting_enqueues = False
            self._claiming = False
            loop_task = self._loop_task
            self._loop_task = None
            workers = tuple(self._workers.items())
            if loop_task is not None:
                loop_task.cancel()
            for task, _claim in workers:
                task.cancel()

        await self.notify()
        if loop_task is not None:
            try:
                await loop_task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
        if workers:
            await asyncio.gather(
                *(task for task, _claim in workers),
                return_exceptions=True,
            )
        for _task, claim in workers:
            await self._try_requeue(claim, SummaryReasonCode.CANCELLED)
        async with self._condition:
            self._workers = {
                task: claim for task, claim in self._workers.items() if not task.done()
            }
            self._target_parallelism = 0
            self._condition.notify_all()

    async def recover(self) -> int:
        """幂等回收已过期 claim，并唤醒当前领取循环。"""

        if self._closed:
            return 0
        recovered = self._job_store.recover_expired_claims(self._now())
        if inspect.isawaitable(recovered):
            recovered = await recovered
            await self.notify()
        return max(0, int(recovered))

    async def cancel_session_jobs(
        self,
        session_id: str,
        epoch: int,
        reason_code: SummaryReasonCode = SummaryReasonCode.CANCELLED,
    ) -> int:
        """持久 fence 会话任务，并取消当前调度器持有的对应 worker。"""

        cancelled = self._job_store.cancel_session_jobs(
            session_id,
            epoch,
            reason_code,
        )
        if inspect.isawaitable(cancelled):
            cancelled = await cancelled
        async with self._condition:
            tasks = tuple(
                task
                for task, claim in self._workers.items()
                if claim.session_id == session_id and claim.session_epoch == epoch
            )
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.notify()
        return max(0, int(cancelled))

    async def enqueue_automatic(
        self,
        context: SummaryWindowContext,
        observed_end_seq: int,
    ) -> SummaryEnqueueResult:
        """仅在调用方边界已达到固定窗口阈值时规划自动任务。"""

        observed_end = self._validate_observed_end(observed_end_seq)
        if observed_end - context.start_seq < context.window_size:
            return await self._local_enqueue_result(SummaryReasonCode.NO_WINDOW)
        return await self._enqueue(
            replace(context, triggered_by="automatic"),
            observed_end,
        )

    async def enqueue_manual(
        self,
        context: SummaryWindowContext,
        observed_end_seq: int,
    ) -> SummaryEnqueueResult:
        """跳过自动阈值，但仍委托同一 frontier planner 追加固定窗口。"""

        return await self._enqueue(
            replace(context, triggered_by="manual"),
            self._validate_observed_end(observed_end_seq),
        )

    async def snapshot(self) -> SummaryTaskSnapshot:
        """返回 Store 累计计数与调度器 active/target 的统一安全投影。"""

        try:
            stored = await self._job_store.snapshot()
        except asyncio.CancelledError:
            raise
        except Exception:
            stored = SummaryTaskSnapshot()
        active = self.active_parallelism
        target = self._calculate_target(stored, active)
        self._target_parallelism = target
        return replace(
            stored,
            active_parallelism=active,
            target_parallelism=target,
        )

    async def notify(self) -> None:
        """递增唤醒代次并通知 due/claim condition。"""

        async with self._condition:
            self._wake_generation += 1
            self._condition.notify_all()

    async def _enqueue(
        self,
        context: SummaryWindowContext,
        observed_end_seq: int,
    ) -> SummaryEnqueueResult:
        """调用唯一 planner，并将并发字段替换为调度器安全投影。"""

        async with self._condition:
            self._known_sessions.add(context.session_id)
        if self._closed or not self._accepting_enqueues:
            return await self._local_enqueue_result(SummaryReasonCode.CANCELLED)
        try:
            result = await self._job_store.plan_and_enqueue_windows(
                context,
                observed_end_seq,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return await self._local_enqueue_result(SummaryReasonCode.STORE_UNAVAILABLE)
        await self.notify()
        snapshot = await self.snapshot()
        return replace(
            result,
            active_parallelism=snapshot.active_parallelism,
            target_parallelism=snapshot.target_parallelism,
        )

    async def _local_enqueue_result(
        self,
        reason_code: SummaryReasonCode,
    ) -> SummaryEnqueueResult:
        """为未进入 Store planner 的分支生成固定安全结果。"""
        snapshot = await self.snapshot()
        return SummaryEnqueueResult(
            accepted=False,
            active_parallelism=snapshot.active_parallelism,
            target_parallelism=snapshot.target_parallelism,
            reason_code=reason_code,
        )

    async def _claim_loop(self) -> None:
        """使用 condition 和有界 timer 持续填充可用 worker 槽位。"""
        while self._claiming:
            async with self._condition:
                generation = self._wake_generation
            try:
                recovered = self._job_store.recover_expired_claims(self._now())
                if inspect.isawaitable(recovered):
                    await recovered
                started = await self._claim_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._claiming = False
                raise
            if started:
                continue
            await self._wait_for_wake(generation)

    async def _claim_once(self) -> int:
        """领取一个确定性 round-robin 批次并只为可用槽位创建任务。"""

        async with self._condition:
            if not self._claiming:
                return 0
            available = self._max_parallel - len(self._workers)
        if available <= 0:
            return 0
        claim_ready = self._job_store.claim_ready
        claim_kwargs: dict[str, Any] = {
            "max_parallel_per_session": self._max_parallel_per_session,
            "lease_seconds": max(1, int(self._lease_seconds)),
            "session_order": self._session_order(),
        }
        if _supports_keyword(claim_ready, "global_limit"):
            claim_kwargs["global_limit"] = self._max_parallel
        claims = await claim_ready(
            self._now(), self._scheduler_id, available, **claim_kwargs
        )
        return await self._register_claims(claims)

    async def _register_claims(self, claims: Sequence[ClaimedJob]) -> int:
        """按 cursor 排序并登记每个 session 本轮至多一个有效 claim。"""

        valid_claims = [claim for claim in claims if isinstance(claim, ClaimedJob)]
        ordered, duplicate_claims = self._round_robin_order(valid_claims)
        rejected: list[ClaimedJob] = list(duplicate_claims)
        started = 0
        async with self._condition:
            session_counts: dict[str, int] = {}
            for task, active_claim in self._workers.items():
                if not task.done():
                    session_counts[active_claim.session_id] = (
                        session_counts.get(active_claim.session_id, 0) + 1
                    )
            available = max(0, self._max_parallel - len(self._workers))
            for claim in ordered:
                session_count = session_counts.get(claim.session_id, 0)
                if (
                    not self._claiming
                    or started >= available
                    or session_count >= self._max_parallel_per_session
                ):
                    rejected.append(claim)
                    continue
                coroutine = self._run_claim(claim)
                try:
                    task = asyncio.create_task(
                        coroutine,
                        name="memora-summary-worker",
                        context=Context(),
                    )
                except Exception:
                    coroutine.close()
                    rejected.append(claim)
                    continue
                self._workers[task] = claim
                session_counts[claim.session_id] = session_count + 1
                self._round_robin_cursor = claim.session_id
                started += 1
            self._condition.notify_all()
        for claim in rejected:
            reason = (
                SummaryReasonCode.CANCELLED
                if not self._claiming
                else SummaryReasonCode.RETRY_SCHEDULED
            )
            await self._try_requeue(claim, reason)
        return started

    def _round_robin_order(
        self,
        claims: Sequence[ClaimedJob],
    ) -> tuple[list[ClaimedJob], list[ClaimedJob]]:
        """选取每个 session 最早窗口，并从上次 cursor 后确定性轮转。"""

        earliest: dict[str, ClaimedJob] = {}
        duplicates: list[ClaimedJob] = []
        for claim in claims:
            previous = earliest.get(claim.session_id)
            if previous is None:
                earliest[claim.session_id] = claim
                continue
            if self._claim_sort_key(claim) < self._claim_sort_key(previous):
                duplicates.append(previous)
                earliest[claim.session_id] = claim
            else:
                duplicates.append(claim)
        sessions = sorted(earliest)
        self._known_sessions.update(sessions)
        cursor = self._round_robin_cursor
        if cursor is not None:
            sessions = [item for item in sessions if item > cursor] + [
                item for item in sessions if item <= cursor
            ]
        return [earliest[session_id] for session_id in sessions], duplicates

    def _session_order(self) -> tuple[str, ...]:
        """从已观察 session 构造下一轮稳定顺序；Store 负责补入新 session。"""

        sessions = sorted(self._known_sessions)
        cursor = self._round_robin_cursor
        if cursor is None:
            return tuple(sessions)
        return tuple(
            [item for item in sessions if item > cursor]
            + [item for item in sessions if item <= cursor]
        )

    @staticmethod
    def _claim_sort_key(claim: ClaimedJob) -> tuple[int, float, str]:
        """返回同一 session 内最早窗口的稳定排序键。"""

        return (claim.start_seq, claim.created_at, claim.job_id)

    async def _wait_for_wake(self, generation: int) -> None:
        """等待显式通知或有界 timer 到期，保证 due retry 无需新消息。"""

        async with self._condition:
            if not self._claiming or generation != self._wake_generation:
                return
            try:
                await asyncio.wait_for(
                    self._condition.wait(),
                    timeout=self._retry_poll_seconds,
                )
            except TimeoutError:
                return

    async def _run_claim(self, claim: ClaimedJob) -> None:
        """执行一个 claim；取消 requeue，普通失败写固定失败 DTO。"""

        try:
            outcome = await self._worker.execute(claim)
            try:
                await self._job_store.commit_window(claim, outcome)
            except asyncio.CancelledError:
                raise
            except Exception:
                # canonical 副作用可能已经发生；保留 running lease 给启动 reconcile。
                return
        except asyncio.CancelledError:
            await self._try_requeue(claim, SummaryReasonCode.CANCELLED)
            raise
        except SummaryWorkerFailure as failure:
            await self._try_fail(claim, failure.to_failure())
        except Exception as error:
            await self._try_fail(
                claim,
                SummaryFailure(
                    failed_stage="worker",
                    reason_code=SummaryReasonCode.UNKNOWN,
                    exception_type=error.__class__.__name__,
                    retryable=True,
                ),
            )
        finally:
            current = asyncio.current_task()
            async with self._condition:
                if current is not None:
                    self._workers.pop(current, None)
                self._wake_generation += 1
                self._condition.notify_all()

    async def _try_fail(
        self,
        claim: ClaimedJob,
        failure: SummaryFailure,
    ) -> None:
        """尽力提交 token-fenced 失败；Store 不可用时保留 lease 供恢复。"""
        try:
            call = self._job_store.fail_window
            if _supports_keyword(call, "now"):
                await call(claim, failure, now=self._now())
            else:
                await call(claim, failure)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _try_requeue(
        self,
        claim: ClaimedJob,
        reason_code: SummaryReasonCode,
    ) -> None:
        """尽力执行 token-fenced requeue，不伪造完成或失败终态。"""
        try:
            call = self._job_store.requeue_claim
            if _supports_keyword(call, "now"):
                await call(claim, reason_code, now=self._now())
            else:
                await call(claim, reason_code)
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    def _calculate_target(
        self,
        snapshot: SummaryTaskSnapshot,
        active: int,
    ) -> int:
        """从安全状态计数计算与实际 active 分离的目标并发数。"""

        pending = max(snapshot.target_parallelism, snapshot.queued)
        executable = max(active, snapshot.running) + pending
        return min(self._max_parallel, max(0, executable))

    @staticmethod
    def _validate_observed_end(value: int) -> int:
        """校验 planner 的已观测高水位为非负整数。"""

        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("observed_end_seq 必须是非负整数")
        return value

    def _now(self) -> datetime:
        """读取并校验注入时钟，再规范为带 UTC 时区的 datetime。"""

        value = self._clock()
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            timestamp = value.timestamp()
        else:
            timestamp = float(value)
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("总结调度时钟必须返回非负有限时间")
        return datetime.fromtimestamp(timestamp, timezone.utc)


__all__ = ["SummaryScheduler"]
