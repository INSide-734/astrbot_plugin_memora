"""跨领域人工对象与 canonical 派生来源契约。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from ..shared.temporal import parse_datetime, serialize_datetime
from .memory_evolution import MemorySourceRef

_PRIVACY_ORDER = {"public": 0, "shared": 1, "confidential": 2}
_DOMAIN_SOURCE_ROLES = frozenset({"primary", "supporting"})


class DomainObjectOrigin(str, Enum):
    """区分人工维护对象和带 canonical 证据的派生对象。"""

    MANUAL = "manual"
    DERIVED = "derived"


@dataclass(frozen=True, slots=True)
class DomainProvenance:
    """描述一个领域对象的来源类型和 canonical 证据集合。"""

    origin: DomainObjectOrigin = DomainObjectOrigin.MANUAL
    sources: tuple[MemorySourceRef, ...] = ()

    def __post_init__(self) -> None:
        """规范化枚举与来源，并拒绝越权或含糊的来源组合。"""

        try:
            origin = (
                self.origin
                if isinstance(self.origin, DomainObjectOrigin)
                else DomainObjectOrigin(self.origin)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("domain_origin_invalid") from exc
        sources = tuple(self.sources)
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "sources", sources)

        if origin is DomainObjectOrigin.MANUAL:
            if sources:
                raise ValueError("manual_origin_has_sources")
            return
        if not sources:
            raise ValueError("derived_sources_required")

        memory_ids = [source.memory_id for source in sources]
        if len(set(memory_ids)) != len(memory_ids):
            raise ValueError("duplicate_source_memory_id")
        scopes = {source.scope_key for source in sources}
        if len(scopes) != 1:
            raise ValueError("source_scope_mismatch")
        if any(source.source_role not in _DOMAIN_SOURCE_ROLES for source in sources):
            raise ValueError("domain_source_role_invalid")
        if sum(source.source_role == "primary" for source in sources) != 1:
            raise ValueError("primary_source_required")

    @property
    def scope_key(self) -> str | None:
        """返回派生来源的唯一可信作用域；人工对象返回 ``None``。"""

        return self.sources[0].scope_key if self.sources else None

    @property
    def privacy_level(self) -> str | None:
        """返回全部派生来源中最严格的隐私等级。"""

        if not self.sources:
            return None
        return max(
            (source.privacy_level for source in self.sources),
            key=_PRIVACY_ORDER.__getitem__,
        )

    def to_dict(self) -> dict[str, Any]:
        """返回不含 canonical 正文的 JSON 安全持久化映射。"""

        return {
            "origin": self.origin.value,
            "scope_key": self.scope_key,
            "privacy_level": self.privacy_level,
            "sources": [_source_to_dict(source) for source in self.sources],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DomainProvenance:
        """从持久化映射恢复来源契约，并重新执行全部边界校验。"""

        if not isinstance(data, Mapping):
            raise ValueError("domain_provenance_invalid")
        raw_sources = data.get("sources", ())
        if not isinstance(raw_sources, Sequence) or isinstance(
            raw_sources, (str, bytes, bytearray)
        ):
            raise ValueError("domain_sources_invalid")
        return cls(
            origin=DomainObjectOrigin(data.get("origin", "manual")),
            sources=tuple(_source_from_dict(item) for item in raw_sources),
        )


def merge_domain_provenance(
    existing: DomainProvenance | None,
    incoming: DomainProvenance,
) -> DomainProvenance:
    """合并同一领域对象的来源；任何人工权威都优先于派生来源。"""

    if existing is None:
        return incoming
    if (
        existing.origin is DomainObjectOrigin.MANUAL
        or incoming.origin is DomainObjectOrigin.MANUAL
    ):
        return DomainProvenance(DomainObjectOrigin.MANUAL)

    merged_by_id = {source.memory_id: source for source in existing.sources}
    primary_id = next(
        source.memory_id
        for source in existing.sources
        if source.source_role == "primary"
    )
    for source in incoming.sources:
        role = "primary" if source.memory_id == primary_id else "supporting"
        merged_by_id[source.memory_id] = replace(source, source_role=role)
    ordered = sorted(
        merged_by_id.values(),
        key=lambda source: (
            source.memory_id != primary_id,
            source.memory_id,
            source.revision_token,
        ),
    )
    return DomainProvenance(DomainObjectOrigin.DERIVED, tuple(ordered))


def _source_to_dict(source: MemorySourceRef) -> dict[str, Any]:
    """序列化 canonical 来源引用，同时故意排除证据正文。"""

    return {
        "memory_id": source.memory_id,
        "revision_token": source.revision_token,
        "scope_key": source.scope_key,
        "privacy_level": source.privacy_level,
        "occurred_at": serialize_datetime(source.occurred_at),
        "reference_at": serialize_datetime(source.reference_at),
        "ingested_at": serialize_datetime(source.ingested_at),
        "time_source": source.time_source,
        "time_precision": source.time_precision,
        "source_role": source.source_role,
        "valid_from": serialize_datetime(source.valid_from),
        "valid_to": serialize_datetime(source.valid_to),
    }


def _source_from_dict(data: Any) -> MemorySourceRef:
    """从单条持久化引用恢复 canonical 来源快照。"""

    if not isinstance(data, Mapping):
        raise ValueError("domain_source_invalid")
    occurred_at = parse_datetime(data.get("occurred_at"))
    if occurred_at is None:
        raise ValueError("source_occurred_at_required")
    return MemorySourceRef(
        memory_id=data.get("memory_id"),
        revision_token=str(data.get("revision_token") or ""),
        scope_key=str(data.get("scope_key") or ""),
        privacy_level=str(data.get("privacy_level") or ""),
        occurred_at=occurred_at,
        reference_at=parse_datetime(data.get("reference_at")),
        ingested_at=parse_datetime(data.get("ingested_at")),
        time_source=str(data.get("time_source") or "unknown"),
        time_precision=str(data.get("time_precision") or "unknown"),
        source_role=str(data.get("source_role") or "primary"),
        valid_from=parse_datetime(data.get("valid_from")),
        valid_to=parse_datetime(data.get("valid_to")),
    )


__all__ = [
    "DomainObjectOrigin",
    "DomainProvenance",
    "merge_domain_provenance",
]
