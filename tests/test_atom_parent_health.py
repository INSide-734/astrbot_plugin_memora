"""验证 Atom parent provenance 的安全健康计数。"""

from __future__ import annotations

import json

import aiosqlite
import pytest

from core.features.memory.domain.memory_atom import AtomType, MemoryAtom
from core.features.memory.infrastructure.atom_store import AtomStore
from core.features.memory.infrastructure.validators.persistence_health_validator import (
    PersistenceHealthValidator,
)


def _atom(content: str) -> MemoryAtom:
    """构造绑定同一 canonical source 的健康检查 Atom。"""

    return MemoryAtom(
        parent_memory_id=17,
        parent_revision="rev-17",
        parent_scope_key="private:user-a",
        parent_privacy_level="confidential",
        atom_type=AtomType.FACTUAL,
        content=content,
    )


@pytest.mark.asyncio
async def test_health_reports_atom_parent_provenance_counts_only(
    tmp_db_path: str,
) -> None:
    """健康检查用固定 reason 和计数报告四类父来源问题。"""

    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute(
            """CREATE TABLE documents (
                   id INTEGER PRIMARY KEY, text TEXT NOT NULL, metadata TEXT,
                   created_at TEXT NOT NULL, updated_at TEXT NOT NULL
               )"""
        )
        await db.execute(
            """INSERT INTO documents(id,text,metadata,created_at,updated_at)
               VALUES(?,?,?,?,?)""",
            (
                17,
                "匿名 canonical 正文",
                json.dumps(
                    {
                        "scope_key": "private:user-a",
                        "privacy_level": "confidential",
                    },
                    ensure_ascii=False,
                ),
                "2026-07-21T00:00:00+00:00",
                "rev-17",
            ),
        )
        await db.commit()

    store = AtomStore(tmp_db_path)
    await store.initialize()
    atom_ids = await store.insert_many([_atom(f"原子-{index}") for index in range(5)])
    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute(
            "UPDATE memory_atoms SET parent_revision = ? WHERE id = ?",
            ("stale", atom_ids[1]),
        )
        await db.execute(
            "UPDATE memory_atoms SET parent_scope_key = ? WHERE id = ?",
            ("group:other", atom_ids[2]),
        )
        await db.execute(
            "UPDATE memory_atoms SET parent_privacy_level = ? WHERE id = ?",
            ("shared", atom_ids[3]),
        )
        await db.execute(
            "UPDATE memory_atoms SET parent_revision = NULL WHERE id = ?",
            (atom_ids[4],),
        )
        await db.commit()

    result = await PersistenceHealthValidator(tmp_db_path).check()

    assert result["issues"]["atom_parent_provenance_missing"] == 1
    assert result["issues"]["atom_parent_revision_mismatch"] == 1
    assert result["issues"]["atom_parent_scope_mismatch"] == 1
    assert result["issues"]["atom_parent_privacy_mismatch"] == 1
    assert all(
        isinstance(result["issues"][reason], int)
        for reason in (
            "atom_parent_provenance_missing",
            "atom_parent_revision_mismatch",
            "atom_parent_scope_mismatch",
            "atom_parent_privacy_mismatch",
        )
    )
