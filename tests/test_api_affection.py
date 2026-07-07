"""core/api/affection_api.py 测试 — AffectionApiMixin。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.affection_api import AffectionApiMixin


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    return mock


def _make_user(user_id: str = "u1", group_id: str = "g1", level_name: str = "温暖"):
    user = MagicMock()
    user.user_id = user_id
    user.group_id = group_id
    user.affection_score = 42
    user.level = MagicMock()
    user.level.name = "WARM"
    user.level_name = level_name
    user.interaction_count = 5
    user.last_interaction = 1700000000.0
    return user


def _make_mood():
    mood = MagicMock()
    mood.mood_type = MagicMock()
    mood.mood_type.value = "happy"
    mood.intensity = 0.7
    mood.description = "feeling good"
    mood.is_active = MagicMock(return_value=True)
    return mood


def _make_stub(*, has_manager=True, has_store=True, status=None, groups=None, mood=None):
    class Stub:
        get_affection_status = AffectionApiMixin.get_affection_status
        _get_affection_manager = AffectionApiMixin._get_affection_manager
        _get_affection_store = AffectionApiMixin._get_affection_store

    stub = Stub()
    if has_manager or has_store:
        stub.plugin = MagicMock()
        stub.plugin.initializer = None

    if has_manager:
        manager = MagicMock()
        manager.get_group_affection_status = AsyncMock(return_value=status)
        manager.get_mood = AsyncMock(return_value=mood)
        stub.plugin._affection_manager = manager

    if has_store:
        store = MagicMock()
        store.list_groups = AsyncMock(return_value=groups or [])
        stub.plugin._affection_store = store

    return stub


class TestAffectionStatus:
    @pytest.mark.asyncio
    async def test_no_manager_returns_error(self) -> None:
        stub = _make_stub(has_manager=False, has_store=False)
        with patch("core.api.affection_api.request", _mock_request()):
            result = await stub.get_affection_status()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_missing_group_id_falls_back_to_first_store_group(self) -> None:
        status = {
            "group_id": "group-1",
            "total_affection": 42,
            "max_total_affection": 100,
            "user_count": 1,
            "top_users": [_make_user(group_id="group-1")],
            "current_mood": {"mood_type": "happy", "intensity": 0.5},
        }
        stub = _make_stub(status=status, groups=["group-1"])

        with patch("core.api.affection_api.request", _mock_request()):
            result = await stub.get_affection_status()

        assert result["status"] == "ok"
        stub.plugin._affection_manager.get_group_affection_status.assert_awaited_once_with(
            "group-1"
        )
        assert result["data"]["group_id"] == "group-1"
        assert result["data"]["top_users"][0]["user_id"] == "u1"

    @pytest.mark.asyncio
    async def test_fetches_mood_directly_when_status_has_none(self) -> None:
        status = {
            "group_id": "default",
            "total_affection": 0,
            "max_total_affection": 0,
            "user_count": 0,
            "top_users": [],
            "current_mood": None,
        }
        stub = _make_stub(status=status, groups=[], mood=_make_mood())

        with patch("core.api.affection_api.request", _mock_request()):
            result = await stub.get_affection_status()

        assert result["status"] == "ok"
        assert result["data"]["current_mood"]["mood_type"] == "happy"
        assert result["data"]["current_mood"]["is_active"] is True

    @pytest.mark.asyncio
    async def test_missing_group_data_returns_error(self) -> None:
        stub = _make_stub(status=None, groups=["group-x"])

        with patch("core.api.affection_api.request", _mock_request(group_id="group-x")):
            result = await stub.get_affection_status()

        assert result["status"] == "error"
        assert "group-x" in result["message"]

    @pytest.mark.asyncio
    async def test_skips_malformed_top_users(self) -> None:
        broken = MagicMock()
        type(broken).user_id = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken affection user")))
        status = {
            "group_id": "group-1",
            "total_affection": 42,
            "max_total_affection": 100,
            "user_count": 3,
            "top_users": [_make_user(user_id="u1"), broken, _make_user(user_id="u2")],
            "current_mood": {"mood_type": "happy", "intensity": 0.5},
        }
        stub = _make_stub(status=status, groups=["group-1"])

        with patch("core.api.affection_api.request", _mock_request(group_id="group-1")):
            result = await stub.get_affection_status()

        assert result["status"] == "ok"
        assert [item["user_id"] for item in result["data"]["top_users"]] == ["u1", "u2"]

    @pytest.mark.asyncio
    async def test_bad_status_mood_falls_back_to_none(self) -> None:
        broken_mood = MagicMock()
        type(broken_mood).mood_type = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken mood")))
        status = {
            "group_id": "group-1",
            "total_affection": 42,
            "max_total_affection": 100,
            "user_count": 1,
            "top_users": [_make_user(group_id="group-1")],
            "current_mood": broken_mood,
        }
        stub = _make_stub(status=status, groups=["group-1"], mood=None)

        with patch("core.api.affection_api.request", _mock_request(group_id="group-1")):
            result = await stub.get_affection_status()

        assert result["status"] == "ok"
        assert result["data"]["current_mood"] is None
