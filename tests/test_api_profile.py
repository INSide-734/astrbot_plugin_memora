"""core/api/profile_api.py — ProfileApiMixin 测试。

Validates request validation, response format, and error handling.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.profile_api import ProfileApiMixin


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock(return_value=None)
    return mock


def _make_mixin(*, profile_manager_available: bool = True,
                profiles_list: list | None = None,
                profiles_total: int = 0,
                detail_profile=None,
                plugin_ready: bool = True):
    """Create a ProfileApiMixin stub."""

    class Stub:
        list_profiles = ProfileApiMixin.list_profiles
        get_profile_detail = ProfileApiMixin.get_profile_detail
        update_profile = ProfileApiMixin.update_profile
        delete_profile = ProfileApiMixin.delete_profile
        batch_delete_profiles = ProfileApiMixin.batch_delete_profiles
        manage_profile_tags = ProfileApiMixin.manage_profile_tags

        async def _ensure_plugin_ready(self):
            if not plugin_ready:
                return None, {"status": "error", "message": "plugin not ready"}
            if profile_manager_available:
                engine = MagicMock()
                engine.profile_manager = MagicMock()
                engine.profile_manager.list_profiles = AsyncMock(
                    return_value=(profiles_list or [], profiles_total))
                engine.profile_manager.get_profile = AsyncMock(
                    return_value=detail_profile)
                engine.profile_manager.update_profile_fields = AsyncMock(
                    return_value=detail_profile)
                engine.profile_manager.delete_profile = AsyncMock(return_value=True)
                engine.profile_manager.add_tag = AsyncMock(return_value=detail_profile)
                engine.profile_manager.remove_tag = AsyncMock(
                    return_value=detail_profile)
            else:
                engine = MagicMock(spec=[])  # no auto-attrs → getattr returns default
            return {"memory_engine": engine}, None

    return Stub()


def _make_profile(user_id="u1", display_name="Test User"):
    p = MagicMock()
    p.to_dict.return_value = {
        "user_id": user_id, "display_name": display_name,
        "preferences": {}, "tags": [], "interests": []}
    p.user_id = user_id
    p.display_name = display_name
    p.preferences = {}
    return p


class TestProfileValidation:
    """Profile API validates required fields."""

    @pytest.mark.asyncio
    async def test_list_rejects_non_numeric_limit_or_offset(self) -> None:
        req = _mock_request(limit="abc", offset="1x")
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.list_profiles()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_detail_requires_user_id(self) -> None:
        req = _mock_request(user_id="")
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.get_profile_detail()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_requires_user_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"user_id": ""})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.update_profile()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-profile"])
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.update_profile()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_update_rejects_boolean_user_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"user_id": True, "display_name": "New Name"}
        )
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=_make_profile())
            result = await mixin.update_profile()
        assert result["status"] == "error"
        assert "user_id required" in result["message"]

    @pytest.mark.asyncio
    async def test_update_profile_not_found(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "user_id": "u999", "display_name": "New Name"})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=None)
            result = await mixin.update_profile()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_requires_user_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"user_id": ""})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.delete_profile()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_delete_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-profile"])
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.delete_profile()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_rejects_boolean_user_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"user_id": True})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.delete_profile()
        assert result["status"] == "error"
        assert "user_id required" in result["message"]

    @pytest.mark.asyncio
    async def test_batch_delete_requires_ids(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"user_ids": []})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.batch_delete_profiles()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-profile"])
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.batch_delete_profiles()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_manage_tags_requires_user_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"user_id": "", "action": "add"})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_manage_tags_rejects_non_object_json_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value=["bad-profile"])
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    @pytest.mark.asyncio
    async def test_manage_tags_rejects_boolean_user_id(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "user_id": True,
                "action": "add",
                "tag": {"category": "hobby", "value": "reading", "confidence": 0.8},
            }
        )
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=_make_profile())
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"
        assert "user_id required" in result["message"]

    @pytest.mark.asyncio
    async def test_manage_tags_invalid_action(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "user_id": "u1", "action": "invalid"})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_manage_tags_requires_tag_object(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "user_id": "u1", "action": "add", "tag": "not_a_dict"})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=_make_profile())
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_manage_tags_rejects_non_numeric_confidence(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "user_id": "u1",
                "action": "add",
                "tag": {
                    "category": "hobby",
                    "value": "reading",
                    "confidence": "invalid",
                },
            }
        )
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=_make_profile())
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"
        assert "confidence" in result["message"]

    @pytest.mark.asyncio
    async def test_manage_tags_rejects_boolean_confidence(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "user_id": "u1",
                "action": "add",
                "tag": {
                    "category": "hobby",
                    "value": "reading",
                    "confidence": True,
                },
            }
        )
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=_make_profile())
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"
        assert "confidence" in result["message"]


class TestProfileHappyPath:
    """Profile API with mocked manager."""

    @pytest.mark.asyncio
    async def test_list_returns_profiles(self) -> None:
        req = _mock_request()
        p = _make_profile()
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(profiles_list=[p], profiles_total=1)
            result = await mixin.list_profiles()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 1

    @pytest.mark.asyncio
    async def test_list_skips_malformed_profile_items(self) -> None:
        req = _mock_request()
        broken = MagicMock()
        type(broken).to_dict = lambda self: (_ for _ in ()).throw(RuntimeError("broken profile"))
        p1 = _make_profile(user_id="u1", display_name="User 1")
        p2 = _make_profile(user_id="u2", display_name="User 2")
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(profiles_list=[p1, broken, p2], profiles_total=3)
            result = await mixin.list_profiles()
        assert result["status"] == "ok"
        assert result["data"]["total"] == 3
        assert [item["user_id"] for item in result["data"]["profiles"]] == ["u1", "u2"]

    @pytest.mark.asyncio
    async def test_list_tolerates_malformed_profile_container_and_total(self) -> None:
        class BrokenProfiles:
            def __iter__(self):
                raise RuntimeError("broken profiles")

            def __bool__(self):
                return True

        class Stub:
            list_profiles = ProfileApiMixin.list_profiles

            async def _ensure_plugin_ready(self):
                engine = MagicMock()
                engine.profile_manager = MagicMock()
                engine.profile_manager.list_profiles = AsyncMock(
                    return_value=(BrokenProfiles(), "bad-total")
                )
                return {"memory_engine": engine}, None

        req = _mock_request()
        with patch("core.api.profile_api.request", req):
            result = await Stub().list_profiles()
        assert result["status"] == "ok"
        assert result["data"]["profiles"] == []
        assert result["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_get_detail_returns_profile(self) -> None:
        req = _mock_request(user_id="u1")
        p = _make_profile()
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=p)
            result = await mixin.get_profile_detail()
        assert result["status"] == "ok"
        assert result["data"]["user_id"] == "u1"

    @pytest.mark.asyncio
    async def test_get_detail_returns_error_for_malformed_profile_payload(self) -> None:
        req = _mock_request(user_id="u1")
        broken = _make_profile()
        broken.to_dict.side_effect = RuntimeError("broken profile")
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=broken)
            result = await mixin.get_profile_detail()
        assert result["status"] == "error"
        assert "profile serialization failed" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_returns_result(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"user_id": "u1"})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.delete_profile()
        assert result["status"] == "ok"
        assert result["data"]["deleted"] is True

    @pytest.mark.asyncio
    async def test_batch_delete_profiles(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"user_ids": ["u1", "u2", "u3"]})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.batch_delete_profiles()
        assert result["status"] == "ok"
        assert result["data"]["deleted_count"] == 3

    @pytest.mark.asyncio
    async def test_batch_delete_rejects_boolean_user_ids_and_processes_valid_ones(
        self,
    ) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"user_ids": [True, "u2"]})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.batch_delete_profiles()
        assert result["status"] == "ok"
        assert result["data"]["deleted_count"] == 1
        assert result["data"]["failed_count"] == 1
        assert result["data"]["failed_ids"] == [True]

    @pytest.mark.asyncio
    async def test_add_tag(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "user_id": "u1", "action": "add",
            "tag": {"category": "hobby", "value": "reading", "confidence": 0.8}
        })
        p = _make_profile()
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=p)
            result = await mixin.manage_profile_tags()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_add_tag_returns_error_for_malformed_profile_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "user_id": "u1", "action": "add",
            "tag": {"category": "hobby", "value": "reading", "confidence": 0.8}
        })
        broken = _make_profile()
        broken.to_dict.side_effect = RuntimeError("broken profile")
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=broken)
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"
        assert "profile serialization failed" in result["message"]

    @pytest.mark.asyncio
    async def test_remove_tag(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "user_id": "u1", "action": "remove",
            "tag": {"category": "hobby", "value": "reading"}
        })
        p = _make_profile()
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=p)
            result = await mixin.manage_profile_tags()
        assert result["status"] == "ok"


class TestProfileEdgeCases:
    """Additional coverage for profile API edge cases."""

    @pytest.mark.asyncio
    async def test_list_profiles_no_manager(self) -> None:
        req = _mock_request()
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(profile_manager_available=False)
            result = await mixin.list_profiles()
        assert result["status"] == "ok"
        assert result["data"]["profiles"] == []

    @pytest.mark.asyncio
    async def test_get_detail_no_manager(self) -> None:
        req = _mock_request(user_id="u1")
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(profile_manager_available=False)
            result = await mixin.get_profile_detail()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_get_detail_not_found(self) -> None:
        req = _mock_request(user_id="u999")
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=None)
            result = await mixin.get_profile_detail()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_no_manager(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"user_id": "u1"})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(profile_manager_available=False)
            result = await mixin.update_profile()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_update_with_display_name(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "user_id": "u1", "display_name": "New Name", "preferences": {"theme": "dark"}
        })
        p = _make_profile()
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=p)
            result = await mixin.update_profile()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_update_returns_error_for_malformed_profile_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "user_id": "u1", "display_name": "New Name", "preferences": {"theme": "dark"}
        })
        broken = _make_profile()
        broken.to_dict.side_effect = RuntimeError("broken profile")
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=broken)
            result = await mixin.update_profile()
        assert result["status"] == "error"
        assert "profile serialization failed" in result["message"]

    @pytest.mark.asyncio
    async def test_delete_no_manager(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"user_id": "u1"})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(profile_manager_available=False)
            result = await mixin.delete_profile()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_delete_with_failed_ids(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"user_ids": ["u1", "", "u2"]})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.batch_delete_profiles()
        assert result["status"] == "ok"
        assert result["data"]["deleted_count"] == 2
        assert result["data"]["failed_count"] == 1

    @pytest.mark.asyncio
    async def test_batch_delete_no_manager(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"user_ids": ["u1"]})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(profile_manager_available=False)
            result = await mixin.batch_delete_profiles()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_batch_delete_non_list(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"user_ids": "not_a_list"})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.batch_delete_profiles()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_manage_tags_missing_category(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "user_id": "u1", "action": "add",
            "tag": {"category": "", "value": "reading"}
        })
        p = _make_profile()
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=p)
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_manage_tags_no_manager(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "user_id": "u1", "action": "add",
            "tag": {"category": "hobby", "value": "reading"}
        })
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(profile_manager_available=False)
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_manage_tags_profile_not_found(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={
            "user_id": "u999", "action": "add",
            "tag": {"category": "hobby", "value": "reading"}
        })
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=None)
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_plugin_not_ready_list(self) -> None:
        req = _mock_request()
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(plugin_ready=False)
            result = await mixin.list_profiles()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_plugin_not_ready_delete(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(return_value={"user_id": "u1"})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(plugin_ready=False)
            result = await mixin.delete_profile()
        assert result["status"] == "error"
