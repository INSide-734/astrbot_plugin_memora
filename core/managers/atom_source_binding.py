"""把 MemoryAtom 绑定到创建时 canonical source 快照。"""

from __future__ import annotations

import json
from typing import Any, Iterable

from ..models.memory_atom import MemoryAtom
from .memory_engine_evolution_hooks import memory_revision


def bind_atoms_to_canonical_source(
    atoms: Iterable[MemoryAtom],
    memory: dict[str, Any] | None,
    *,
    fallback_metadata: dict[str, Any] | None = None,
) -> list[MemoryAtom]:
    """为原子写入稳定父 ID、revision、scope 与 privacy。"""

    (
        memory_id,
        revision_token,
        scope_key,
        privacy_level,
        session_id,
        persona_id,
    ) = _canonical_source_values(
        memory,
        fallback_metadata=fallback_metadata,
    )

    bound: list[MemoryAtom] = []
    for atom in atoms:
        atom.parent_memory_id = memory_id
        atom.parent_revision = revision_token
        atom.parent_scope_key = scope_key
        atom.parent_privacy_level = privacy_level
        atom.session_id = session_id
        atom.persona_id = persona_id
        bound.append(atom)
    return bound


def validate_bound_atoms_match_canonical_source(
    atoms: Iterable[MemoryAtom],
    memory: dict[str, Any] | None,
    *,
    fallback_metadata: dict[str, Any] | None = None,
) -> None:
    """拒绝把带旧父快照的修复载荷重放到新 canonical revision。"""

    atom_list = list(atoms)
    if not atom_list:
        return
    has_any_source = any(
        atom.parent_revision or atom.parent_scope_key or atom.parent_privacy_level
        for atom in atom_list
    )
    if not has_any_source:
        return
    (
        memory_id,
        revision_token,
        scope_key,
        privacy_level,
        session_id,
        persona_id,
    ) = _canonical_source_values(
        memory,
        fallback_metadata=fallback_metadata,
    )
    for atom in atom_list:
        if not (
            atom.parent_revision and atom.parent_scope_key and atom.parent_privacy_level
        ):
            raise ValueError("source_provenance_required")
        if atom.parent_memory_id != memory_id:
            raise ValueError("source_not_found")
        if atom.parent_revision != revision_token:
            raise ValueError("source_revision_mismatch")
        if atom.parent_scope_key != scope_key:
            raise ValueError("source_scope_mismatch")
        if atom.parent_privacy_level != privacy_level:
            raise ValueError("source_privacy_mismatch")
        if atom.session_id != session_id or atom.persona_id != persona_id:
            raise ValueError("source_scope_mismatch")


def _canonical_source_values(
    memory: dict[str, Any] | None,
    *,
    fallback_metadata: dict[str, Any] | None,
) -> tuple[int, str, str, str, str | None, str | None]:
    """从 canonical 读取结果生成稳定父来源四元组。"""

    if memory is None:
        raise ValueError("source_not_found")
    memory_id = int(memory.get("id") or 0)
    revision_token = memory_revision(memory)
    if memory_id <= 0:
        raise ValueError("source_not_found")
    if not revision_token:
        raise ValueError("source_revision_unavailable")
    metadata = dict(fallback_metadata or {})
    metadata.update(_metadata_dict(memory.get("metadata")))
    raw_scope = (
        metadata.get("scope_key")
        or metadata.get("session_id")
        or metadata.get("persona_id")
    )
    if raw_scope is None:
        raise ValueError("source_scope_missing")
    scope_key = str(raw_scope)
    raw_privacy = metadata.get("privacy_level")
    if raw_privacy not in {"public", "shared", "confidential"}:
        raise ValueError("source_privacy_missing")
    privacy_level = str(raw_privacy)
    session_id = (
        str(metadata.get("session_id"))
        if metadata.get("session_id") is not None
        else None
    )
    persona_id = (
        str(metadata.get("persona_id"))
        if metadata.get("persona_id") is not None
        else None
    )
    return (
        memory_id,
        revision_token,
        scope_key,
        privacy_level,
        session_id,
        persona_id,
    )


def _metadata_dict(value: Any) -> dict[str, Any]:
    """把 canonical metadata 安全解析为字典。"""

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
    "bind_atoms_to_canonical_source",
    "validate_bound_atoms_match_canonical_source",
]
