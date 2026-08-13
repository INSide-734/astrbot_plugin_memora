"""Knowledge、Note 等领域对象的 canonical 来源校验。"""

from __future__ import annotations

from copy import copy
from datetime import datetime, timezone
from typing import Any, Iterable, TypeVar

import aiosqlite

from ....models.domain_provenance import DomainObjectOrigin, DomainProvenance
from .canonical_source_validation import (
    load_canonical_source_states,
    source_matches_state,
    validate_domain_provenance,
)

_T = TypeVar("_T")


async def validate_domain_object_write(
    db: aiosqlite.Connection,
    origin: DomainObjectOrigin,
    provenance: DomainProvenance | None,
) -> None:
    """在领域对象写事务中验证 derived provenance。"""

    if origin is DomainObjectOrigin.MANUAL:
        return
    if provenance is None:
        raise ValueError("source_provenance_required")
    await validate_domain_provenance(db, provenance)


async def filter_current_domain_objects(
    db: aiosqlite.Connection,
    objects: Iterable[_T],
) -> list[_T]:
    """保留人工对象及全部 canonical 来源仍有效的派生对象。"""

    object_list = list(objects)
    manual: list[_T] = []
    derived: list[_T] = []
    for item in object_list:
        if _origin(item) is DomainObjectOrigin.MANUAL:
            manual.append(item)
        else:
            derived.append(item)
    if not derived:
        return object_list
    source_ids = tuple(
        sorted(
            {
                source.memory_id
                for item in derived
                for source in _provenance(item).sources
            }
        )
    )
    try:
        states = await load_canonical_source_states(db, source_ids)
    except RuntimeError:
        return manual

    current = datetime.now(timezone.utc)
    visible = list(manual)
    for item in derived:
        provenance = _provenance(item)
        valid_sources = tuple(
            source
            for source in provenance.sources
            if source_matches_state(
                source.revision_token,
                source.scope_key,
                source.privacy_level,
                states.get(source.memory_id),
            )
            and (source.valid_from is None or source.valid_from <= current)
            and (source.valid_to is None or current < source.valid_to)
        )
        primary_id = next(
            source.memory_id
            for source in provenance.sources
            if source.source_role == "primary"
        )
        if not any(source.memory_id == primary_id for source in valid_sources):
            continue
        if len(valid_sources) != len(provenance.sources):
            valid_sources = tuple(
                source if source.source_role == "primary" else source
                for source in valid_sources
            )
            refreshed = copy(item)
            refreshed.provenance = DomainProvenance(
                DomainObjectOrigin.DERIVED,
                valid_sources,
            )
            if hasattr(refreshed, "source_ids"):
                refreshed.source_ids = [source.memory_id for source in valid_sources]
            if hasattr(refreshed, "source_memory_ids"):
                refreshed.source_memory_ids = [
                    source.memory_id for source in valid_sources
                ]
            item = refreshed
        visible.append(item)
    return visible


def _provenance(item: Any) -> DomainProvenance:
    """读取已由领域模型保证存在的 derived provenance。"""

    provenance = getattr(item, "provenance", None)
    if not isinstance(provenance, DomainProvenance):
        raise ValueError("source_provenance_required")
    return provenance


def _origin(item: Any) -> DomainObjectOrigin:
    """优先读取模型 origin，兼容仅保存 provenance 的画像字段。"""

    origin = getattr(item, "origin", None)
    if isinstance(origin, DomainObjectOrigin):
        return origin
    provenance = getattr(item, "provenance", None)
    if isinstance(provenance, DomainProvenance):
        return provenance.origin
    return DomainObjectOrigin.MANUAL


__all__ = ["filter_current_domain_objects", "validate_domain_object_write"]
