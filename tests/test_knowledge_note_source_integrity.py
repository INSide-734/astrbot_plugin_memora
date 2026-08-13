"""验证 Knowledge 与 Note 的人工/派生来源边界。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from core.features.knowledge.application import KnowledgeManager
from core.features.knowledge.domain import KnowledgeEntry, KnowledgeType
from core.features.knowledge.infrastructure import KnowledgeStore
from core.features.notes.domain import Note
from core.features.notes.infrastructure import NoteStore
from core.shared.domain_provenance import DomainObjectOrigin, DomainProvenance
from core.shared.contracts import MemorySourceRef


def _provenance(*memory_ids: int) -> DomainProvenance:
    """构造同一作用域下的多来源派生证据。"""

    sources = tuple(
        MemorySourceRef(
            memory_id,
            f"rev-{memory_id}",
            "private:user-a",
            "confidential",
            datetime(2026, 7, 21, tzinfo=timezone.utc),
            source_role="primary" if index == 0 else "supporting",
        )
        for index, memory_id in enumerate(memory_ids)
    )
    return DomainProvenance(DomainObjectOrigin.DERIVED, sources)


async def _create_sources(db_path: str, *memory_ids: int) -> None:
    """写入 Knowledge/Note 测试使用的 canonical source 行。"""

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS documents (
                   id INTEGER PRIMARY KEY, text TEXT NOT NULL, metadata TEXT,
                   created_at TEXT NOT NULL, updated_at TEXT NOT NULL
               )"""
        )
        for memory_id in memory_ids:
            await db.execute(
                """INSERT OR REPLACE INTO documents
                   (id,text,metadata,created_at,updated_at) VALUES(?,?,?,?,?)""",
                (
                    memory_id,
                    "匿名 canonical 正文",
                    json.dumps(
                        {
                            "scope_key": "private:user-a",
                            "privacy_level": "confidential",
                        },
                        ensure_ascii=False,
                    ),
                    "2026-07-21T00:00:00+00:00",
                    f"rev-{memory_id}",
                ),
            )
        await db.commit()


def test_derived_domain_objects_require_provenance() -> None:
    """只有 source_ids 或 source_memory_ids 时不能伪装成派生对象。"""

    with pytest.raises(ValueError, match="source_provenance_required"):
        KnowledgeEntry(
            title="事实",
            content="内容",
            origin=DomainObjectOrigin.DERIVED,
        )
    with pytest.raises(ValueError, match="source_provenance_required"):
        Note(
            title="笔记",
            content="内容",
            origin=DomainObjectOrigin.DERIVED,
        )


@pytest.mark.asyncio
async def test_knowledge_derived_round_trip_and_stale_filter(tmp_db_path: str) -> None:
    """派生 Knowledge 保存来源，source revision 变化后不可读。"""

    store = KnowledgeStore(tmp_db_path)
    await store.init_table()
    await _create_sources(tmp_db_path, 17)
    entry = KnowledgeEntry(
        title="SQLite",
        content="SQLite 是嵌入式数据库",
        category=KnowledgeType.FACT,
        origin=DomainObjectOrigin.DERIVED,
        provenance=_provenance(17),
    )
    entry_id = await store.insert(entry)

    stored = await store.get(entry_id)
    assert stored is not None
    assert stored.provenance is not None
    assert stored.source_ids == [17]

    await _create_sources(tmp_db_path, 17)
    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute(
            "UPDATE documents SET updated_at = ? WHERE id = 17",
            ("rev-18",),
        )
        await db.commit()

    assert await store.get(entry_id) is None


@pytest.mark.asyncio
async def test_knowledge_supporting_source_removal_preserves_primary(
    tmp_db_path: str,
) -> None:
    """删除 supporting source 时保留仍有有效 primary 的 Knowledge。"""

    store = KnowledgeStore(tmp_db_path)
    await store.init_table()
    await _create_sources(tmp_db_path, 17, 18)
    entry_id = await store.insert(
        KnowledgeEntry(
            title="联合事实",
            content="两个来源共同支持",
            origin=DomainObjectOrigin.DERIVED,
            provenance=_provenance(17, 18),
        )
    )
    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute("DELETE FROM documents WHERE id = 18")
        await db.commit()

    stored = await store.get(entry_id)

    assert stored is not None
    assert stored.source_ids == [17]


@pytest.mark.asyncio
async def test_manual_knowledge_and_note_survive_canonical_delete(
    tmp_db_path: str,
) -> None:
    """人工 Knowledge/Note 使用各自领域权威，不受 canonical 删除影响。"""

    knowledge = KnowledgeStore(tmp_db_path)
    note = NoteStore(tmp_db_path)
    await knowledge.init_table()
    await note.init_table()
    await _create_sources(tmp_db_path, 17)

    knowledge_id = await knowledge.insert(
        KnowledgeEntry(title="人工知识", content="手工内容", source_ids=[17])
    )
    note_id = await note.create(
        Note(title="人工笔记", content="手工正文", source_memory_ids=[17])
    )
    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute("DELETE FROM documents WHERE id = 17")
        await db.commit()

    assert await knowledge.get(knowledge_id) is not None
    assert await note.get(note_id) is not None


@pytest.mark.asyncio
async def test_note_derived_round_trip_and_scope_rejection(tmp_db_path: str) -> None:
    """派生 Note 保存来源，跨 scope proposal 在写入时拒绝。"""

    store = NoteStore(tmp_db_path)
    await store.init_table()
    await _create_sources(tmp_db_path, 17)
    note = Note(
        title="派生笔记",
        content="来自 canonical",
        origin=DomainObjectOrigin.DERIVED,
        provenance=_provenance(17),
    )
    note_id = await store.create(note)
    stored = await store.get(note_id)
    assert stored is not None
    assert stored.source_memory_ids == [17]

    wrong_scope = DomainProvenance(
        DomainObjectOrigin.DERIVED,
        (
            MemorySourceRef(
                17,
                "rev-17",
                "group:other",
                "confidential",
                datetime(2026, 7, 21, tzinfo=timezone.utc),
            ),
        ),
    )
    with pytest.raises(ValueError, match="source_scope_mismatch"):
        await store.create(
            Note(
                title="越权笔记",
                content="不应写入",
                origin=DomainObjectOrigin.DERIVED,
                provenance=wrong_scope,
            )
        )


@pytest.mark.asyncio
async def test_knowledge_visible_page_filters_stale_before_limit(
    tmp_db_path: str,
) -> None:
    """stale 派生知识不能占用搜索返回窗口。"""

    store = KnowledgeStore(tmp_db_path)
    await store.init_table()
    await _create_sources(tmp_db_path, 17, 18)
    stale_id = await store.insert(
        KnowledgeEntry(
            title="同一主题",
            content="旧来源内容",
            origin=DomainObjectOrigin.DERIVED,
            provenance=_provenance(17),
        )
    )
    valid_id = await store.insert(
        KnowledgeEntry(
            title="同一主题",
            content="当前来源内容",
            origin=DomainObjectOrigin.DERIVED,
            provenance=_provenance(18),
        )
    )
    assert stale_id != valid_id
    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute(
            "UPDATE documents SET updated_at = ? WHERE id = 17",
            ("rev-stale",),
        )
        await db.commit()

    entries, total = await store.search("同一主题", limit=1)
    assert total == 1
    assert [entry.entry_id for entry in entries] == [valid_id]


@pytest.mark.asyncio
async def test_note_visible_page_filters_stale_before_offset(tmp_db_path: str) -> None:
    """stale 派生笔记不能改变可见分页的 offset 和 total。"""

    store = NoteStore(tmp_db_path)
    await store.init_table()
    await _create_sources(tmp_db_path, 17, 18)
    stale_id = await store.create(
        Note(
            title="主题笔记",
            content="旧来源",
            origin=DomainObjectOrigin.DERIVED,
            provenance=_provenance(17),
        )
    )
    valid_id = await store.create(
        Note(
            title="主题笔记",
            content="当前来源",
            origin=DomainObjectOrigin.DERIVED,
            provenance=_provenance(18),
        )
    )
    assert stale_id != valid_id
    async with aiosqlite.connect(tmp_db_path) as db:
        await db.execute(
            "UPDATE documents SET updated_at = ? WHERE id = 17",
            ("rev-stale",),
        )
        await db.commit()

    notes, total = await store.list_notes(limit=1, offset=0)
    assert total == 1
    assert [note.note_id for note in notes] == [valid_id]


@pytest.mark.asyncio
async def test_knowledge_dedup_merges_derived_sources() -> None:
    """相同知识的派生去重必须保留全部 canonical 来源。"""

    existing = KnowledgeEntry(
        title="联合事实",
        content="同一事实",
        origin=DomainObjectOrigin.DERIVED,
        provenance=_provenance(17),
    )
    incoming = KnowledgeEntry(
        title="联合事实",
        content="同一事实",
        origin=DomainObjectOrigin.DERIVED,
        provenance=_provenance(18),
    )
    store = AsyncMock()
    store.search.return_value = ([existing], 1)
    manager = KnowledgeManager(store)

    assert await manager.add_entry(incoming) == existing.entry_id
    assert existing.provenance is not None
    assert [source.memory_id for source in existing.provenance.sources] == [17, 18]
    store.update.assert_awaited_once_with(existing)
