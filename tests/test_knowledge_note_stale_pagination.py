"""验证 Knowledge 与 Note 在分页前过滤 stale 派生对象。"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import aiosqlite
import pytest

from core.features.knowledge.domain import KnowledgeEntry
from core.features.knowledge.infrastructure import KnowledgeStore
from core.features.notes.domain import Note
from core.features.notes.infrastructure import NoteStore
from core.models.domain_provenance import DomainObjectOrigin, DomainProvenance
from core.shared.contracts import MemorySourceRef

_OCCURRED_AT = datetime(2026, 7, 21, tzinfo=timezone.utc)


def _provenance(revision: str) -> DomainProvenance:
    """构造分页测试使用的单来源派生证据。"""

    return DomainProvenance(
        DomainObjectOrigin.DERIVED,
        (
            MemorySourceRef(
                memory_id=17,
                revision_token=revision,
                scope_key="private:test-scope",
                privacy_level="confidential",
                occurred_at=_OCCURRED_AT,
            ),
        ),
    )


async def _set_source_revision(db_path: str, revision: str) -> None:
    """创建或更新 canonical source 的当前 revision。"""

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                   id INTEGER PRIMARY KEY, text TEXT NOT NULL, metadata TEXT,
                   created_at TEXT NOT NULL, updated_at TEXT NOT NULL
               )"""
        )
        await db.execute(
            """INSERT OR REPLACE INTO documents
               (id, text, metadata, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                17,
                "测试正文",
                json.dumps(
                    {
                        "scope_key": "private:test-scope",
                        "privacy_level": "confidential",
                    }
                ),
                "2026-07-21T00:00:00+00:00",
                revision,
            ),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_knowledge_filters_stale_before_search_and_list_limit(
    tmp_db_path: str,
) -> None:
    """排序首项 stale 时，Knowledge 仍返回后续有效条目和有效总数。"""

    store = KnowledgeStore(tmp_db_path)
    await store.init_table()
    await _set_source_revision(tmp_db_path, "rev-current")
    visible_id = await store.insert(
        KnowledgeEntry(
            title="分页知识",
            content="用于分页验证",
            origin=DomainObjectOrigin.DERIVED,
            provenance=_provenance("rev-current"),
        )
    )
    await _set_source_revision(tmp_db_path, "rev-stale")
    stale_id = await store.insert(
        KnowledgeEntry(
            title="分页知识",
            content="用于分页验证",
            origin=DomainObjectOrigin.DERIVED,
            provenance=_provenance("rev-stale"),
        )
    )
    await _set_source_revision(tmp_db_path, "rev-current")
    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute(
            "UPDATE knowledge_entries SET updated_at = ? WHERE id = ?",
            (9_999_999_999.0, stale_id),
        )
        await db.commit()

    search_results, search_total = await store.search("分页", limit=1)
    list_results, list_total = await store.list_entries(limit=1)

    assert [entry.entry_id for entry in search_results] == [visible_id]
    assert search_total == 1
    assert [entry.entry_id for entry in list_results] == [visible_id]
    assert list_total == 1


@pytest.mark.asyncio
async def test_note_filters_stale_before_search_and_list_limit(
    tmp_db_path: str,
) -> None:
    """排序首项 stale 时，Note 仍返回后续有效条目和有效总数。"""

    store = NoteStore(tmp_db_path)
    await store.init_table()
    await _set_source_revision(tmp_db_path, "rev-current")
    visible_id = await store.create(
        Note(
            title="分页笔记",
            content="用于分页验证",
            origin=DomainObjectOrigin.DERIVED,
            provenance=_provenance("rev-current"),
        )
    )
    await _set_source_revision(tmp_db_path, "rev-stale")
    stale_id = await store.create(
        Note(
            title="分页笔记",
            content="用于分页验证",
            origin=DomainObjectOrigin.DERIVED,
            provenance=_provenance("rev-stale"),
        )
    )
    await _set_source_revision(tmp_db_path, "rev-current")
    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute(
            "UPDATE notes SET updated_at = ? WHERE id = ?",
            (9_999_999_999.0, stale_id),
        )
        await db.commit()

    search_results, search_total = await store.search("分页", limit=1)
    list_results, list_total = await store.list_notes(limit=1)

    assert [note.note_id for note in search_results] == [visible_id]
    assert search_total == 1
    assert [note.note_id for note in list_results] == [visible_id]
    assert list_total == 1
