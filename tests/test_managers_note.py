"""测试 NoteManager — 基于 Mock NoteStore 的笔记 CRUD 操作。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.managers.note_manager import NoteManager
from core.models.note_models import Note, NoteStatus, NoteVersion

# ---------------------------------------------------------------------------
# create_note
# ---------------------------------------------------------------------------


class TestCreateNote:
    """create_note."""

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
# get_note
# ---------------------------------------------------------------------------


class TestGetNote:
    """get_note."""

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


# ---------------------------------------------------------------------------
# update_note
# ---------------------------------------------------------------------------


class TestUpdateNote:
    """update_note."""

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
# delete_note
# ---------------------------------------------------------------------------


class TestDeleteNote:
    """delete_note."""

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
# search
# ---------------------------------------------------------------------------


class TestSearch:
    """search."""

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


# ---------------------------------------------------------------------------
# list_notes
# ---------------------------------------------------------------------------


class TestListNotes:
    """list_notes."""

    @pytest.mark.asyncio
    async def test_list_notes_delegates(self) -> None:
        store = MagicMock()
        store.list_notes = AsyncMock(return_value=([], 0))
        mgr = NoteManager(store=store)
        result, total = await mgr.list_notes(limit=30, offset=10, status="active")
        store.list_notes.assert_called_once_with(limit=30, offset=10, status="active")


# ---------------------------------------------------------------------------
# get_versions
# ---------------------------------------------------------------------------


class TestGetVersions:
    """get_versions."""

    @pytest.mark.asyncio
    async def test_get_versions_delegates(self) -> None:
        versions = [NoteVersion(version=1), NoteVersion(version=2)]
        store = MagicMock()
        store.get_versions = AsyncMock(return_value=versions)
        mgr = NoteManager(store=store)
        result = await mgr.get_versions(1)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# count / prune_versions
# ---------------------------------------------------------------------------


class TestCountAndPrune:
    """count and prune_versions."""

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
# auto_create_from_memory
# ---------------------------------------------------------------------------


class TestAutoCreateFromMemory:
    """auto_create_from_memory logic."""

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
        result = await mgr.auto_create_from_memory(long_text, user_id="user1")
        assert result == 10
        created = store.create.call_args[0][0]
        assert created.title == "A" * 50  # first line, capped at 80
        assert created.content == "Body text here"
        assert "auto-generated" in created.tags

    @pytest.mark.asyncio
    async def test_single_line_content(self) -> None:
        store = MagicMock()
        store.create = AsyncMock(return_value=5)
        mgr = NoteManager(store=store)
        long_text = "B" * 60  # no newline → title = first 80 chars, body = same
        result = await mgr.auto_create_from_memory(long_text)
        assert result == 5
        created = store.create.call_args[0][0]
        assert created.title == "B" * 60
        assert created.content == "B" * 60

    @pytest.mark.asyncio
    async def test_exactly_50_chars_borderline(self) -> None:
        store = MagicMock()
        store.create = AsyncMock(return_value=1)
        mgr = NoteManager(store=store)
        text = "C" * 50  # exactly 50 — meets threshold
        result = await mgr.auto_create_from_memory(text)
        assert result == 1

    @pytest.mark.asyncio
    async def test_49_chars_below_threshold(self) -> None:
        store = MagicMock()
        store.create = AsyncMock()
        mgr = NoteManager(store=store)
        text = "C" * 49  # below 50 threshold
        result = await mgr.auto_create_from_memory(text)
        assert result is None
        store.create.assert_not_called()
