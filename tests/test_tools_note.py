"""测试 core/tools/note_tools.py — NoteSearchTool, NoteReadTool, NoteWriteTool."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot.api.platform import MessageType

from core.platform.transport.tools.note_tools import (
    NoteReadTool,
    NoteSearchTool,
    NoteWriteTool,
)
from tests.tool_contract_support import call_text_handler

# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


async def _call_text(tool: Any, event: Any, **kwargs: Any) -> str:
    """通过公开 handler 调用 Agent 工具，并断言文本返回契约。"""

    return await call_text_handler(tool, event, **kwargs)


def _make_mock_note(
    note_id: int = 1,
    title: str = "Test Note",
    content: str = "Note content here.",
    version: int = 1,
    tags: list[str] | None = None,
) -> MagicMock:
    """构造笔记搜索和读取断言所需的最小条目替身。"""

    note = MagicMock()
    note.note_id = note_id
    note.title = title
    note.content = content
    note.version = version
    note.tags = tags or []
    return note


def _make_mock_event() -> MagicMock:
    """构造公开工具 handler 所需的最小消息事件。"""

    event = MagicMock()
    event.unified_msg_origin = "private:user-001"
    event.get_sender_id.return_value = "user-001"
    event.get_message_type.return_value = MessageType.FRIEND_MESSAGE
    event.get_extra.return_value = SimpleNamespace(trust_status="unsupported")
    return event


# ---------------------------------------------------------------------------
# 笔记搜索工具
# ---------------------------------------------------------------------------


class TestNoteSearchTool:
    """测试 NoteSearchTool 定义与执行。"""

    def test_tool_definition_has_correct_name_and_params(self):
        """工具应公开稳定名称、描述和必需查询参数。"""
        tool = NoteSearchTool()

        assert tool.name == "note_search"
        assert "Search notes by keyword" in tool.description
        assert tool.parameters["type"] == "object"
        assert "query" in tool.parameters["properties"]
        assert "limit" in tool.parameters["properties"]
        assert tool.parameters["required"] == ["query"]

    @pytest.mark.asyncio
    async def test_search_happy_path_returns_formatted_list(self):
        """管理器返回笔记时工具应格式化为文本列表。"""
        note1 = _make_mock_note(
            1, "Shopping List", "Buy milk, eggs, bread.", version=2, tags=["shopping"]
        )
        note2 = _make_mock_note(
            2, "Meeting Notes", "Discussed Q3 roadmap.", version=1, tags=["work"]
        )

        mock_mgr = MagicMock()
        mock_mgr.search_for_scope = AsyncMock(return_value=([note1, note2], 2))

        tool = NoteSearchTool(note_manager=mock_mgr)
        result = await _call_text(tool, _make_mock_event(), query="shop")

        assert "Found 2 note(s)" in result
        assert "[1] Shopping List (v2)" in result
        assert "[2] Meeting Notes (v1)" in result
        assert "Buy milk, eggs, bread." in result

    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        """搜索没有笔记时应返回未命中文本。"""
        mock_mgr = MagicMock()
        mock_mgr.search_for_scope = AsyncMock(return_value=([], 0))

        tool = NoteSearchTool(note_manager=mock_mgr)
        result = await _call_text(tool, _make_mock_event(), query="nothing")

        assert "No notes found for: nothing" == result

    @pytest.mark.asyncio
    async def test_search_manager_not_available(self):
        """笔记管理器缺失时应返回稳定错误文本。"""
        tool = NoteSearchTool(note_manager=None)
        result = await _call_text(tool, _make_mock_event(), query="test")

        assert "Error: note_manager not available" == result

    @pytest.mark.asyncio
    async def test_search_passes_limit_parameter(self):
        """工具应把数量上限传给管理器。"""
        mock_mgr = MagicMock()
        mock_mgr.search_for_scope = AsyncMock(return_value=([], 0))

        tool = NoteSearchTool(note_manager=mock_mgr)
        await _call_text(tool, _make_mock_event(), query="q", limit=5)

        mock_mgr.search_for_scope.assert_called_once_with(
            "q",
            scope_key="private:user-001",
            user_id="user-001",
            limit=5,
        )


# ---------------------------------------------------------------------------
# 笔记读取工具
# ---------------------------------------------------------------------------


class TestNoteReadTool:
    """测试 NoteReadTool 定义与执行。"""

    def test_tool_definition_has_correct_name_and_params(self):
        """工具应公开稳定名称、描述和必需笔记标识。"""
        tool = NoteReadTool()

        assert tool.name == "note_read"
        assert "Read a note's full content" in tool.description
        assert tool.parameters["type"] == "object"
        assert "note_id" in tool.parameters["properties"]
        assert tool.parameters["required"] == ["note_id"]

    @pytest.mark.asyncio
    async def test_read_happy_path_returns_full_note(self):
        """管理器返回笔记时工具应格式化完整正文。"""
        note = _make_mock_note(
            3, "My Note", "Full content\nLine 2.", version=3, tags=["a", "b"]
        )

        mock_mgr = MagicMock()
        mock_mgr.get_note_for_scope = AsyncMock(return_value=note)

        tool = NoteReadTool(note_manager=mock_mgr)
        result = await _call_text(tool, _make_mock_event(), note_id=3)

        assert "# My Note" in result
        assert "Full content\nLine 2." in result
        assert "Tags: a, b" in result
        assert "Version: 3" in result

    @pytest.mark.asyncio
    async def test_read_note_not_found(self):
        """笔记不存在时应返回未命中文本。"""
        mock_mgr = MagicMock()
        mock_mgr.get_note_for_scope = AsyncMock(return_value=None)

        tool = NoteReadTool(note_manager=mock_mgr)
        result = await _call_text(tool, _make_mock_event(), note_id=999)

        assert "Note 999 not found." == result

    @pytest.mark.asyncio
    async def test_read_manager_not_available(self):
        """笔记管理器缺失时应返回稳定错误文本。"""
        tool = NoteReadTool(note_manager=None)
        result = await _call_text(tool, _make_mock_event(), note_id=1)

        assert "Error: note_manager not available" == result


# ---------------------------------------------------------------------------
# 笔记写入工具
# ---------------------------------------------------------------------------


class TestNoteWriteTool:
    """测试 NoteWriteTool 定义与执行。"""

    def test_tool_definition_has_correct_name_and_params(self):
        """工具应公开稳定名称、描述及必需标题和正文参数。"""
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
        """未提供笔记标识时应创建新笔记。"""
        mock_mgr = MagicMock()
        mock_mgr.create_note = AsyncMock(return_value=42)

        tool = NoteWriteTool(note_manager=mock_mgr)
        result = await _call_text(
            tool,
            _make_mock_event(),
            title="New Note",
            content="Some content",
        )

        assert "Note 42 created: New Note" == result
        mock_mgr.create_note.assert_called_once_with(
            title="New Note", content="Some content", tags=[], user_id="user-001"
        )

    @pytest.mark.asyncio
    async def test_write_update_existing_note(self):
        """提供笔记标识时应更新已有笔记。"""
        updated_note = _make_mock_note(
            7, "Updated Title", "New content", version=4, tags=["updated"]
        )

        mock_mgr = MagicMock()
        mock_mgr.update_note_for_scope = AsyncMock(return_value=updated_note)

        tool = NoteWriteTool(note_manager=mock_mgr)
        result = await _call_text(
            tool,
            _make_mock_event(),
            title="Updated Title",
            content="New content",
            note_id=7,
            tags=["updated"],
        )

        assert "Note 7 updated (v4): Updated Title" == result
        mock_mgr.update_note_for_scope.assert_called_once_with(
            7,
            scope_key="private:user-001",
            user_id="user-001",
            title="Updated Title",
            content="New content",
            tags=["updated"],
        )

    @pytest.mark.asyncio
    async def test_write_update_note_not_found(self):
        """待更新笔记不存在时应返回未命中文本。"""
        mock_mgr = MagicMock()
        mock_mgr.update_note_for_scope = AsyncMock(return_value=None)

        tool = NoteWriteTool(note_manager=mock_mgr)
        result = await _call_text(
            tool,
            _make_mock_event(),
            title="Missing",
            content="content",
            note_id=999,
        )

        assert "Note 999 not found." == result

    @pytest.mark.asyncio
    async def test_write_manager_not_available(self):
        """笔记管理器缺失时应返回稳定错误文本。"""
        tool = NoteWriteTool(note_manager=None)
        result = await _call_text(
            tool,
            _make_mock_event(),
            title="Test",
            content="content",
        )

        assert "Error: note_manager not available" == result

    @pytest.mark.asyncio
    async def test_write_passes_tags_correctly(self):
        """工具应把标签列表原样传给管理器。"""
        mock_mgr = MagicMock()
        mock_mgr.create_note = AsyncMock(return_value=1)

        tool = NoteWriteTool(note_manager=mock_mgr)
        await _call_text(
            tool,
            _make_mock_event(),
            title="Tagged Note",
            content="content",
            tags=["tag1", "tag2"],
        )

        mock_mgr.create_note.assert_called_once_with(
            title="Tagged Note",
            content="content",
            tags=["tag1", "tag2"],
            user_id="user-001",
        )

    @pytest.mark.asyncio
    async def test_write_rejects_empty_content(self):
        """空白正文应在调用管理器前被拒绝。"""

        mock_mgr = MagicMock()
        mock_mgr.create_note = AsyncMock(return_value=1)

        tool = NoteWriteTool(note_manager=mock_mgr)
        result = await _call_text(
            tool, _make_mock_event(), title="Title", content="   "
        )

        assert result.startswith("Error:")
        mock_mgr.create_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_rejects_overlong_title_and_content(self):
        """超长标题或正文应在调用管理器前被拒绝。"""

        mock_mgr = MagicMock()
        mock_mgr.create_note = AsyncMock(return_value=1)
        tool = NoteWriteTool(note_manager=mock_mgr)

        long_title = "T" * 121
        title_result = await _call_text(
            tool,
            _make_mock_event(),
            title=long_title,
            content="content",
        )
        long_content = "C" * 20001
        content_result = await _call_text(
            tool,
            _make_mock_event(),
            title="Title",
            content=long_content,
        )

        assert "title" in title_result.lower()
        assert "content" in content_result.lower()
        mock_mgr.create_note.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_rejects_too_many_or_invalid_tags(self):
        """过多标签或含非法字符的标签应在写入前被拒绝。"""

        mock_mgr = MagicMock()
        mock_mgr.create_note = AsyncMock(return_value=1)
        tool = NoteWriteTool(note_manager=mock_mgr)

        too_many = await _call_text(
            tool,
            _make_mock_event(),
            title="Title",
            content="content",
            tags=[f"tag{i}" for i in range(11)],
        )
        invalid = await _call_text(
            tool,
            _make_mock_event(),
            title="Title",
            content="content",
            tags=["ok", "bad tag"],
        )

        assert "tags" in too_many.lower()
        assert "tag" in invalid.lower()
        mock_mgr.create_note.assert_not_called()
