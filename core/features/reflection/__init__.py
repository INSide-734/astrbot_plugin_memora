"""反思 feature 的惰性公开边界。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .application import (
        ExtraLlmBudgetDenied,
        ReflectionTrigger,
        ReflectionWindowRequest,
        TopicBatchPreparer,
        build_reflection_idempotency_key,
        fit_batches_to_extra_llm_budget,
        process_reflection_batches,
        record_continuity_topics,
        resolve_continuity_session,
        store_reflection_candidates,
    )
    from .domain import (
        ReflectionStoreOutcome,
        ReflectionStoreResult,
        ReflectionStoreSummary,
        summarize_store_results,
    )

__all__ = [
    "ReflectionStoreOutcome",
    "ReflectionStoreResult",
    "ReflectionStoreSummary",
    "ReflectionTrigger",
    "ReflectionWindowRequest",
    "TopicBatchPreparer",
    "ExtraLlmBudgetDenied",
    "build_reflection_idempotency_key",
    "fit_batches_to_extra_llm_budget",
    "process_reflection_batches",
    "record_continuity_topics",
    "resolve_continuity_session",
    "store_reflection_candidates",
    "summarize_store_results",
]

_APPLICATION_EXPORTS = frozenset(
    {
        "ExtraLlmBudgetDenied",
        "ReflectionTrigger",
        "ReflectionWindowRequest",
        "TopicBatchPreparer",
        "build_reflection_idempotency_key",
        "fit_batches_to_extra_llm_budget",
        "process_reflection_batches",
        "record_continuity_topics",
        "resolve_continuity_session",
        "store_reflection_candidates",
    }
)


def __getattr__(name: str) -> Any:
    """首次访问公开符号时从对应 reflection 分层延迟导入。

    参数：
        name: 待解析的包级公开符号名。

    返回：
        真实 owner 模块中的符号对象。

    异常：
        AttributeError: 名称不属于公开 feature 边界。
    """

    if name in _APPLICATION_EXPORTS:
        module_name = ".application"
    elif name in __all__:
        module_name = ".domain"
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
