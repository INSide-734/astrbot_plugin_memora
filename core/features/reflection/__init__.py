"""反思 feature 的公开边界。"""

from .application import (
    build_reflection_idempotency_key,
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
    "build_reflection_idempotency_key",
    "record_continuity_topics",
    "resolve_continuity_session",
    "store_reflection_candidates",
    "summarize_store_results",
]
