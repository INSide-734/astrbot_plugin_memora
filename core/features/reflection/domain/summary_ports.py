"""记忆总结调度与持久化之间的窄端口。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol

from .summary_models import (
    CandidateIntent,
    ClaimedJob,
    CompletionResult,
    EpochResult,
    RetryResult,
    SourceWindow,
    SummaryEnqueueResult,
    SummaryFailure,
    SummaryReasonCode,
    SummaryTaskSnapshot,
    SummaryWindowContext,
    TrimResult,
    WindowOutcome,
)


class SummaryJobStorePort(Protocol):
    """定义调度器可使用的总结任务持久化能力。"""

    def set_summary_clock(self, clock: Callable[[], datetime | float]) -> None:
        """设置任务状态比较使用的统一时钟。"""
        ...

    async def plan_existing_frontiers(
        self, context_factory: Callable[[str, int, int], SummaryWindowContext]
    ) -> int:
        """为已有会话按同一 planner 补建启动期总结窗口。"""
        ...

    async def plan_and_enqueue_windows(
        self, context: SummaryWindowContext, observed_end_seq: int
    ) -> SummaryEnqueueResult:
        """原子规划固定窗口并幂等入队。"""
        ...

    async def claim_ready(
        self,
        now: datetime,
        scheduler_id: str,
        limit: int,
        *,
        max_parallel_per_session: int = 1,
        lease_seconds: int = 120,
        session_order: Sequence[str] | None = None,
        global_limit: int | None = None,
    ) -> list[ClaimedJob]:
        """按 session 顺序并在全局容量内领取 ready 任务。"""
        ...

    async def read_claimed_window(self, claim: ClaimedJob) -> SourceWindow:
        """读取并校验 claim 固化的消息来源窗口。"""
        ...

    async def claim_is_active(self, claim: ClaimedJob) -> bool:
        """检查 claim 是否仍可执行外部副作用。"""
        ...

    async def begin_candidate_intents(
        self, claim: ClaimedJob, intents: Sequence[CandidateIntent]
    ) -> bool:
        """在候选 canonical 副作用前原子登记 slot intent。"""
        ...

    async def commit_window(
        self, claim: ClaimedJob, outcome: WindowOutcome
    ) -> CompletionResult:
        """以 claim fencing 收口候选、任务状态和连续 cursor。"""
        ...

    async def fail_window(
        self,
        claim: ClaimedJob,
        failure: SummaryFailure,
        *,
        now: datetime | float | None = None,
    ) -> RetryResult:
        """以 claim fencing 写入失败、退避或 blocked 状态。"""
        ...

    async def requeue_claim(
        self,
        claim: ClaimedJob,
        reason_code: SummaryReasonCode,
        *,
        now: datetime | float | None = None,
    ) -> bool:
        """在取消或关闭时仅重排仍属于当前 claim 的任务。"""
        ...

    async def cancel_session_jobs(
        self, session_id: str, epoch: int, reason_code: SummaryReasonCode
    ) -> int:
        """fence 指定 session epoch 的非终态任务。"""
        ...

    async def recover_expired_claims(self, now: datetime) -> int:
        """回收 lease 已过期的 running 任务。"""
        ...

    async def reset_session_epoch(
        self, session_id: str, reason_code: SummaryReasonCode
    ) -> EpochResult:
        """递增 session epoch 并取消旧 epoch 的非终态任务。"""
        ...

    async def trim_if_safe(
        self, session_id: str, epoch: int, delete_count: int
    ) -> TrimResult:
        """在任务、ledger 和隔离状态允许时原子修剪来源消息。"""
        ...

    async def has_trim_blocker(self, session_id: str, epoch: int) -> bool:
        """判断指定 epoch 是否存在阻止来源删除的任务或派生状态。"""
        ...

    async def snapshot(self) -> SummaryTaskSnapshot:
        """返回不含原始任务字段的安全总结统计。"""
        ...


__all__ = ["SummaryJobStorePort"]
