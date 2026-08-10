"""反思 feature 的应用服务。"""

from .candidate_writer import (
    build_reflection_idempotency_key,
    store_reflection_candidates,
)
from .continuity import record_continuity_topics, resolve_continuity_session
from .reflection_metadata import commit_summary_metadata, persist_pending_summary
from .reflection_trigger import ReflectionTrigger, ReflectionWindowRequest

__all__ = [
    "build_reflection_idempotency_key",
    "commit_summary_metadata",
    "persist_pending_summary",
    "record_continuity_topics",
    "ReflectionTrigger",
    "ReflectionWindowRequest",
    "resolve_continuity_session",
    "store_reflection_candidates",
]
