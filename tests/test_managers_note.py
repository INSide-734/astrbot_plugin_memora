"""测试 NoteManager — 基于 Mock NoteStore 的笔记 CRUD 操作。"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.managers.note_manager import NoteManager
from core.models.domain_provenance import DomainObjectOrigin, DomainProvenance
from core.models.memory_evolution import MemorySourceRef
from core.models.note_models import Note, NoteStatus, NoteVersion


def _provenance() -> DomainProvenance:
    """构造自动笔记测试使用的 canonical 来源证据。"""

    return DomainProvenance(
        DomainObjectOrigin.DERIVED,
        (
            MemorySourceRef(
                memory_id=17,
                revision_token="revision-17",
                scope_key="session:test",
                privacy_level="shared",
                occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# 创建笔记
# ---------------------------------------------------------------------------


class TestCreateNote:
    """验证创建笔记。"""

    @pytest.mark.asyncio
    async def test_create_note_returns_id(self) -> None:
        store = MagicMock()
        store.create = AsyncMock(return_value=42)
        mgr = NoteManager(store=store)
        note_id = await mgr.create_note(
            "Title", "Content", tags=["tag1"], user_id="user1"
        )
        assert note_id == 42
        store.create.assert_called_once()
        created_note = store.create.call_args[0][0]
        assert isinstance(created_note, Note)
        assert created_note.title == "Title"
        assert created_note.content == "Content"
        assert created_note.tags == ["tag1"]
        assert created_note.user_id == "user1"

    @pytest.mark.asyncio
    async def test_create_note_defaults(self) -> None:
        store = MagicMock()
        store.create = AsyncMock(return_value=1)
        mgr = NoteManager(store=store)
        note_id = await mgr.create_note("T", "C")
        assert note_id == 1
        created_note = store.create.call_args[0][0]
        assert created_note.tags == []
        assert created_note.user_id == ""


# ---------------------------------------------------------------------------
# 读取笔记
# ---------------------------------------------------------------------------


class TestGetNote:
    """验证读取笔记。"""

    @pytest.mark.asyncio
    async def test_get_existing_note(self) -> None:
        note = Note(note_id=1, title="Test", content="Body")
        store = MagicMock()
        store.get = AsyncMock(return_value=note)
        mgr = NoteManager(store=store)
        result = await mgr.get_note(1)
        assert result is note
        store.get.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_get_nonexistent_note(self) -> None:
        store = MagicMock()
        store.get = AsyncMock(return_value=None)
        mgr = NoteManager(store=store)
        result = await mgr.get_note(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_scoped_get_rejects_other_user_note(self) -> None:
        """按 ID 读取笔记时，其他用户的人工正文必须不可见。"""
        note = Note(note_id=1, user_id="user-a", content="secret")
        store = MagicMock()
        store.get = AsyncMock(return_value=note)
        mgr = NoteManager(store=store)

        assert (
            await mgr.get_note_for_scope(
                1,
                scope_key="private:user-b",
                user_id="user-b",
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_scoped_get_rejects_other_session_derived_note(self) -> None:
        """按 ID 读取笔记时，其他会话的派生正文必须不可见。"""
        note = Note(
            note_id=1,
            origin=DomainObjectOrigin.DERIVED,
            provenance=_provenance(),
            content="secret",
        )
        store = MagicMock()
        store.get = AsyncMock(return_value=note)
        mgr = NoteManager(store=store)

        assert (
            await mgr.get_note_for_scope(
                1,
                scope_key="session:other",
                user_id="user-a",
            )
            is None
        )


# ---------------------------------------------------------------------------
# 更新笔记
# ---------------------------------------------------------------------------


class TestUpdateNote:
    """验证更新笔记。"""

    @pytest.mark.asyncio
    async def test_update_title(self) -> None:
        note = Note(note_id=1, title="Old Title")
        store = MagicMock()
        store.get = AsyncMock(return_value=note)
        store.update = AsyncMock()
        store.prune_versions = AsyncMock()
        mgr = NoteManager(store=store)
        result = await mgr.update_note(1, title="New Title")
        assert result is note
        assert result.title == "New Title"

    @pytest.mark.asyncio
    async def test_update_content(self) -> None:
        note = Note(note_id=1, content="Old Content")
        store = MagicMock()
        store.get = AsyncMock(return_value=note)
        store.update = AsyncMock()
        store.prune_versions = AsyncMock()
        mgr = NoteManager(store=store)
        result = await mgr.update_note(1, content="New Content")
        assert result.content == "New Content"

    @pytest.mark.asyncio
    async def test_update_tags(self) -> None:
        note = Note(note_id=1, tags=["old"])
        store = MagicMock()
        store.get = AsyncMock(return_value=note)
        store.update = AsyncMock()
        store.prune_versions = AsyncMock()
        mgr = NoteManager(store=store)
        result = await mgr.update_note(1, tags=["new1", "new2"])
        assert result.tags == ["new1", "new2"]

    @pytest.mark.asyncio
    async def test_update_status(self) -> None:
        note = Note(note_id=1, status=NoteStatus.ACTIVE)
        store = MagicMock()
        store.get = AsyncMock(return_value=note)
        store.update = AsyncMock()
        store.prune_versions = AsyncMock()
        mgr = NoteManager(store=store)
        result = await mgr.update_note(1, status="archived")
        assert result.status == NoteStatus.ARCHIVED

    @pytest.mark.asyncio
    async def test_update_nonexistent(self) -> None:
        store = MagicMock()
        store.get = AsyncMock(return_value=None)
        mgr = NoteManager(store=store)
        result = await mgr.update_note(999, title="x")
        assert result is None


# ---------------------------------------------------------------------------
# 删除笔记
# ---------------------------------------------------------------------------


class TestDeleteNote:
    """验证删除笔记。"""

    @pytest.mark.asyncio
    async def test_delete_returns_store_result(self) -> None:
        store = MagicMock()
        store.delete = AsyncMock(return_value=True)
        mgr = NoteManager(store=store)
        assert await mgr.delete_note(1) is True

    @pytest.mark.asyncio
    async def test_delete_not_found(self) -> None:
        store = MagicMock()
        store.delete = AsyncMock(return_value=False)
        mgr = NoteManager(store=store)
        assert await mgr.delete_note(999) is False


# ---------------------------------------------------------------------------
# 搜索笔记
# ---------------------------------------------------------------------------


class TestSearch:
    """验证搜索笔记。"""

    @pytest.mark.asyncio
    async def test_search_delegates(self) -> None:
        notes = [Note(note_id=1), Note(note_id=2)]
        store = MagicMock()
        store.search = AsyncMock(return_value=(notes, 2))
        mgr = NoteManager(store=store)
        result, total = await mgr.search("query", limit=10)
        assert len(result) == 2
        assert total == 2
        store.search.assert_called_once_with("query", limit=10)

    @pytest.mark.asyncio
    async def test_scoped_search_rejects_cross_user_note(self) -> None:
        """搜索笔记时，其他用户的人工正文必须从结果中移除。"""
        notes = [Note(note_id=1, user_id="user-a"), Note(note_id=2, user_id="user-b")]
        store = MagicMock()
        store.search = AsyncMock(return_value=(notes, 2))
        mgr = NoteManager(store=store)

        result, total = await mgr.search_for_scope(
            "query",
            scope_key="private:user-b",
            user_id="user-b",
            limit=10,
        )

        assert [note.note_id for note in result] == [2]
        assert total == 1


# ---------------------------------------------------------------------------
# 分页列出笔记
# ---------------------------------------------------------------------------


class TestListNotes:
    """验证分页列出笔记。"""

    @pytest.mark.asyncio
    async def test_list_notes_delegates(self) -> None:
        store = MagicMock()
        store.list_notes = AsyncMock(return_value=([], 0))
        mgr = NoteManager(store=store)
        result, total = await mgr.list_notes(limit=30, offset=10, status="active")
        store.list_notes.assert_called_once_with(limit=30, offset=10, status="active")


# ---------------------------------------------------------------------------
# 读取版本历史
# ---------------------------------------------------------------------------


class TestGetVersions:
    """验证读取版本历史。"""

    @pytest.mark.asyncio
    async def test_get_versions_delegates(self) -> None:
        versions = [NoteVersion(version=1), NoteVersion(version=2)]
        store = MagicMock()
        store.get_versions = AsyncMock(return_value=versions)
        mgr = NoteManager(store=store)
        result = await mgr.get_versions(1)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 计数与裁剪版本
# ---------------------------------------------------------------------------


class TestCountAndPrune:
    """验证计数与版本裁剪。"""

    @pytest.mark.asyncio
    async def test_count(self) -> None:
        store = MagicMock()
        store.count = AsyncMock(return_value=5)
        mgr = NoteManager(store=store)
        assert await mgr.count() == 5

    @pytest.mark.asyncio
    async def test_prune_versions(self) -> None:
        store = MagicMock()
        store.prune_versions = AsyncMock(return_value=3)
        mgr = NoteManager(store=store)
        assert await mgr.prune_versions(max_versions=10) == 3
        store.prune_versions.assert_called_once_with(10)


# ---------------------------------------------------------------------------
# 从 canonical memory 自动创建
# ---------------------------------------------------------------------------


class TestAutoCreateFromMemory:
    """验证 canonical 来源约束的自动创建逻辑。"""

    @pytest.mark.asyncio
    async def test_short_content_returns_none(self) -> None:
        store = MagicMock()
        store.create = AsyncMock()
        mgr = NoteManager(store=store)
        result = await mgr.auto_create_from_memory("short")
        assert result is None
        store.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_long_content_creates_note(self) -> None:
        store = MagicMock()
        store.create = AsyncMock(return_value=10)
        mgr = NoteManager(store=store)
        long_text = "A" * 50 + "\nBody text here"
        result = await mgr.auto_create_from_memory(
            long_text,
            source_memory_ids=[17],
            user_id="user1",
            provenance=_provenance(),
        )
        assert result == 10
        created = store.create.call_args[0][0]
        assert created.title == "A" * 50  # 第一行作为标题，最多 80 字符
        assert created.content == "Body text here"
        assert "auto-generated" in created.tags

    @pytest.mark.asyncio
    async def test_single_line_content(self) -> None:
        store = MagicMock()
        store.create = AsyncMock(return_value=5)
        mgr = NoteManager(store=store)
        long_text = "B" * 60  # 无换行时标题取前 80 字符，正文保持原文
        result = await mgr.auto_create_from_memory(
            long_text,
            source_memory_ids=[17],
            provenance=_provenance(),
        )
        assert result == 5
        created = store.create.call_args[0][0]
        assert created.title == "B" * 60
        assert created.content == "B" * 60

    @pytest.mark.asyncio
    async def test_exactly_50_chars_borderline(self) -> None:
        store = MagicMock()
        store.create = AsyncMock(return_value=1)
        mgr = NoteManager(store=store)
        text = "C" * 50  # 恰好达到默认 50 字符门槛
        result = await mgr.auto_create_from_memory(
            text,
            source_memory_ids=[17],
            provenance=_provenance(),
        )
        assert result == 1

    @pytest.mark.asyncio
    async def test_49_chars_below_threshold(self) -> None:
        store = MagicMock()
        store.create = AsyncMock()
        mgr = NoteManager(store=store)
        text = "C" * 49  # 低于默认 50 字符门槛
        result = await mgr.auto_create_from_memory(text)
        assert result is None
        store.create.assert_not_called()
