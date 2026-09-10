"""Atom 父来源公开读取、前瞻分页与真实修复回归。"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from core.features.memory.domain.memory_atom import AtomType, MemoryAtom
from core.features.memory.infrastructure.atom_store import AtomStore
from core.features.memory.infrastructure.write_op_journal import WriteOpJournal
from core.features.memory.infrastructure.write_op_serialization import (
    serialize_atom_for_repair,
)


async def _upsert_document(
    db_path: str,
    memory_id: int,
    *,
    revision: str,
    privacy_level: str,
    session_id: str = "scope-a",
    persona_id: str = "persona-a",
) -> dict:
    """写入匿名 canonical 文档并返回 manager 形状的读取结果。"""

    metadata = {
        "scope_key": session_id,
        "privacy_level": privacy_level,
        "session_id": session_id,
        "persona_id": persona_id,
    }
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                   id INTEGER PRIMARY KEY, text TEXT NOT NULL, metadata TEXT,
                   created_at TEXT NOT NULL, updated_at TEXT NOT NULL
               )"""
        )
        await db.execute(
            """INSERT OR REPLACE INTO documents
               (id,text,metadata,created_at,updated_at) VALUES(?,?,?,?,?)""",
            (
                memory_id,
                f"匿名正文-{memory_id}",
                json.dumps(metadata, ensure_ascii=False),
                "2026-07-21T00:00:00+00:00",
                revision,
            ),
        )
        await db.commit()
    return {
        "id": memory_id,
        "text": f"匿名正文-{memory_id}",
        "metadata": metadata,
        "created_at": "2026-07-21T00:00:00+00:00",
        "updated_at": revision,
    }


def _planned_atom(
    memory_id: int,
    *,
    revision: str,
    privacy_level: str,
    content: str,
    event_time: float,
) -> MemoryAtom:
    """构造已绑定父来源的 PLANNED Atom。"""

    return MemoryAtom(
        parent_memory_id=memory_id,
        parent_revision=revision,
        parent_scope_key="scope-a",
        parent_privacy_level=privacy_level,
        atom_type=AtomType.PLANNED,
        content=content,
        event_time=event_time,
        session_id="scope-a",
        persona_id="persona-a",
    )


@pytest.mark.asyncio
async def test_public_reads_drop_stale_atom_but_raw_reads_preserve_it(
    tmp_db_path: str,
) -> None:
    """公开读取 fail closed，维护接口仍能定位陈旧行。"""

    store = AtomStore(tmp_db_path)
    await store.initialize()
    await _upsert_document(
        tmp_db_path,
        17,
        revision="rev-17",
        privacy_level="shared",
    )
    atom_id = await store.insert(
        _planned_atom(
            17,
            revision="rev-17",
            privacy_level="shared",
            content="稍后处理",
            event_time=time.time() + 60,
        )
    )
    await _upsert_document(
        tmp_db_path,
        17,
        revision="rev-18",
        privacy_level="shared",
    )

    assert await store.get(atom_id) is None
    assert await store.get_by_parent(17) == []
    raw_atom = await store.get_raw(atom_id)
    assert raw_atom is not None
    assert raw_atom.atom_id == atom_id
    assert [atom.atom_id for atom in await store.get_by_parent_raw(17)] == [atom_id]


@pytest.mark.asyncio
async def test_planned_query_skips_stale_first_row_before_applying_limit(
    tmp_db_path: str,
) -> None:
    """较早的 stale Atom 不得占满 limit 窗口。"""

    store = AtomStore(tmp_db_path)
    await store.initialize()
    now = time.time()
    await _upsert_document(
        tmp_db_path,
        17,
        revision="rev-17",
        privacy_level="public",
    )
    await store.insert(
        _planned_atom(
            17,
            revision="rev-17",
            privacy_level="public",
            content="陈旧计划",
            event_time=now + 60,
        )
    )
    await _upsert_document(
        tmp_db_path,
        17,
        revision="rev-18",
        privacy_level="public",
    )
    await _upsert_document(
        tmp_db_path,
        18,
        revision="rev-18",
        privacy_level="public",
    )
    await store.insert(
        _planned_atom(
            18,
            revision="rev-18",
            privacy_level="public",
            content="有效计划",
            event_time=now + 120,
        )
    )

    results = await store.query_upcoming_planned(
        session_id="scope-a",
        persona_id="persona-a",
        chat_type="group",
        limit=1,
    )

    assert [atom.content for atom in results] == ["有效计划"]


@pytest.mark.asyncio
async def test_planned_query_skips_confidential_first_row_before_limit(
    tmp_db_path: str,
) -> None:
    """群聊中较早的 confidential Atom 不得遮挡后续公开计划。"""

    store = AtomStore(tmp_db_path)
    await store.initialize()
    now = time.time()
    await _upsert_document(
        tmp_db_path,
        17,
        revision="rev-17",
        privacy_level="confidential",
    )
    await _upsert_document(
        tmp_db_path,
        18,
        revision="rev-18",
        privacy_level="public",
    )
    await store.insert(
        _planned_atom(
            17,
            revision="rev-17",
            privacy_level="confidential",
            content="私密计划",
            event_time=now + 60,
        )
    )
    await store.insert(
        _planned_atom(
            18,
            revision="rev-18",
            privacy_level="public",
            content="公开计划",
            event_time=now + 120,
        )
    )

    results = await store.query_upcoming_planned(
        session_id="scope-a",
        persona_id="persona-a",
        chat_type="group",
        limit=1,
    )

    assert [atom.content for atom in results] == ["公开计划"]


@pytest.mark.asyncio
async def test_failed_atom_repair_restores_through_real_store(
    tmp_db_path: str,
) -> None:
    """failed_atoms 重放必须经过真实 Store 校验并恢复可读 Atom。"""

    store = AtomStore(tmp_db_path)
    await store.initialize()
    memory = await _upsert_document(
        tmp_db_path,
        42,
        revision="rev-42",
        privacy_level="shared",
    )
    atom = _planned_atom(
        42,
        revision="rev-42",
        privacy_level="shared",
        content="修复后的计划",
        event_time=time.time() + 300,
    )

    async with aiosqlite.connect(tmp_db_path) as db:
        db.row_factory = aiosqlite.Row
        journal = WriteOpJournal(
            db_connection=db,
            graph_memory_manager=None,
            atom_store=store,
            atom_enabled=True,
            get_memory_cb=AsyncMock(return_value=memory),
        )
        await journal.create_table()
        op_id = await journal.start_op(
            "add",
            {"failed_atoms": [serialize_atom_for_repair(atom)]},
            memory_id=42,
        )
        await db.execute(
            "UPDATE memory_write_ops SET status='needs_repair' WHERE id=?",
            (op_id,),
        )
        await db.commit()

        assert await journal.repair_incomplete() == 1
        row = await (
            await db.execute(
                "SELECT status,step FROM memory_write_ops WHERE id=?",
                (op_id,),
            )
        ).fetchone()

    restored = await store.get_by_parent(42)
    assert [(item.content, item.parent_revision) for item in restored] == [
        ("修复后的计划", "rev-42")
    ]
    assert row is not None
    assert row["status"] == "completed"
