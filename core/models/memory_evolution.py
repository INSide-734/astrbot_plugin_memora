"""Domain contracts for evidence-grounded memory evolution.

The models in this module are deliberately persistence- and provider-agnostic.
They carry the small amount of local validation needed to keep malformed
proposals from crossing component boundaries; storage and policy validation
remain responsibilities of their owning components.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class RelationType(str, Enum):
    SUPPORTS = "supports"
    UPDATES = "updates"
    CONTRADICTS = "contradicts"
    SAME_EPISODE = "same_episode"
    PREFERENCE_CHANGE = "preference_change"
    CAUSES = "causes"
    SUPERSEDES = "supersedes"
    RELATED = "related"


class ProjectionType(str, Enum):
    EPISODE_SUMMARY = "episode_summary"
    PREFERENCE_STATE = "preference_state"
    RELATIONSHIP_STATE = "relationship_state"
    CONFLICT_SET = "conflict_set"


class JobState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    RETRY_WAIT = "retry_wait"
    DEAD = "dead"


class DerivedState(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    REJECTED = "rejected"


_PRIVACY_LEVELS = frozenset({"public", "shared", "confidential"})
_MAX_EVIDENCE_CHARS = 4_000


def _require_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value


def _check_confidence(value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return float(value)


def _check_interval(start: datetime | None, end: datetime | None) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError("valid_to must not precede valid_from")


@dataclass(frozen=True)
class MemorySourceRef:
    """A canonical memory snapshot used as evidence for a proposal."""

    memory_id: int
    revision_token: str
    scope_key: str
    privacy_level: str
    occurred_at: datetime
    content: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.memory_id, int) or self.memory_id < 0:
            raise ValueError("memory_id must be a non-negative integer")
        _require_text(self.revision_token, "revision_token")
        _require_text(self.scope_key, "scope_key")
        if self.privacy_level not in _PRIVACY_LEVELS:
            raise ValueError("privacy_level must be public, shared, or confidential")
        if self.content is not None:
            if not isinstance(self.content, str):
                raise ValueError("content must be a string or None")
            if len(self.content) > _MAX_EVIDENCE_CHARS:
                raise ValueError("content exceeds the evidence length limit")

    @property
    def revision(self) -> str:
        """Compatibility alias for code using the design document's name."""

        return self.revision_token


@dataclass(frozen=True)
class ScopeContext:
    scope_key: str
    privacy_level: str = "shared"

    def __post_init__(self) -> None:
        _require_text(self.scope_key, "scope_key")
        if self.privacy_level not in _PRIVACY_LEVELS:
            raise ValueError("privacy_level must be public, shared, or confidential")


@dataclass(frozen=True)
class ExpansionBudget:
    max_chars: int = 2_000
    max_items: int = 16

    def __post_init__(self) -> None:
        if self.max_chars < 0 or self.max_items < 0:
            raise ValueError("expansion budget values must be non-negative")


@dataclass(frozen=True)
class MemoryRelationProposal:
    source_alias: str
    target_alias: str
    relation_type: RelationType
    confidence: float
    rationale: str | None
    valid_from: datetime | None
    valid_to: datetime | None

    def __post_init__(self) -> None:
        _require_text(self.source_alias, "source_alias")
        _require_text(self.target_alias, "target_alias")
        if not isinstance(self.relation_type, RelationType):
            try:
                object.__setattr__(self, "relation_type", RelationType(self.relation_type))
            except (TypeError, ValueError) as exc:
                raise ValueError("unknown relation_type") from exc
        _check_confidence(self.confidence)
        _check_interval(self.valid_from, self.valid_to)


@dataclass(frozen=True)
class MemoryProjectionProposal:
    projection_type: ProjectionType
    source_aliases: tuple[str, ...]
    title: str | None
    summary: str
    confidence: float
    valid_from: datetime | None
    valid_to: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.projection_type, ProjectionType):
            try:
                object.__setattr__(self, "projection_type", ProjectionType(self.projection_type))
            except (TypeError, ValueError) as exc:
                raise ValueError("unknown projection_type") from exc
        aliases = tuple(self.source_aliases)
        if not aliases or any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
            raise ValueError("source_aliases must contain non-empty aliases")
        object.__setattr__(self, "source_aliases", aliases)
        _require_text(self.summary, "summary")
        _check_confidence(self.confidence)
        _check_interval(self.valid_from, self.valid_to)


@dataclass(frozen=True)
class EvolutionProposal:
    relations: tuple[MemoryRelationProposal, ...] = ()
    projections: tuple[MemoryProjectionProposal, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "relations", tuple(self.relations))
        object.__setattr__(self, "projections", tuple(self.projections))


@dataclass(frozen=True)
class EvolutionSignal:
    memory_id: int
    revision_token: str
    importance: float = 0.0
    scope_key: str = ""
    topic_keys: tuple[str, ...] = ()
    entity_keys: tuple[str, ...] = ()
    occurred_at: datetime | None = None
    pending_jobs: int = 0
    privacy_level: str = "shared"
    content: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.memory_id, int) or self.memory_id < 0:
            raise ValueError("memory_id must be a non-negative integer")
        _require_text(self.revision_token, "revision_token")
        _require_text(self.scope_key, "scope_key")
        _check_confidence(self.importance)
        if self.privacy_level not in _PRIVACY_LEVELS:
            raise ValueError("privacy_level must be public, shared, or confidential")
        if self.pending_jobs < 0:
            raise ValueError("pending_jobs must be non-negative")
        object.__setattr__(self, "topic_keys", tuple(self.topic_keys))
        object.__setattr__(self, "entity_keys", tuple(self.entity_keys))

    @property
    def source(self) -> MemorySourceRef:
        """Return a canonical source view for consumers that need one."""

        if self.occurred_at is None:
            raise ValueError("occurred_at is required to build a source")
        return MemorySourceRef(
            self.memory_id,
            self.revision_token,
            self.scope_key,
            self.privacy_level,
            self.occurred_at,
            self.content,
        )


@dataclass(frozen=True)
class GateDecision:
    should_enqueue: bool
    bucket_key: str | None
    reason_code: str
    deduplicated: bool = False
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.reason_code, "reason_code")


@dataclass(frozen=True)
class JobSpec:
    scope_key: str
    bucket_key: str
    source_ids: tuple[int, ...]
    idempotency_key: str
    not_before: datetime
    job_id: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.scope_key, "scope_key")
        _require_text(self.bucket_key, "bucket_key")
        _require_text(self.idempotency_key, "idempotency_key")
        source_ids = tuple(self.source_ids)
        if not source_ids:
            raise ValueError("source_ids must not be empty")
        if any(not isinstance(source_id, int) or source_id < 0 for source_id in source_ids):
            raise ValueError("source_ids must contain non-negative integers")
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source_ids must be unique")
        object.__setattr__(self, "source_ids", source_ids)


@dataclass(frozen=True)
class MemoryEvolutionJob:
    job_id: str
    scope_key: str
    bucket_key: str
    state: JobState
    attempt_count: int
    not_before: datetime
    lease_until: datetime | None
    idempotency_key: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class JobClaim:
    job_id: str
    worker_token: str
    scope_key: str
    bucket_key: str
    source_ids: tuple[int, ...] = ()
    attempt_count: int = 0
    lease_until: datetime | None = None


@dataclass(frozen=True)
class RetrySpec:
    not_before: datetime
    attempt_count: int
    reason_code: str


@dataclass(frozen=True)
class RelationView:
    relation_id: str
    source_memory_id: int
    target_memory_id: int
    relation_type: RelationType
    confidence: float
    scope_key: str
    privacy_level: str
    state: DerivedState = DerivedState.ACTIVE
    source_revision: str | None = None
    target_revision: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.relation_id, "relation_id")
        if not isinstance(self.relation_type, RelationType):
            try:
                object.__setattr__(self, "relation_type", RelationType(self.relation_type))
            except (TypeError, ValueError) as exc:
                raise ValueError("unknown relation_type") from exc
        if not isinstance(self.state, DerivedState):
            try:
                object.__setattr__(self, "state", DerivedState(self.state))
            except (TypeError, ValueError) as exc:
                raise ValueError("unknown derived state") from exc
        _check_confidence(self.confidence)
        if self.privacy_level not in _PRIVACY_LEVELS:
            raise ValueError("privacy_level must be public, shared, or confidential")
        _check_interval(self.valid_from, self.valid_to)


@dataclass(frozen=True)
class ProjectionView:
    projection_id: str
    projection_type: ProjectionType
    summary: str
    source_memory_ids: tuple[int, ...]
    scope_key: str
    privacy_level: str
    confidence: float
    state: DerivedState = DerivedState.ACTIVE
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.projection_id, "projection_id")
        _require_text(self.summary, "summary")
        object.__setattr__(self, "source_memory_ids", tuple(self.source_memory_ids))
        if not self.source_memory_ids:
            raise ValueError("projection must have at least one source")
        if not isinstance(self.projection_type, ProjectionType):
            try:
                object.__setattr__(self, "projection_type", ProjectionType(self.projection_type))
            except (TypeError, ValueError) as exc:
                raise ValueError("unknown projection_type") from exc
        if not isinstance(self.state, DerivedState):
            try:
                object.__setattr__(self, "state", DerivedState(self.state))
            except (TypeError, ValueError) as exc:
                raise ValueError("unknown derived state") from exc
        _check_confidence(self.confidence)
        if self.privacy_level not in _PRIVACY_LEVELS:
            raise ValueError("privacy_level must be public, shared, or confidential")
        _check_interval(self.valid_from, self.valid_to)


@dataclass(frozen=True)
class DerivedApplyPlan:
    relations: tuple[RelationView, ...] = ()
    projections: tuple[ProjectionView, ...] = ()
    projection_sources: tuple["ProjectionSourceView", ...] = ()
    source_revisions: dict[int, str] = field(default_factory=dict)
    reason_code: str = "accepted"

    def __post_init__(self) -> None:
        object.__setattr__(self, "relations", tuple(self.relations))
        object.__setattr__(self, "projections", tuple(self.projections))
        object.__setattr__(self, "projection_sources", tuple(self.projection_sources))
        object.__setattr__(self, "source_revisions", dict(self.source_revisions))


@dataclass(frozen=True)
class ProjectionSourceView:
    projection_id: str
    memory_id: int
    revision_token: str
    role: str = "supporting"
    ordinal: int = 0

    def __post_init__(self) -> None:
        _require_text(self.projection_id, "projection_id")
        _require_text(self.revision_token, "revision_token")
        if self.role not in {"primary", "supporting", "conflict_left", "conflict_right"}:
            raise ValueError("unknown projection source role")
        if self.ordinal < 0:
            raise ValueError("ordinal must be non-negative")


__all__ = [
    "DerivedApplyPlan",
    "DerivedState",
    "EvolutionProposal",
    "EvolutionSignal",
    "ExpansionBudget",
    "GateDecision",
    "JobClaim",
    "JobSpec",
    "JobState",
    "MemoryProjectionProposal",
    "MemoryEvolutionJob",
    "MemoryRelationProposal",
    "MemorySourceRef",
    "ProjectionType",
    "ProjectionView",
    "ProjectionSourceView",
    "RelationType",
    "RelationView",
    "RetrySpec",
    "ScopeContext",
]
