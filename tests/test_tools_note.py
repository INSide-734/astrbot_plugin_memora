"""测试 core/tools/note_tools.py — NoteSearchTool, NoteReadTool, NoteWriteTool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.tools.note_tools import NoteReadTool, NoteSearchTool, NoteWriteTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_note(
    note_id: int = 1,
    title: str = "Test Note",
    content: str = "Note content here.",
    version: int = 1,
    tags: list[str] | None = None,
) -> MagicMock:
    note = MagicMock()
    note.note_id = note_id
    note.title = title
    note.content = content
    note.version = version
    note.tags = tags or []
    return note


def _make_mock_ctx() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# NoteSearchTool
# ---------------------------------------------------------------------------


class TestNoteSearchTool:
    """测试 NoteSearchTool 定义与执行。"""

    def test_tool_definition_has_correct_name_and_params(self):
        """工具 should expose name, description, and parameters schema requiring query."""
        tool = NoteSearchTool()

        assert tool.name == "note_search"
        assert "Search notes by keyword" in tool.description
        assert tool.parameters["type"] == "object"
        assert "query" in tool.parameters["properties"]
        assert "limit" in tool.parameters["properties"]
        assert tool.parameters["required"] == ["query"]

    @pytest.mark.asyncio
    async def test_search_happy_path_returns_formatted_list(self):
        """当 note_manager.search() returns notes, tool should format them as text."""
        note1 = _make_mock_note(1, "Shopping List", "Buy milk, eggs, bread.", version=2, tags=["shopping"])
        note2 = _make_mock_note(2, "Meeting Notes", "Discussed Q3 roadmap.", version=1, tags=["work"])

        mock_mgr = MagicMock()
        mock_mgr.search = AsyncMock(return_value=([note1, note2], 2))

        tool = NoteSearchTool(note_manager=mock_mgr)
        result = await tool.call(_make_mock_ctx(), query="shop")

        assert "Found 2 note(s)" in result
        assert "[1] Shopping List (v2)" in result
        assert "[2] Meeting Notes (v1)" in result
        assert "Buy milk, eggs, bread." in result

    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        """当 search returns no notes, tool should report no notes found."""
        mock_mgr = MagicMock()
        mock_mgr.search = AsyncMock(return_value=([], 0))

        tool = NoteSearchTool(note_manager=mock_mgr)
        result = await tool.call(_make_mock_ctx(), query="nothing")

        assert "No notes found for: nothing" == result

    @pytest.mark.asyncio
    async def test_search_manager_not_available(self):
        """当 note_manager is None, tool should return an error message."""
        tool = NoteSearchTool(note_manager=None)
        result = await tool.call(_make_mock_ctx(), query="test")

        assert "Error: note_manager not available" == result

    @pytest.mark.asyncio
    async def test_search_passes_limit_parameter(self):
        """工具 should forward the limit parameter to the manager."""
        mock_mgr = MagicMock()
        mock_mgr.search = AsyncMock(return_value=([], 0))

        tool = NoteSearchTool(note_manager=mock_mgr)
        await tool.call(_make_mock_ctx(), query="q", limit=5)

        mock_mgr.search.assert_called_once_with("q", limit=5)


# ---------------------------------------------------------------------------
# NoteReadTool
# ---------------------------------------------------------------------------


class TestNoteReadTool:
    """测试 NoteReadTool 定义与执行。"""

    def test_tool_definition_has_correct_name_and_params(self):
        """工具 should expose name, description, and parameters schema requiring note_id."""
        tool = NoteReadTool()

        assert tool.name == "note_read"
        assert "Read a note's full content" in tool.description
        assert tool.parameters["type"] == "object"
        assert "note_id" in tool.parameters["properties"]
        assert tool.parameters["required"] == ["note_id"]

    @pytest.mark.asyncio
    async def test_read_happy_path_returns_full_note(self):
        """当 note_manager.get_note() returns a note, tool should format as markdown-like."""
        note = _make_mock_note(3, "My Note", "Full content\nLine 2.", version=3, tags=["a", "b"])

        mock_mgr = MagicMock()
        mock_mgr.get_note = AsyncMock(return_value=note)

        tool = NoteReadTool(note_manager=mock_mgr)
        result = await tool.call(_make_mock_ctx(), note_id=3)

        assert "# My Note" in result
        assert "Full content\nLine 2." in result
        assert "Tags: a, b" in result
        assert "Version: 3" in result

    @pytest.mark.asyncio
    async def test_read_note_not_found(self):
        """当 get_note returns None, tool should report not found."""
        mock_mgr = MagicMock()
        mock_mgr.get_note = AsyncMock(return_value=None)

        tool = NoteReadTool(note_manager=mock_mgr)
        result = await tool.call(_make_mock_ctx(), note_id=999)

        assert "Note 999 not found." == result

    @pytest.mark.asyncio
    async def test_read_manager_not_available(self):
        """当 note_manager is None, tool should return an error message."""
        tool = NoteReadTool(note_manager=None)
        result = await tool.call(_make_mock_ctx(), note_id=1)

        assert "Error: note_manager not available" == result


# ---------------------------------------------------------------------------
# NoteWriteTool
# ---------------------------------------------------------------------------


class TestNoteWriteTool:
    """测试 NoteWriteTool 定义与执行。"""

    def test_tool_definition_has_correct_name_and_params(self):
        """工具 should expose name, description, and parameters schema requiring title and content."""
        tool = NoteWriteTool()

        assert tool.name == "note_write"
        assert "Create a new note or update an existing one" in tool.description
        assert "explicitly asks" in tool.description
        assert tool.parameters["type"] == "object"
        assert "title" in tool.parameters["properties"]
        assert "content" in tool.parameters["properties"]
        assert "note_id" in tool.parameters["properties"]
        assert "tags" in tool.parameters["properties"]
        assert tool.parameters["required"] == ["title", "content"]

    @pytest.mark.asyncio
    async def test_write_create_new_note(self):
        """当 note_id is not provided, tool should create a new note."""
        mock_mgr = MagicMock()
        mock_mgr.create_note = AsyncMock(return_value=42)

        tool = NoteWriteTool(note_manager=mock_mgr)
        result = await tool.call(
            _make_mock_ctx(),
            title="New Note",
            content="Some content",
        )

        assert "Note 42 created: New Note" == result
        mock_mgr.create_note.assert_called_once_with(
            title="New Note", content="Some content", tags=[]
        )

    @pytest.mark.asyncio
    async def test_write_update_existing_note(self):
        """当 note_id is provided, tool should update the existing note."""
        updated_note = _make_mock_note(7, "Updated Title", "New content", version=4, tags=["updated"])

        mock_mgr = MagicMock()
        mock_mgr.update_note = AsyncMock(return_value=updated_note)

        tool = NoteWriteTool(note_manager=mock_mgr)
        result = await tool.call(
            _make_mock_ctx(),
            title="Updated Title",
            content="New content",
            note_id=7,
            tags=["updated"],
        )

        assert "Note 7 updated (v4): Updated Title" == result
        mock_mgr.update_note.assert_called_once_with(
            7, title="Updated Title", content="New content", tags=["updated"]
        )

    @pytest.mark.asyncio
    async def test_write_update_note_not_found(self):
        """当 update returns None, tool should report not found."""
        mock_mgr = MagicMock()
        mock_mgr.update_note = AsyncMock(return_value=None)

        tool = NoteWriteTool(note_manager=mock_mgr)
        result = await tool.call(
            _make_mock_ctx(),
            title="Missing",
            content="content",
            note_id=999,
        )

        assert "Note 999 not found." == result

    @pytest.mark.asyncio
    async def test_write_manager_not_available(self):
        """当 note_manager is None, tool should return an error message."""
        tool = NoteWriteTool(note_manager=None)
        result = await tool.call(
            _make_mock_ctx(),
            title="Test",
            content="content",
        )

        assert "Error: note_manager not available" == result

    @pytest.mark.asyncio
    async def test_write_passes_tags_correctly(self):
        """工具 should convert tags list and forward to the manager."""
        mock_mgr = MagicMock()
        mock_mgr.create_note = AsyncMock(return_value=1)

        tool = NoteWriteTool(note_manager=mock_mgr)
        await tool.call(
            _make_mock_ctx(),
            title="Tagged Note",
            content="content",
            tags=["tag1", "tag2"],
        )

        mock_mgr.create_note.assert_called_once_with(
            title="Tagged Note", content="content", tags=["tag1", "tag2"]
        )

    @pytest.mark.asyncio
    async def test_write_rejects_empty_content(self):
        mock_mgr = MagicMock()
        mock_mgr.create_note = AsyncMock(return_value=1)

        tool = NoteWriteTool(note_manager=mock_mgr)
        result = await tool.call(_make_mock_ctx(), title="Title", content="   ")

        assert result.startswith("Error:")
        mock_mgr.create_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_rejects_overlong_title_and_content(self):
        mock_mgr = MagicMock()
        mock_mgr.create_note = AsyncMock(return_value=1)
        tool = NoteWriteTool(note_manager=mock_mgr)

        long_title = "T" * 121
        title_result = await tool.call(
            _make_mock_ctx(),
            title=long_title,
            content="content",
        )
        long_content = "C" * 20001
        content_result = await tool.call(
            _make_mock_ctx(),
            title="Title",
            content=long_content,
        )

        assert "title" in title_result.lower()
        assert "content" in content_result.lower()
        mock_mgr.create_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_rejects_too_many_or_invalid_tags(self):
        mock_mgr = MagicMock()
        mock_mgr.create_note = AsyncMock(return_value=1)
        tool = NoteWriteTool(note_manager=mock_mgr)

        too_many = await tool.call(
            _make_mock_ctx(),
            title="Title",
            content="content",
            tags=[f"tag{i}" for i in range(11)],
        )
        invalid = await tool.call(
            _make_mock_ctx(),
            title="Title",
            content="content",
            tags=["ok", "bad tag"],
        )

        assert "tags" in too_many.lower()
        assert "tag" in invalid.lower()
        mock_mgr.create_note.assert_not_called()
