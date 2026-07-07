"""测试 core/tools/profile_tools.py — ProfileLookupTool."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.tools.profile_tools import ProfileLookupTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_tag(value: str, category: str = "interest", confidence: float = 0.8, count: int = 3) -> MagicMock:
    tag = MagicMock()
    tag.value = value
    tag.category = MagicMock(value=category)
    tag.confidence = confidence
    tag.occurrence_count = count
    return tag


def _make_mock_profile(
    user_id: str = "user-001",
    display_name: str = "TestUser",
    tags: list[MagicMock] | None = None,
) -> MagicMock:
    profile = MagicMock()
    profile.user_id = user_id
    profile.display_name = display_name
    profile.tags = tags or [
        _make_mock_tag("Python", "interest", 0.95, 10),
        _make_mock_tag("Machine Learning", "interest", 0.80, 5),
        _make_mock_tag("Rust", "interest", 0.60, 2),
        _make_mock_tag("introverted", "personality", 0.70, 4),
        _make_mock_tag("detail_oriented", "personality", 0.85, 6),
        _make_mock_tag("morning_person", "habit", 0.75, 3),
    ]

    prefs = MagicMock()
    prefs.reply_style = "concise"
    prefs.preferred_topics = ["Python", "AI"]
    prefs.avoided_topics = ["politics"]
    prefs.avg_reply_length = 120
    profile.preferences = prefs

    profile.total_messages = 150
    profile.total_sessions = 30
    return profile


def _make_mock_context(user_id_from_event: str | None = "event-user-99") -> MagicMock:
    """构建 a ContextWrapper-compatible mock."""
    event = MagicMock()
    if user_id_from_event:
        event.get_sender_id.return_value = user_id_from_event
    else:
        del event.get_sender_id  # remove attribute so hasattr fails

    inner_ctx = MagicMock()
    inner_ctx.event = event

    wrapper = MagicMock()
    wrapper.context = inner_ctx
    return wrapper


# ---------------------------------------------------------------------------
# ProfileLookupTool
# ---------------------------------------------------------------------------


class TestProfileLookupTool:
    """测试 ProfileLookupTool 定义与执行。"""

    def test_tool_definition_has_correct_name_and_params(self):
        """工具 should expose name, description, and parameters schema with optional user_id."""
        tool = ProfileLookupTool()

        assert tool.name == "profile_lookup"
        assert "Look up the user profile" in tool.description
        assert tool.parameters["type"] == "object"
        assert "user_id" in tool.parameters["properties"]
        assert tool.parameters["required"] == []

    @pytest.mark.asyncio
    async def test_lookup_happy_path_with_user_id(self):
        """当 a user_id is provided, tool should return full profile data."""
        profile = _make_mock_profile("user-42", "Alice")
        mock_mgr = MagicMock()
        mock_mgr.get_profile = AsyncMock(return_value=profile)
        mock_mgr.get_tag_weights = AsyncMock(return_value={"Python": 0.9, "AI": 0.7})

        tool = ProfileLookupTool(profile_manager=mock_mgr)
        result = await tool.call(_make_mock_context(), user_id="user-42")

        data = json.loads(result)
        assert data["user_id"] == "user-42"
        assert data["found"] is True
        assert data["display_name"] == "Alice"
        assert "tags_by_category" in data
        assert "interest" in data["tags_by_category"]
        assert "personality" in data["tags_by_category"]
        assert "habit" in data["tags_by_category"]
        assert data["tag_weights"] == {"Python": 0.9, "AI": 0.7}
        assert data["preferences"]["reply_style"] == "concise"
        assert data["preferences"]["preferred_topics"] == ["Python", "AI"]
        assert data["preferences"]["avoided_topics"] == ["politics"]
        assert data["stats"]["total_messages"] == 150
        assert data["stats"]["total_sessions"] == 30

    @pytest.mark.asyncio
    async def test_lookup_infers_user_id_from_event(self):
        """当 user_id is omitted, tool should infer it from the event context."""
        profile = _make_mock_profile("event-user-99", "Bob")
        mock_mgr = MagicMock()
        mock_mgr.get_profile = AsyncMock(return_value=profile)
        mock_mgr.get_tag_weights = AsyncMock(return_value={})

        tool = ProfileLookupTool(profile_manager=mock_mgr)
        result = await tool.call(_make_mock_context("event-user-99"), user_id="")

        data = json.loads(result)
        assert data["user_id"] == "event-user-99"
        assert data["found"] is True
        mock_mgr.get_profile.assert_called_once_with("event-user-99")

    @pytest.mark.asyncio
    async def test_lookup_user_id_empty_and_cannot_infer(self):
        """当 both user_id and event context are empty, tool should return error."""
        tool = ProfileLookupTool(profile_manager=MagicMock())
        # Context with no get_sender_id and no user_id provided
        ctx = _make_mock_context(user_id_from_event=None)
        ctx.context.event.unified_msg_origin = ""

        result = await tool.call(ctx, user_id="")

        data = json.loads(result)
        assert data["found"] is False
        assert "user_id is empty" in data["error"]

    @pytest.mark.asyncio
    async def test_lookup_manager_not_available(self):
        """当 profile_manager is None, tool should return an error."""
        tool = ProfileLookupTool(profile_manager=None)
        result = await tool.call(_make_mock_context(), user_id="user-1")

        data = json.loads(result)
        assert data["found"] is False
        assert data["error"] == "profile_manager not available"

    @pytest.mark.asyncio
    async def test_lookup_profile_not_found(self):
        """当 get_profile returns None, tool should report found=False."""
        mock_mgr = MagicMock()
        mock_mgr.get_profile = AsyncMock(return_value=None)

        tool = ProfileLookupTool(profile_manager=mock_mgr)
        result = await tool.call(_make_mock_context(), user_id="nonexistent")

        data = json.loads(result)
        assert data["user_id"] == "nonexistent"
        assert data["found"] is False

    @pytest.mark.asyncio
    async def test_lookup_manager_raises_exception(self):
        """当 get_profile raises, tool should catch and return error."""
        mock_mgr = MagicMock()
        mock_mgr.get_profile = AsyncMock(side_effect=RuntimeError("DB down"))

        tool = ProfileLookupTool(profile_manager=mock_mgr)
        result = await tool.call(_make_mock_context(), user_id="user-1")

        data = json.loads(result)
        assert data["found"] is False
        assert data["error"] == "lookup_failed"

    @pytest.mark.asyncio
    async def test_lookup_tags_truncated_to_top5_per_category(self):
        """Each category should only return top 5 tags by confidence."""
        tags = [_make_mock_tag(f"interest-{i}", "interest", 0.9 - i * 0.05, 1) for i in range(10)]
        profile = _make_mock_profile("u1", tags=tags)
        mock_mgr = MagicMock()
        mock_mgr.get_profile = AsyncMock(return_value=profile)
        mock_mgr.get_tag_weights = AsyncMock(return_value={})

        tool = ProfileLookupTool(profile_manager=mock_mgr)
        result = await tool.call(_make_mock_context(), user_id="u1")

        data = json.loads(result)
        interest_tags = data["tags_by_category"]["interest"]
        assert len(interest_tags) == 5
        # First tag should have highest confidence
        assert interest_tags[0]["confidence"] == 0.9
        assert interest_tags[-1]["confidence"] == 0.7  # 0.9 - 4*0.05

    @pytest.mark.asyncio
    async def test_lookup_tag_weights_failure_graceful(self):
        """当 get_tag_weights raises, tool should fallback to empty dict and still return profile."""
        profile = _make_mock_profile("u1")
        mock_mgr = MagicMock()
        mock_mgr.get_profile = AsyncMock(return_value=profile)
        mock_mgr.get_tag_weights = AsyncMock(side_effect=RuntimeError("weights unavailable"))

        tool = ProfileLookupTool(profile_manager=mock_mgr)
        result = await tool.call(_make_mock_context(), user_id="u1")

        data = json.loads(result)
        assert data["found"] is True
        assert data["tag_weights"] == {}
