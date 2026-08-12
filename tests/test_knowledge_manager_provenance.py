"""验证 KnowledgeManager 去重时的 canonical provenance 合并。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from core.features.knowledge.application import KnowledgeManager
from core.features.knowledge.domain import KnowledgeEntry
from core.features.knowledge.infrastructure import KnowledgeStore
from core.models.domain_provenance import DomainObjectOrigin, DomainProvenance
from core.shared.contracts import MemorySourceRef

_OCCURRED_AT = datetime(2026, 7, 21, tzinfo=timezone.utc)


def _provenance(memory_id: int, revision: str) -> DomainProvenance:
    """构造同一可信作用域下的单 primary 来源。"""

    return DomainProvenance(
        DomainObjectOrigin.DERIVED,
        (
            MemorySourceRef(
                memory_id=memory_id,
                revision_token=revision,
                scope_key="private:test-scope",
                privacy_level="confidential",
                occurred_at=_OCCURRED_AT,
                source_role="primary",
            ),
        ),
    )


def _entry(
    *,
    entry_id: int,
    provenance: DomainProvenance,
) -> KnowledgeEntry:
    """构造用于去重的派生知识条目。"""

    return KnowledgeEntry(
        title="稳定事实",
        content="同一份结构化事实内容",
        entry_id=entry_id,
        origin=DomainObjectOrigin.DERIVED,
        provenance=provenance,
    )


async def _set_source_revision(db_path: str, revision: str) -> None:
    """创建或更新真实 Store 测试使用的 canonical revision。"""

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
async def test_add_entry_merges_different_derived_sources() -> None:
    """相同内容来自不同 source 时保留一个条目并合并来源。"""

    store = AsyncMock()
    existing = _entry(entry_id=7, provenance=_provenance(17, "rev-17"))
    incoming = _entry(entry_id=0, provenance=_provenance(18, "rev-18"))
    store.search.return_value = ([existing], 1)
    manager = KnowledgeManager(store)

    result = await manager.add_entry(incoming)

    assert result == 7
    assert store.insert.await_count == 0
    store.update.assert_awaited_once_with(existing)
    assert existing.source_ids == [17, 18]
    assert existing.provenance is not None
    assert [source.memory_id for source in existing.provenance.sources] == [17, 18]
    assert [source.source_role for source in existing.provenance.sources] == [
        "primary",
        "supporting",
    ]


@pytest.mark.asyncio
async def test_add_entry_replaces_same_source_revision() -> None:
    """相同 source 的新 revision 更新来源快照而不制造重复条目。"""

    store = AsyncMock()
    existing = _entry(entry_id=8, provenance=_provenance(17, "rev-old"))
    incoming = _entry(entry_id=0, provenance=_provenance(17, "rev-new"))
    store.search.return_value = ([], 0)
    store.search_merge_candidates.return_value = [existing]
    manager = KnowledgeManager(store)

    result = await manager.add_entry(incoming)

    assert result == 8
    assert store.insert.await_count == 0
    store.search_merge_candidates.assert_awaited_once_with("稳定事实", limit=5)
    store.update.assert_awaited_once_with(existing)
    assert existing.provenance is not None
    assert len(existing.provenance.sources) == 1
    assert existing.provenance.sources[0].memory_id == 17
    assert existing.provenance.sources[0].revision_token == "rev-new"
    assert existing.source_ids == [17]


@pytest.mark.asyncio
async def test_add_entry_reuses_stale_row_for_same_source_revision(
    tmp_db_path: str,
) -> None:
    """真实 Store 过滤旧 revision 后，内部合并读取仍复用原条目。"""

    store = KnowledgeStore(tmp_db_path)
    await store.init_table()
    manager = KnowledgeManager(store)
    await _set_source_revision(tmp_db_path, "rev-old")
    original_id = await manager.add_entry(
        _entry(entry_id=0, provenance=_provenance(17, "rev-old"))
    )
    await _set_source_revision(tmp_db_path, "rev-new")

    merged_id = await manager.add_entry(
        _entry(entry_id=0, provenance=_provenance(17, "rev-new"))
    )

    assert merged_id == original_id
    assert await store.count() == 1
    stored = await store.get(int(original_id or 0))
    assert stored is not None
    assert stored.provenance is not None
    assert stored.provenance.sources[0].revision_token == "rev-new"


@pytest.mark.asyncio
async def test_scoped_knowledge_read_rejects_other_session() -> None:
    """按 ID 读取知识时，其他会话的派生正文必须不可见。"""
    store = AsyncMock()
    entry = _entry(entry_id=21, provenance=_provenance(17, "rev-17"))
    store.get.return_value = entry
    manager = KnowledgeManager(store)

    assert await manager.get_entry_for_scope(21, scope_key="private:other") is None
    assert (
        await manager.get_entry_for_scope(21, scope_key="private:test-scope") is entry
    )


@pytest.mark.asyncio
async def test_scoped_knowledge_search_rejects_other_session() -> None:
    """搜索知识时，其他会话的派生正文必须从结果和总数中移除。"""
    store = AsyncMock()
    entry = _entry(entry_id=22, provenance=_provenance(17, "rev-17"))
    store.search.return_value = ([entry], 1)
    manager = KnowledgeManager(store)

    entries, total = await manager.search_for_scope(
        "稳定事实",
        scope_key="private:other",
    )

    assert entries == []
    assert total == 0
