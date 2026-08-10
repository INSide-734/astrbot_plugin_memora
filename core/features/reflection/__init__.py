"""反思 feature 的公开边界。"""

from .application import (
    ReflectionTrigger,
    ReflectionWindowRequest,
    build_reflection_idempotency_key,
    commit_summary_metadata,
    persist_pending_summary,
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
    "build_reflection_idempotency_key",
    "commit_summary_metadata",
    "persist_pending_summary",
    "record_continuity_topics",
    "resolve_continuity_session",
    "store_reflection_candidates",
    "summarize_store_results",
]
