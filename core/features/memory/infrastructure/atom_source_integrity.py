"""MemoryAtom 父 canonical 来源的事务内校验。"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from ..application.fact_text_alignment import (
    FactTextAlignment,
    facts_aligned,
    normalize_fact,
)
from ..domain.memory_atom import MemoryAtom, has_user_source_evidence
from .canonical_source_validation import (
    load_canonical_source_states,
    source_matches_state,
)


def current_canonical_facts(content: Any, metadata: Any) -> dict[str, str]:
    """返回当前 canonical 仍对齐的事实集合：规范化条目 → 条目原文。

    事实表示（``key_facts`` + ``fact_source_evidence``）必须与当前正文一致才
    可用；缺失、结构不完整或条目已不在正文中时返回空映射，调用方按「无事实
    元数据」处理，不得消费残留事实。``metadata`` 接受字典或 JSON 字符串。
    """

    parsed = _metadata_dict(metadata)
    facts = parsed.get("key_facts")
    if not isinstance(facts, list) or (
        facts_aligned(content, facts, parsed.get("fact_source_evidence"))
        is not FactTextAlignment.ALIGNED
    ):
        return {}
    lookup: dict[str, str] = {}
    for fact in facts:
        normalized = normalize_fact(fact)
        if normalized:
            lookup.setdefault(normalized, fact)
    return lookup


async def load_canonical_documents(
    db: aiosqlite.Connection,
    source_ids: tuple[int, ...],
) -> dict[int, dict[str, Any]]:
    """读取父 canonical 行（正文、metadata 与时间字段），供事实校验与重派生使用。"""

    if not source_ids:
        return {}
    placeholders = ",".join("?" for _ in source_ids)
    try:
        cursor = await db.execute(
            "SELECT id, text, metadata, created_at, updated_at FROM documents "
            f"WHERE id IN ({placeholders})",
            source_ids,
        )
        rows = await cursor.fetchall()
    except aiosqlite.OperationalError as exc:
        raise RuntimeError("canonical_source_unavailable") from exc

    documents: dict[int, dict[str, Any]] = {}
    for row in rows:
        documents[int(row[0])] = {
            "id": int(row[0]),
            "text": row[1],
            "metadata": _metadata_dict(row[2]),
            "created_at": row[3],
            "updated_at": row[4],
        }
    return documents


async def validate_atom_parent_sources(
    db: aiosqlite.Connection,
    atoms: list[MemoryAtom],
) -> None:
    """当 canonical 表存在时，拒绝缺失或陈旧的 Atom 父来源。

    来源处于总结写入事务的 pending 暂态（``summary_source_pending`` 为真）
    时视为可用：atom 与 parent canonical 属于同一次写入，收口阶段会把
    orphan/pending 一并清除；只有真正的失效来源才拒绝派生写入。
    """

    if any(not has_user_source_evidence(atom.source_evidence) for atom in atoms):
        raise ValueError("grounding_source_evidence_invalid")
    if not atoms or not await _documents_table_exists(db):
        return
    source_ids = tuple(sorted({atom.parent_memory_id for atom in atoms}))
    states = await load_canonical_source_states(db, source_ids)
    pending_ids = await _pending_source_ids(db, source_ids)
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
        if not state.is_active and atom.parent_memory_id not in pending_ids:
            raise ValueError("source_inactive")
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

    atoms = [atom for atom in atoms if has_user_source_evidence(atom.source_evidence)]
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


async def _pending_source_ids(
    db: aiosqlite.Connection, source_ids: tuple[int, ...]
) -> frozenset[int]:
    """返回仍处于总结写入 pending 暂态的 source ID 集合。"""

    if not source_ids:
        return frozenset()
    placeholders = ",".join("?" for _ in source_ids)
    cursor = await db.execute(
        "SELECT id, metadata FROM documents "
        f"WHERE id IN ({placeholders}) AND metadata LIKE '%summary_source_pending%'",
        source_ids,
    )
    pending: set[int] = set()
    for row in await cursor.fetchall():
        metadata = _metadata_dict(row[1])
        if metadata.get("summary_source_pending") is True:
            pending.add(int(row[0]))
    return frozenset(pending)


def _metadata_dict(value: Any) -> dict[str, Any]:
    """把 metadata 列安全解析为字典。"""

    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


async def _documents_table_exists(db: aiosqlite.Connection) -> bool:
    """判断当前数据库是否包含 canonical documents 表。"""

    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
    )
    return await cursor.fetchone() is not None


__all__ = [
    "current_canonical_facts",
    "filter_atoms_by_current_sources",
    "load_canonical_documents",
    "validate_atom_parent_sources",
]
