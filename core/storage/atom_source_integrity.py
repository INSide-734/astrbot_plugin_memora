"""MemoryAtom 父 canonical 来源的事务内校验。"""

from __future__ import annotations

import aiosqlite

from ..models.memory_atom import MemoryAtom
from .canonical_source_validation import (
    load_canonical_source_states,
    source_matches_state,
)


async def validate_atom_parent_sources(
    db: aiosqlite.Connection,
    atoms: list[MemoryAtom],
) -> None:
    """当 canonical 表存在时，拒绝缺失或陈旧的 Atom 父来源。"""

    if not atoms or not await _documents_table_exists(db):
        return
    source_ids = tuple(sorted({atom.parent_memory_id for atom in atoms}))
    states = await load_canonical_source_states(db, source_ids)
    for atom in atoms:
        state = states.get(atom.parent_memory_id)
        has_provenance = bool(
            atom.parent_revision and atom.parent_scope_key and atom.parent_privacy_level
        )
        if not has_provenance:
            # 允许旧工具向不存在或尚未建立的父 ID 写入 legacy 行；
            # documents 建立后公开读取会 fail closed，避免该行进入召回。
            if state is not None:
                raise ValueError("source_provenance_required")
            continue
        if state is None:
            raise ValueError("source_not_found")
        if atom.parent_revision != state.revision_token:
            raise ValueError("source_revision_mismatch")
        if atom.parent_scope_key != state.scope_key:
            raise ValueError("source_scope_mismatch")
        if atom.parent_privacy_level != state.privacy_level:
            raise ValueError("source_privacy_mismatch")
        if atom.session_id != state.session_id or atom.persona_id != state.persona_id:
            raise ValueError("source_scope_mismatch")


async def filter_atoms_by_current_sources(
    db: aiosqlite.Connection,
    atoms: list[MemoryAtom],
) -> list[MemoryAtom]:
    """仅保留来源三元组完整且仍与 canonical 一致的 Atom。"""

    if not atoms:
        return []
    if not await _documents_table_exists(db):
        return atoms
    source_ids = tuple(
        sorted(
            {
                atom.parent_memory_id
                for atom in atoms
                if atom.parent_revision
                and atom.parent_scope_key
                and atom.parent_privacy_level
            }
        )
    )
    if not source_ids:
        return []
    states = await load_canonical_source_states(db, source_ids)
    return [
        atom
        for atom in atoms
        if source_matches_state(
            atom.parent_revision or "",
            atom.parent_scope_key or "",
            atom.parent_privacy_level or "",
            states.get(atom.parent_memory_id),
        )
    ]


async def _documents_table_exists(db: aiosqlite.Connection) -> bool:
    """判断当前数据库是否包含 canonical documents 表。"""

    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
    )
    return await cursor.fetchone() is not None


__all__ = ["filter_atoms_by_current_sources", "validate_atom_parent_sources"]
