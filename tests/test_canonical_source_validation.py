"""验证 canonical source 校验端 privacy_level 缺失回退与非法值 fail-closed。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite
import pytest

from core.features.memory.infrastructure.canonical_source_validation import (
    load_canonical_source_states,
    validate_domain_provenance,
)
from core.features.notes.domain import Note
from core.features.notes.infrastructure import NoteStore
from core.shared.contracts import MemorySourceRef
from core.shared.domain_provenance import DomainObjectOrigin, DomainProvenance

_ABSENT = object()


async def _create_source(
    db_path: str,
    *,
    memory_id: int = 17,
    privacy_level: Any = _ABSENT,
) -> None:
    """写入 canonical source 行；privacy_level 用哨兵区分键缺失与显式 null。"""

    metadata: dict[str, Any] = {"scope_key": "private:user-a"}
    if privacy_level is not _ABSENT:
        metadata["privacy_level"] = privacy_level
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
                "匿名 canonical 正文",
                json.dumps(metadata, ensure_ascii=False),
                "2026-09-09T00:00:00+00:00",
                f"rev-{memory_id}",
            ),
        )
        await db.commit()


def _provenance(
    memory_id: int = 17,
    privacy_level: str = "shared",
) -> DomainProvenance:
    """构造与 canonical 行同 revision/scope 的派生证据。"""

    source = MemorySourceRef(
        memory_id,
        f"rev-{memory_id}",
        "private:user-a",
        privacy_level,
        datetime(2026, 9, 9, tzinfo=timezone.utc),
    )
    return DomainProvenance(DomainObjectOrigin.DERIVED, (source,))


@pytest.mark.asyncio
@pytest.mark.parametrize("privacy_level", [_ABSENT, None])
async def test_missing_privacy_level_falls_back_to_shared(
    tmp_db_path: str,
    privacy_level: Any,
) -> None:
    """legacy 行缺失 privacy_level（键不存在或值为 null）回退 shared，派生写入通过。"""

    await _create_source(tmp_db_path, privacy_level=privacy_level)
    async with aiosqlite.connect(tmp_db_path) as db:
        states = await load_canonical_source_states(db, (17,))
    assert states[17].privacy_level == "shared"

    async with aiosqlite.connect(tmp_db_path) as db:
        assert await validate_domain_provenance(db, _provenance(17)) is None

    store = NoteStore(tmp_db_path)
    await store.init_table()
    note_id = await store.create(
        Note(
            title="回退笔记",
            content="缺失 privacy 的 legacy 来源派生",
            origin=DomainObjectOrigin.DERIVED,
            provenance=_provenance(17),
        )
    )
    assert note_id > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_level", ["secret", "", "Shared"])
async def test_invalid_privacy_level_keeps_fail_closed(
    tmp_db_path: str,
    invalid_level: Any,
) -> None:
    """privacy_level 存在但不在闭集时保持 None，派生写入继续被拒。"""

    await _create_source(tmp_db_path, privacy_level=invalid_level)
    async with aiosqlite.connect(tmp_db_path) as db:
        states = await load_canonical_source_states(db, (17,))
        assert states[17].privacy_level is None
        with pytest.raises(ValueError, match="source_privacy_missing"):
            await validate_domain_provenance(db, _provenance(17))

    store = NoteStore(tmp_db_path)
    await store.init_table()
    with pytest.raises(ValueError, match="source_privacy_missing"):
        await store.create(
            Note(
                title="非法隐私笔记",
                content="不应写入",
                origin=DomainObjectOrigin.DERIVED,
                provenance=_provenance(17),
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["public", "shared", "confidential"])
async def test_valid_privacy_level_round_trips(
    tmp_db_path: str,
    level: str,
) -> None:
    """闭集内 privacy_level 原样读取，等级变化仍按 mismatch 拒绝。"""

    other = next(item for item in ("public", "shared", "confidential") if item != level)
    await _create_source(tmp_db_path, privacy_level=level)
    async with aiosqlite.connect(tmp_db_path) as db:
        states = await load_canonical_source_states(db, (17,))
        assert states[17].privacy_level == level
        assert await validate_domain_provenance(db, _provenance(17, level)) is None
        with pytest.raises(ValueError, match="source_privacy_mismatch"):
            await validate_domain_provenance(db, _provenance(17, other))
