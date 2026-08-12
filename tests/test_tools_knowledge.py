"""测试 core/tools/knowledge_tools.py — KnowledgeSearchTool, KnowledgeReadTool."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot.api.platform import MessageType

from core.tools.knowledge_tools import KnowledgeReadTool, KnowledgeSearchTool
from tests.tool_contract_support import call_text_handler

# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------


async def _call_text(tool: Any, event: Any, **kwargs: Any) -> str:
    """通过公开 handler 调用 Agent 工具，并断言文本返回契约。"""

    return await call_text_handler(tool, event, **kwargs)


def _make_mock_knowledge_entry(
    entry_id: int = 1,
    title: str = "Test Rule",
    content: str = "A test rule about something.",
    category: str = "rule",
    confidence: float = 0.85,
    tags: list[str] | None = None,
    source_ids: list[str] | None = None,
    access_count: int = 3,
    created_at: str = "2026-06-01T00:00:00",
    updated_at: str = "2026-06-10T00:00:00",
) -> MagicMock:
    """构造知识搜索和读取断言所需的最小条目替身。"""

    entry = MagicMock()
    entry.entry_id = entry_id
    entry.title = title
    entry.content = content
    entry.category = MagicMock(value=category)
    entry.confidence = confidence
    entry.tags = tags or []
    entry.source_ids = source_ids or []
    entry.access_count = access_count
    entry.created_at = created_at
    entry.updated_at = updated_at
    return entry


def _make_mock_event() -> MagicMock:
    """构造公开工具 handler 所需的最小消息事件。"""

    event = MagicMock()
    event.unified_msg_origin = "private:user-001"
    event.get_sender_id.return_value = "user-001"
    event.get_message_type.return_value = MessageType.FRIEND_MESSAGE
    event.get_extra.return_value = SimpleNamespace(trust_status="unsupported")
    return event


# ---------------------------------------------------------------------------
# 知识搜索工具
# ---------------------------------------------------------------------------


class TestKnowledgeSearchTool:
    """测试 KnowledgeSearchTool 定义与执行。"""

    def test_tool_definition_has_correct_name_and_params(self):
        """工具应公开稳定名称、描述和必需查询参数。"""
        tool = KnowledgeSearchTool()

        assert tool.name == "knowledge_search"
        assert "Search the structured knowledge base" in tool.description
        assert tool.parameters["type"] == "object"
        assert "query" in tool.parameters["properties"]
        assert "limit" in tool.parameters["properties"]
        assert "category" in tool.parameters["properties"]
        assert tool.parameters["required"] == ["query"]

    @pytest.mark.asyncio
    async def test_search_happy_path_returns_entries(self):
        """管理器返回知识条目时工具应稳定序列化结果。"""
        entry1 = _make_mock_knowledge_entry(
            1, "Rule A", "Content A", category="rule", confidence=0.9
        )
        entry2 = _make_mock_knowledge_entry(
            2, "Concept B", "Content B", category="concept"
        )

        mock_mgr = MagicMock()
        mock_mgr.search_for_scope = AsyncMock(return_value=([entry1, entry2], 2))

        tool = KnowledgeSearchTool(knowledge_manager=mock_mgr)
        result = await _call_text(tool, _make_mock_event(), query="test")

        data = json.loads(result)
        assert data["query"] == "test"
        assert data["count"] == 2
        assert len(data["results"]) == 2
        assert data["results"][0]["entry_id"] == 1
        assert data["results"][0]["title"] == "Rule A"
        assert data["results"][1]["entry_id"] == 2

    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        """搜索没有条目时应返回空结果和零计数。"""
        mock_mgr = MagicMock()
        mock_mgr.search_for_scope = AsyncMock(return_value=([], 0))

        tool = KnowledgeSearchTool(knowledge_manager=mock_mgr)
        result = await _call_text(tool, _make_mock_event(), query="nonexistent")

        data = json.loads(result)
        assert data["count"] == 0
        assert data["results"] == []

    @pytest.mark.asyncio
    async def test_search_manager_not_available(self):
        """知识管理器缺失时应返回稳定错误对象。"""
        tool = KnowledgeSearchTool(knowledge_manager=None)
        result = await _call_text(tool, _make_mock_event(), query="test")

        data = json.loads(result)
        assert data["count"] == 0
        assert data["results"] == []
        assert data["error"] == "knowledge_manager not available"

    @pytest.mark.asyncio
    async def test_search_manager_raises_exception(self):
        """知识搜索普通异常应隔离为稳定错误。"""
        mock_mgr = MagicMock()
        mock_mgr.search_for_scope = AsyncMock(side_effect=RuntimeError("DB down"))

        tool = KnowledgeSearchTool(knowledge_manager=mock_mgr)
        result = await _call_text(tool, _make_mock_event(), query="test")

        data = json.loads(result)
        assert data["count"] == 0
        assert data["error"] == "search_failed"

    @pytest.mark.asyncio
    async def test_search_passes_limit_and_category(self):
        """工具应把数量上限与分类参数传给管理器。"""
        mock_mgr = MagicMock()
        mock_mgr.search_for_scope = AsyncMock(return_value=([], 0))

        tool = KnowledgeSearchTool(knowledge_manager=mock_mgr)
        await _call_text(
            tool,
            _make_mock_event(),
            query="q",
            limit=5,
            category="rule",
        )

        mock_mgr.search_for_scope.assert_called_once_with(
            "q", scope_key="private:user-001", limit=5, category="rule"
        )


# ---------------------------------------------------------------------------
# 知识读取工具
# ---------------------------------------------------------------------------


class TestKnowledgeReadTool:
    """测试 KnowledgeReadTool 定义与执行。"""

    def test_tool_definition_has_correct_name_and_params(self):
        """工具应公开稳定名称、描述和必需条目标识。"""
        tool = KnowledgeReadTool()

        assert tool.name == "knowledge_read"
        assert "Read the full content" in tool.description
        assert tool.parameters["type"] == "object"
        assert "entry_id" in tool.parameters["properties"]
        assert tool.parameters["required"] == ["entry_id"]

    @pytest.mark.asyncio
    async def test_read_happy_path_returns_full_entry(self):
        """管理器返回条目时工具应序列化全部公开字段。"""
        entry = _make_mock_knowledge_entry(7, "Full Rule", "Detailed content here...")

        mock_mgr = MagicMock()
        mock_mgr.get_entry_for_scope = AsyncMock(return_value=entry)

        tool = KnowledgeReadTool(knowledge_manager=mock_mgr)
        result = await _call_text(tool, _make_mock_event(), entry_id=7)

        data = json.loads(result)
        assert data["entry_id"] == 7
        assert data["found"] is True
        assert data["title"] == "Full Rule"
        assert data["content"] == "Detailed content here..."
        assert data["category"] == "rule"
        assert data["confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_read_entry_not_found(self):
        """条目不存在时应报告未命中。"""
        mock_mgr = MagicMock()
        mock_mgr.get_entry_for_scope = AsyncMock(return_value=None)

        tool = KnowledgeReadTool(knowledge_manager=mock_mgr)
        result = await _call_text(tool, _make_mock_event(), entry_id=999)

        data = json.loads(result)
        assert data["entry_id"] == 999
        assert data["found"] is False

    @pytest.mark.asyncio
    async def test_read_manager_not_available(self):
        """知识管理器缺失时应返回稳定错误。"""
        tool = KnowledgeReadTool(knowledge_manager=None)
        result = await _call_text(tool, _make_mock_event(), entry_id=1)

        data = json.loads(result)
        assert data["found"] is False
        assert data["error"] == "knowledge_manager not available"

    @pytest.mark.asyncio
    async def test_read_manager_raises_exception(self):
        """知识读取普通异常应隔离为稳定错误。"""
        mock_mgr = MagicMock()
        mock_mgr.get_entry_for_scope = AsyncMock(side_effect=RuntimeError("DB down"))

        tool = KnowledgeReadTool(knowledge_manager=mock_mgr)
        result = await _call_text(tool, _make_mock_event(), entry_id=1)

        data = json.loads(result)
        assert data["found"] is False
        assert data["error"] == "read_failed"


# ---------------------------------------------------------------------------
# 包级导出
# ---------------------------------------------------------------------------


def test_init_exports_all_tools():
    """工具包入口应导出全部公开工具类。"""
    from core.tools import __all__ as tools_all

    expected = {
        "KnowledgeSearchTool",
        "KnowledgeReadTool",
        "MemoryMemorizeTool",
        "MemorySearchTool",
        "NoteReadTool",
        "NoteSearchTool",
        "NoteWriteTool",
        "ProfileLookupTool",
        "JargonExplainTool",
        "JargonListTool",
        "AffectionCheckTool",
        "BotMoodTool",
        "RelationLookupTool",
        "RelationGraphTool",
        "ExpressionRecallTool",
    }
    assert set(tools_all) == expected
