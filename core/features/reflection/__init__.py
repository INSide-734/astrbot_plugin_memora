"""反思 feature 的公开边界。"""

from .application import (
    ExtraLlmBudgetDenied,
    ReflectionTrigger,
    ReflectionWindowRequest,
    build_reflection_idempotency_key,
    commit_summary_metadata,
    fit_batches_to_extra_llm_budget,
    persist_pending_summary,
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
    "ExtraLlmBudgetDenied",
    "build_reflection_idempotency_key",
    "commit_summary_metadata",
    "fit_batches_to_extra_llm_budget",
    "persist_pending_summary",
    "process_reflection_batches",
    "record_continuity_topics",
    "resolve_continuity_session",
    "store_reflection_candidates",
    "summarize_store_results",
]
