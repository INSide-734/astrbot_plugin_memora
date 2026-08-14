"""领域派生写入使用的 canonical source 当前状态校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import aiosqlite

from ....shared.domain_provenance import DomainObjectOrigin, DomainProvenance


@dataclass(frozen=True, slots=True)
class CanonicalSourceState:
    """当前 canonical source 的最小校验快照。"""

    memory_id: int
    revision_token: str
    scope_key: str | None
    privacy_level: str | None
    session_id: str | None
    persona_id: str | None


async def load_canonical_source_states(
    db: aiosqlite.Connection,
    source_ids: tuple[int, ...],
) -> dict[int, CanonicalSourceState]:
    """在调用方事务中读取 source 的当前 revision、scope 与 privacy。"""

    if not source_ids:
        return {}
    placeholders = ",".join("?" for _ in source_ids)
    try:
        cursor = await db.execute(
            "SELECT id, metadata, created_at, updated_at FROM documents "
            f"WHERE id IN ({placeholders})",
            source_ids,
        )
        rows = await cursor.fetchall()
    except aiosqlite.OperationalError as exc:
        raise RuntimeError("source_validation_unavailable") from exc

    states: dict[int, CanonicalSourceState] = {}
    for row in rows:
        metadata = _metadata_dict(row[1])
        raw_scope = (
            metadata.get("scope_key")
            or metadata.get("session_id")
            or metadata.get("persona_id")
        )
        scope_key = str(raw_scope) if raw_scope is not None else None
        raw_privacy = metadata.get("privacy_level")
        privacy_level = (
            str(raw_privacy)
            if raw_privacy in {"public", "shared", "confidential"}
            else None
        )
        states[int(row[0])] = CanonicalSourceState(
            memory_id=int(row[0]),
            revision_token=str(row[3] or row[2] or "").strip(),
            scope_key=scope_key,
            privacy_level=privacy_level,
            session_id=(
                str(metadata.get("session_id"))
                if metadata.get("session_id") is not None
                else None
            ),
            persona_id=(
                str(metadata.get("persona_id"))
                if metadata.get("persona_id") is not None
                else None
            ),
        )
    return states


def source_matches_state(
    revision_token: str,
    scope_key: str,
    privacy_level: str,
    state: CanonicalSourceState | None,
) -> bool:
    """判断派生对象保存的 source 快照是否仍与当前 canonical 一致。"""

    return bool(
        state is not None
        and revision_token == state.revision_token
        and scope_key == state.scope_key
        and privacy_level == state.privacy_level
    )


async def validate_domain_provenance(
    db: aiosqlite.Connection,
    provenance: DomainProvenance,
) -> None:
    """在调用方事务中重新核对 source ID、revision、scope 与 privacy。"""

    if provenance.origin is DomainObjectOrigin.MANUAL:
        return
    source_ids = tuple(source.memory_id for source in provenance.sources)
    current_by_id = await load_canonical_source_states(db, source_ids)
    for source in provenance.sources:
        current = current_by_id.get(source.memory_id)
        if current is None:
            raise ValueError("source_not_found")
        if source.revision_token != current.revision_token:
            raise ValueError("source_revision_mismatch")
        if current.scope_key is None:
            raise ValueError("source_scope_missing")
        if source.scope_key != current.scope_key:
            raise ValueError("source_scope_mismatch")
        if current.privacy_level is None:
            raise ValueError("source_privacy_missing")
        if source.privacy_level != current.privacy_level:
            raise ValueError("source_privacy_mismatch")


def _metadata_dict(value: Any) -> dict[str, Any]:
    """把 canonical metadata 规范化为字典，损坏值按空映射处理。"""

    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


__all__ = [
    "CanonicalSourceState",
    "load_canonical_source_states",
    "source_matches_state",
    "validate_domain_provenance",
]
