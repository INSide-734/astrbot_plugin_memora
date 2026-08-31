"""持久化记忆总结任务的有界调度与 worker 编排。"""

from __future__ import annotations

import asyncio
import inspect
import math
import uuid
from collections.abc import Awaitable, Callable, Sequence
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
    WindowOutcome,
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


def _is_async_callable(call: object) -> bool:
    """判断依赖是否实现了约定的异步 Store 端口。"""
    return inspect.iscoroutinefunction(call) or inspect.iscoroutinefunction(
        getattr(call, "__call__", None)
    )


async def _startup_call(
    call: object,
    *args: object,
    failure_code: str,
) -> object:
    """执行启动期 Store 操作，并把异常收敛为固定失败语义。"""
    if not callable(call):
        raise RuntimeError(failure_code)
    try:
        result = call(*args)
        if not inspect.isawaitable(result):
            raise TypeError("startup_store_call_not_awaitable")
        return await result
    except asyncio.CancelledError:
        raise
    except RuntimeError as error:
        # Store 已经给出的固定恢复码是可观察契约，不再包一层改变语义。
        if str(error) in {
            "summary_recovery_failed",
            "summary_startup_planning_failed",
            "summary_scheduler_unavailable",
        }:
            raise
        raise RuntimeError(failure_code) from error
    except Exception as error:
        raise RuntimeError(failure_code) from error


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
        startup_context_factory: Callable[
            [str, int, int], SummaryWindowContext | Awaitable[SummaryWindowContext]
        ]
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
        """严格完成恢复和启动扫描后再发布领取循环。"""

        async with self._lifecycle_lock:
            if self._closed or self._loop_task is not None:
                return

            set_clock = getattr(self._job_store, "set_summary_clock", None)
            claim_ready = getattr(self._job_store, "claim_ready", None)
            recover_claims = getattr(self._job_store, "recover_expired_claims", None)
            migrate_legacy = getattr(self._job_store, "recover_legacy_pending", None)
            plan_frontiers = getattr(self._job_store, "plan_existing_frontiers", None)
            if not callable(set_clock) or not _is_async_callable(claim_ready):
                raise RuntimeError("summary_scheduler_unavailable")
            if (
                not all(
                    callable(method) and _is_async_callable(method)
                    for method in (recover_claims, migrate_legacy, plan_frontiers)
                )
                or self._startup_context_factory is None
            ):
                raise RuntimeError("summary_recovery_failed")

            try:
                set_clock(self._now)
                recovered = await _startup_call(
                    recover_claims,
                    self._now(),
                    failure_code="summary_recovery_failed",
                )
                migrated = await _startup_call(
                    migrate_legacy,
                    failure_code="summary_recovery_failed",
                )
                planned = await _startup_call(
                    plan_frontiers,
                    self._startup_context_factory,
                    failure_code="summary_recovery_failed",
                )
            except asyncio.CancelledError:
                raise
            except RuntimeError:
                self._claiming = False
                raise
            except Exception as error:
                self._claiming = False
                raise RuntimeError("summary_recovery_failed") from error

            for value in (recovered, migrated, planned):
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    self._claiming = False
                    raise RuntimeError("summary_recovery_failed")

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
        """返回 Store 全局有效 lease 与调度器目标的安全投影。"""
        try:
            stored = await self._job_store.snapshot()
        except asyncio.CancelledError:
            raise
        except Exception:
            stored = SummaryTaskSnapshot()
        local_active = self.active_parallelism
        active = max(local_active, max(0, int(stored.active_parallelism)))
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
            if outcome.failed_count > 0 and outcome.unknown_count == 0:
                await self._try_fail(
                    claim,
                    SummaryFailure(
                        failed_stage=outcome.failed_stage or "candidate_write",
                        reason_code=SummaryReasonCode.RETRY_SCHEDULED,
                        exception_type="CandidateWriteFailed",
                        retryable=True,
                    ),
                )
                return
            try:
                committed = await self._job_store.commit_window(claim, outcome)
                if committed.accepted:
                    return
                await self._reconcile_commit_failure(claim, outcome)
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

    async def _reconcile_commit_failure(
        self, claim: ClaimedJob, outcome: WindowOutcome
    ) -> bool:
        """在窗口提交失败后用已有 canonical ID 尝试一次保守收口。"""
        reconcile = getattr(self._job_store, "reconcile_window", None)
        if not callable(reconcile):
            return False
        mapping: dict[object, object] = {}
        for intent in getattr(outcome, "candidate_slots", ()):
            canonical_id = getattr(intent, "canonical_id", None)
            if (
                isinstance(canonical_id, int)
                and not isinstance(canonical_id, bool)
                and canonical_id > 0
            ):
                mapping[getattr(intent, "slot", None)] = canonical_id
        try:
            runner = getattr(self._job_store, "run_claim_side_effect", None)
            if not callable(runner):
                return False

            async def _reconcile() -> object:
                """在 claim/source fence 内核对已落地的 canonical owner。"""

                result = reconcile(claim, mapping)
                if inspect.isawaitable(result):
                    return await result
                return result

            recovered = runner(claim, _reconcile)
            if inspect.isawaitable(recovered):
                recovered = await recovered
            status = getattr(getattr(recovered, "status", None), "value", None)
            if not getattr(recovered, "accepted", False):
                return False
            if status in {"completed", "unknown"}:
                return True
            retried = await self._job_store.commit_window(claim, outcome)
            return bool(getattr(retried, "accepted", False))
        except asyncio.CancelledError:
            raise
        except Exception:
            return False

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
