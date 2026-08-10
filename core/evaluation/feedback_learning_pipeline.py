"""离线反馈排序评测与 Evidence Inbox 投递编排。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from os import PathLike
from typing import Any

from ..features.learning.domain.models import (
    FeedbackSignalAggregate,
    FeedbackSignalPolicy,
)
from ..features.learning.infrastructure.feedback_learning_evidence_store import (
    FeedbackLearningEvidenceInbox,
)
from .feedback_ranking_ablation import (
    FeedbackRankingEvidenceRequest,
    FeedbackRankingReport,
    run_feedback_ranking_ablation,
)
from .retrieval_quality import EvaluationCase


async def run_feedback_ranking_evaluation_and_publish_evidence(
    data_dir: str | PathLike[str],
    cases: Sequence[EvaluationCase],
    baseline_retriever: Callable[
        [EvaluationCase, int], Sequence[Any] | Awaitable[Sequence[Any]]
    ],
    aggregate: FeedbackSignalAggregate | None,
    *,
    k: int,
    policy: FeedbackSignalPolicy | None = None,
    prerequisite_met: bool = True,
    evidence_request: FeedbackRankingEvidenceRequest | None = None,
) -> FeedbackRankingReport:
    """运行受控离线评测，并把生成的匿名 artifact 原子投递到固定 Inbox。

    Args:
        data_dir: 插件受控数据目录。
        cases: 本次配对回放的真实评测用例。
        baseline_retriever: 隔离的 baseline 检索调用。
        aggregate: 待评估的全局归并输入。
        k: 三项质量指标共享的评测深度。
        policy: 可选的固定反馈策略。
        prerequisite_met: 评测前置条件是否满足。
        evidence_request: 完整问题级配对证据请求。

    Returns:
        原始安全评测报告；存在 artifact 时已完成 Inbox 投递。

    Raises:
        asyncio.CancelledError: 评测或投递被取消时保持取消语义。
        LearningEvidenceInboxError: artifact 无法安全持久化时抛出。
    """

    report = await run_feedback_ranking_ablation(
        cases,
        baseline_retriever,
        aggregate,
        k=k,
        policy=policy,
        prerequisite_met=prerequisite_met,
        evidence_request=evidence_request,
    )
    if report.evidence_artifact is not None:
        await FeedbackLearningEvidenceInbox(data_dir).publish(report.evidence_artifact)
    return report


__all__ = ["run_feedback_ranking_evaluation_and_publish_evidence"]
