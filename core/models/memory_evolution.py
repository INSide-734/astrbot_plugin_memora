"""证据锚定记忆演化的领域契约。

本模块的模型不依赖持久化和 Provider，只承担阻止格式错误的 proposal
跨越组件边界所需的最小本地校验；存储一致性和策略校验由各自组件负责。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .temporal import normalize_datetime, validate_time_labels


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
    INVALIDATED = "invalidated"
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
    start = normalize_datetime(start)
    end = normalize_datetime(end)
    if start is not None and end is not None and end < start:
        raise ValueError("valid_to must not precede valid_from")


@dataclass(frozen=True)
class MemorySourceRef:
    """作为 proposal 证据的 canonical memory 快照。"""

    memory_id: int
    revision_token: str
    scope_key: str
    privacy_level: str
    occurred_at: datetime
    content: str | None = None
    reference_at: datetime | None = None
    ingested_at: datetime | None = None
    time_source: str = "unknown"
    time_precision: str = "unknown"

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
        object.__setattr__(self, "occurred_at", normalize_datetime(self.occurred_at))
        if self.reference_at is None:
            object.__setattr__(self, "reference_at", self.occurred_at)
        else:
            object.__setattr__(self, "reference_at", normalize_datetime(self.reference_at))
        object.__setattr__(self, "ingested_at", normalize_datetime(self.ingested_at))
        validate_time_labels(self.time_source, self.time_precision)

    @property
    def revision(self) -> str:
        """为沿用设计文档命名的调用方提供兼容别名。"""

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
        """为需要 source 视图的调用方返回 canonical source。"""

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
    source_revisions: dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.scope_key, "scope_key")
        _require_text(self.bucket_key, "bucket_key")
        _require_text(self.idempotency_key, "idempotency_key")
        source_ids = tuple(self.source_ids)
        if not source_ids:
            raise ValueError("source_ids must not be empty")
        if any(
            not isinstance(source_id, int)
            or isinstance(source_id, bool)
            or source_id < 0
            for source_id in source_ids
        ):
            raise ValueError("source_ids must contain non-negative integers")
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source_ids must be unique")
        object.__setattr__(self, "source_ids", source_ids)
        revisions: dict[int, str] = {}
        for memory_id, revision in dict(self.source_revisions).items():
            if (
                not isinstance(memory_id, int)
                or isinstance(memory_id, bool)
                or memory_id < 0
            ):
                raise ValueError("source_revisions keys must be non-negative integers")
            revisions[memory_id] = str(revision).strip()
        if any(memory_id not in source_ids for memory_id in revisions):
            raise ValueError("source_revisions must reference source_ids")
        if any(not revision for revision in revisions.values()):
            raise ValueError("source_revisions values must be non-empty")
        object.__setattr__(self, "source_revisions", revisions)


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
    source_revisions: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class JobClaim:
    job_id: str
    worker_token: str
    scope_key: str
    bucket_key: str
    source_ids: tuple[int, ...] = ()
    attempt_count: int = 0
    lease_until: datetime | None = None
    source_revisions: dict[int, str] = field(default_factory=dict)


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
    reference_at: datetime | None = None
    discovered_at: datetime | None = None
    invalid_at: datetime | None = None
    time_source: str = "unknown"
    time_precision: str = "unknown"

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
        object.__setattr__(self, "valid_from", normalize_datetime(self.valid_from))
        object.__setattr__(self, "valid_to", normalize_datetime(self.valid_to))
        object.__setattr__(self, "reference_at", normalize_datetime(self.reference_at))
        object.__setattr__(self, "discovered_at", normalize_datetime(self.discovered_at))
        object.__setattr__(self, "invalid_at", normalize_datetime(self.invalid_at))
        validate_time_labels(self.time_source, self.time_precision)


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
    reference_at: datetime | None = None
    discovered_at: datetime | None = None
    invalid_at: datetime | None = None
    time_source: str = "unknown"
    time_precision: str = "unknown"

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
        object.__setattr__(self, "valid_from", normalize_datetime(self.valid_from))
        object.__setattr__(self, "valid_to", normalize_datetime(self.valid_to))
        object.__setattr__(self, "reference_at", normalize_datetime(self.reference_at))
        object.__setattr__(self, "discovered_at", normalize_datetime(self.discovered_at))
        object.__setattr__(self, "invalid_at", normalize_datetime(self.invalid_at))
        validate_time_labels(self.time_source, self.time_precision)


@dataclass(frozen=True)
class DerivedApplyPlan:
    relations: tuple[RelationView, ...] = ()
    projections: tuple[ProjectionView, ...] = ()
    projection_sources: tuple["ProjectionSourceView", ...] = ()
    source_revisions: dict[int, str] = field(default_factory=dict)
    reason_code: str = "accepted"
    origin_job_id: str | None = None

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
    occurred_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        _require_text(self.projection_id, "projection_id")
        _require_text(self.revision_token, "revision_token")
        if self.role not in {"primary", "supporting", "conflict_left", "conflict_right"}:
            raise ValueError("unknown projection source role")
        if self.ordinal < 0:
            raise ValueError("ordinal must be non-negative")
        _check_interval(self.valid_from, self.valid_to)
        object.__setattr__(self, "occurred_at", normalize_datetime(self.occurred_at))
        object.__setattr__(self, "valid_from", normalize_datetime(self.valid_from))
        object.__setattr__(self, "valid_to", normalize_datetime(self.valid_to))


@dataclass(frozen=True, slots=True)
class ProjectionBundle:
    """读取侧使用的 projection 与 source mapping 组合。"""

    projection: ProjectionView
    sources: tuple[ProjectionSourceView, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        if not self.sources:
            raise ValueError("projection bundle must include source mappings")
        projection_id = self.projection.projection_id
        if any(source.projection_id != projection_id for source in self.sources):
            raise ValueError("projection bundle source ids must match projection")


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
    "ProjectionBundle",
    "ProjectionView",
    "ProjectionSourceView",
    "RelationType",
    "RelationView",
    "RetrySpec",
    "ScopeContext",
]
