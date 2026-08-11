"""记忆演化 feature 的惰性公开边界。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .application import (
        EvolutionLeaseLost,
        EvolutionProposalRejected,
        MemoryEvolutionManager,
    )
    from .domain import (
        DerivedApplyPlan,
        DerivedState,
        EvolutionProposal,
        EvolutionSignal,
        ExpansionBudget,
        GateDecision,
        JobClaim,
        JobSpec,
        JobState,
        MemoryEvolutionJob,
        MemoryProjectionProposal,
        MemoryRelationProposal,
        MemorySourceRef,
        ProjectionBundle,
        ProjectionSourceView,
        ProjectionType,
        ProjectionView,
        RelationType,
        RelationView,
        RetrySpec,
        ScopeContext,
    )
    from .infrastructure import (
        DerivedReviewConflictError,
        DerivedReviewNotAllowedError,
        DerivedReviewNotFoundError,
        DerivedReviewSourceError,
        MemoryEvolutionStore,
    )

__all__ = [
    "DerivedApplyPlan",
    "DerivedReviewConflictError",
    "DerivedReviewNotAllowedError",
    "DerivedReviewNotFoundError",
    "DerivedReviewSourceError",
    "DerivedState",
    "EvolutionLeaseLost",
    "EvolutionProposal",
    "EvolutionProposalRejected",
    "EvolutionSignal",
    "ExpansionBudget",
    "GateDecision",
    "JobClaim",
    "JobSpec",
    "JobState",
    "MemoryProjectionProposal",
    "MemoryEvolutionJob",
    "MemoryEvolutionManager",
    "MemoryEvolutionStore",
    "MemoryRelationProposal",
    "MemorySourceRef",
    "ProjectionType",
    "ProjectionBundle",
    "ProjectionView",
    "ProjectionSourceView",
    "RelationType",
    "RelationView",
    "RetrySpec",
    "ScopeContext",
]

_APPLICATION_EXPORTS = frozenset(
    {
        "EvolutionLeaseLost",
        "EvolutionProposalRejected",
        "MemoryEvolutionManager",
    }
)
_INFRASTRUCTURE_EXPORTS = frozenset(
    {
        "DerivedReviewConflictError",
        "DerivedReviewNotAllowedError",
        "DerivedReviewNotFoundError",
        "DerivedReviewSourceError",
        "MemoryEvolutionStore",
    }
)


def __getattr__(name: str) -> Any:
    """首次访问公开符号时从对应 feature 层延迟导入。

    参数：
        name: 待解析的包级公开符号名。

    返回：
        真实 owner 模块中的符号对象。

    异常：
        AttributeError: 名称不属于公开 feature 边界。
    """

    if name in _APPLICATION_EXPORTS:
        module_name = ".application"
    elif name in _INFRASTRUCTURE_EXPORTS:
        module_name = ".infrastructure"
    elif name in __all__:
        module_name = ".domain"
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
