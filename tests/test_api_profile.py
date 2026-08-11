"""验证 ProfileApiMixin 的请求校验、响应格式和错误处理。"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api.profile_api import ProfileApiMixin
from core.features.profiles.application.profile_manager import ProfileManager
from core.features.profiles.infrastructure.profile_store import ProfileStore
from core.shared.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)
from core.shared.list_sorting import SortQuery


def _mock_request(**args):
    mock = MagicMock()
    mock.args = args
    mock.get_json = AsyncMock(return_value=None)
    return mock


def _make_mixin(
    *,
    profile_manager_available: bool = True,
    profiles_list: list | None = None,
    profiles_total: int = 0,
    detail_profile=None,
    plugin_ready: bool = True,
):
    """创建带可控画像依赖的 ProfileApiMixin 测试替身。"""

    engine = MagicMock(spec=[])
    profile_manager = None
    if profile_manager_available:
        profile_manager = MagicMock()
        profile_manager.list_profiles = AsyncMock(
            return_value=(profiles_list or [], profiles_total)
        )
        profile_manager.get_profile = AsyncMock(return_value=detail_profile)
        profile_manager.create_profile_manual = AsyncMock(return_value=detail_profile)
        profile_manager.update_profile_manual = AsyncMock(return_value=detail_profile)
        profile_manager.delete_profile_manual = AsyncMock(return_value=True)
        profile_manager.revision_for = MagicMock(return_value="rev-profile")
        profile_manager.update_profile_fields = AsyncMock(return_value=detail_profile)
        profile_manager.delete_profile = AsyncMock(return_value=True)
        profile_manager.add_tag = AsyncMock(return_value=detail_profile)
        profile_manager.remove_tag = AsyncMock(return_value=detail_profile)
        engine.profile_manager = profile_manager

    class Stub(ProfileApiMixin):
        list_profiles = ProfileApiMixin.list_profiles
        get_profile_detail = ProfileApiMixin.get_profile_detail
        update_profile = ProfileApiMixin.update_profile
        delete_profile = ProfileApiMixin.delete_profile
        batch_delete_profiles = ProfileApiMixin.batch_delete_profiles
        manage_profile_tags = ProfileApiMixin.manage_profile_tags
        _update_profile_envelope = ProfileApiMixin._update_profile_envelope
        _delete_profile_envelope = ProfileApiMixin._delete_profile_envelope
        _legacy_batch_delete_profiles = ProfileApiMixin._legacy_batch_delete_profiles
        _batch_profile_actions = ProfileApiMixin._batch_profile_actions

        async def create_profile(self):
            return await ProfileApiMixin.create_profile(self)

        async def _ensure_plugin_ready(self):
            if not plugin_ready:
                return None, {"status": "error", "message": "plugin not ready"}
            return {"memory_engine": engine}, None

    stub: Any = Stub()
    stub.profile_manager = profile_manager
    return stub


def _make_profile(
    user_id="u1",
    display_name="Test User",
    *,
    preferences=None,
    tags=None,
):
    preferences = preferences or {}
    tags = tags or []
    p = MagicMock()
    p.to_dict.return_value = {
        "user_id": user_id,
        "display_name": display_name,
        "preferences": preferences,
        "tags": tags,
        "interests": [],
    }
    p.user_id = user_id
    p.display_name = display_name
    p.preferences = preferences
    p.tags = tags
    return p


class TestProfileValidation:
    """验证画像 API 的必填字段。"""

    @pytest.mark.asyncio
    async def test_list_rejects_non_numeric_limit_or_offset(self) -> None:
        req = _mock_request(limit="abc", offset="1x")
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.list_profiles()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("sort_by", "sort_order", "field"),
        [
            ("display_name; DROP TABLE user_profiles", "asc", "sort_by"),
            ("missing", "asc", "sort_by"),
            ("display_name", "DESC", "sort_order"),
            ("display_name", "sideways", "sort_order"),
        ],
    )
    async def test_list_rejects_invalid_sort_values(
        self, sort_by: str, sort_order: str, field: str
    ) -> None:
        req = _mock_request(sort_by=sort_by, sort_order=sort_order)
        mixin = _make_mixin()

        with patch("core.api.profile_api.request", req):
            result = await mixin.list_profiles()

        assert result["status"] == "error"
        assert result["code"] == "invalid_query"
        assert field in result["field_errors"]
        mixin.profile_manager.list_profiles.assert_not_awaited()

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
        req.get_json = AsyncMock(
            return_value={"user_id": "u999", "display_name": "New Name"}
        )
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
                "tag": {"category": "interest", "value": "reading", "confidence": 0.8},
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
        req.get_json = AsyncMock(return_value={"user_id": "u1", "action": "invalid"})
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin()
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_manage_tags_requires_tag_object(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"user_id": "u1", "action": "add", "tag": "not_a_dict"}
        )
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
                    "category": "interest",
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
                    "category": "interest",
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

    @pytest.mark.asyncio
    @pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -0.1, 1.1])
    async def test_manage_tags_rejects_non_finite_or_out_of_range_confidence(
        self, confidence
    ) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "user_id": "u1",
                "action": "add",
                "tag": {
                    "category": "interest",
                    "value": "reading",
                    "confidence": confidence,
                },
            }
        )
        mixin = _make_mixin(detail_profile=_make_profile())
        with patch("core.api.profile_api.request", req):
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"
        mixin.profile_manager.add_tag.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_manage_tags_rejects_client_source_before_mutation(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "user_id": "u1",
                "action": "add",
                "tag": {
                    "category": "interest",
                    "value": "reading",
                    "confidence": 0.8,
                    "source": "imported-secret",
                },
            }
        )
        mixin = _make_mixin(detail_profile=_make_profile())
        with patch("core.api.profile_api.request", req):
            result = await mixin.manage_profile_tags()
        assert result["code"] == "validation_error"
        mixin.profile_manager.add_tag.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["avg_reply_length", "interaction_frequency"])
    async def test_legacy_update_rejects_derived_preferences(self, field) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={"user_id": "u1", "preferences": {field: 12}}
        )
        mixin = _make_mixin(detail_profile=_make_profile())
        with patch("core.api.profile_api.request", req):
            result = await mixin.update_profile()
        assert result["code"] == "validation_error"
        mixin.profile_manager.update_profile_fields.assert_not_awaited()


class TestProfileHappyPath:
    """使用模拟 Manager 验证画像 API。"""

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
    async def test_list_forwards_valid_sort_query(self) -> None:
        req = _mock_request(
            limit="25",
            offset="50",
            sort_by="total_messages",
            sort_order="desc",
        )
        mixin = _make_mixin()

        with patch("core.api.profile_api.request", req):
            result = await mixin.list_profiles()

        assert result["status"] == "ok"
        mixin.profile_manager.list_profiles.assert_awaited_once_with(
            limit=25,
            offset=50,
            sort=SortQuery("total_messages", "desc"),
        )

    @pytest.mark.asyncio
    async def test_list_skips_malformed_profile_items(self) -> None:
        req = _mock_request()
        broken = MagicMock()
        type(broken).to_dict = lambda self: (_ for _ in ()).throw(
            RuntimeError("broken profile")
        )
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
        assert result["message"] == "用户画像操作失败"
        assert "broken profile" not in repr(result)

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
        req.get_json = AsyncMock(
            return_value={
                "user_id": "u1",
                "action": "add",
                "tag": {"category": "interest", "value": "reading", "confidence": 0.8},
            }
        )
        p = _make_profile()
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=p)
            result = await mixin.manage_profile_tags()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_add_tag_returns_error_for_malformed_profile_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "user_id": "u1",
                "action": "add",
                "tag": {"category": "interest", "value": "reading", "confidence": 0.8},
            }
        )
        broken = _make_profile()
        broken.to_dict.side_effect = RuntimeError("broken profile")
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=broken)
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"
        assert result["message"] == "用户画像操作失败"
        assert "broken profile" not in repr(result)

    @pytest.mark.asyncio
    async def test_remove_tag(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "user_id": "u1",
                "action": "remove",
                "tag": {"category": "interest", "value": "reading"},
            }
        )
        p = _make_profile()
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=p)
            result = await mixin.manage_profile_tags()
        assert result["status"] == "ok"


class TestProfileEdgeCases:
    """补充验证画像 API 的边界场景。"""

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
        req.get_json = AsyncMock(
            return_value={
                "user_id": "u1",
                "display_name": "New Name",
                "preferences": {"reply_style": "formal"},
            }
        )
        p = _make_profile()
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=p)
            result = await mixin.update_profile()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_update_returns_error_for_malformed_profile_payload(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "user_id": "u1",
                "display_name": "New Name",
                "preferences": {"reply_style": "formal"},
            }
        )
        broken = _make_profile()
        broken.to_dict.side_effect = RuntimeError("broken profile")
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=broken)
            result = await mixin.update_profile()
        assert result["status"] == "error"
        assert result["message"] == "用户画像操作失败"
        assert "broken profile" not in repr(result)

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
        req.get_json = AsyncMock(
            return_value={
                "user_id": "u1",
                "action": "add",
                "tag": {"category": "", "value": "reading"},
            }
        )
        p = _make_profile()
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(detail_profile=p)
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_manage_tags_no_manager(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "user_id": "u1",
                "action": "add",
                "tag": {"category": "interest", "value": "reading"},
            }
        )
        with patch("core.api.profile_api.request", req):
            mixin = _make_mixin(profile_manager_available=False)
            result = await mixin.manage_profile_tags()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_manage_tags_profile_not_found(self) -> None:
        req = _mock_request()
        req.get_json = AsyncMock(
            return_value={
                "user_id": "u999",
                "action": "add",
                "tag": {"category": "interest", "value": "reading"},
            }
        )
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


def _complete_profile_payload(user_id: str = "u1") -> dict:
    return {
        "user_id": user_id,
        "display_name": "Alice",
        "preferences": {
            "reply_style": "formal",
            "preferred_topics": ["graphs"],
            "avoided_topics": [],
            "active_hours": [9, 10],
        },
        "tags": [
            {
                "category": "interest",
                "value": "graphs",
                "confidence": 0.9,
            }
        ],
    }


def _update_envelope(user_id: str = "u1") -> dict:
    payload = _complete_profile_payload(user_id)
    return {
        "identity": {"user_id": payload.pop("user_id")},
        "changes": payload,
        "expected_revision": "rev-old",
    }


def _profile_audit_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if "[画像 AUDIT]" in record.getMessage()
    ]


def _profile_audit(
    action: str,
    identity,
    *,
    result: str,
    error_code: str = "none",
    error_class: str = "none",
) -> str:
    return (
        f"[画像 AUDIT] action={action} entity=profile identity={identity} "
        f"result={result} error_code={error_code} error_class={error_class} count=1"
    )


def _profile_batch_audit(
    action: str,
    *,
    result: str,
    error_code: str,
    succeeded_count: int,
    failed_count: int,
) -> str:
    return (
        f"[画像 AUDIT] action={action} entity=profile identity=batch "
        f"result={result} error_code={error_code} error_class=none "
        f"succeeded_count={succeeded_count} failed_count={failed_count}"
    )


class TestRevisionedProfileApi:
    @pytest.mark.asyncio
    async def test_create_profile_returns_complete_entity_and_revision(self) -> None:
        profile = _make_profile("u1", "Alice")
        mixin = _make_mixin(detail_profile=profile)
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=_complete_profile_payload())

        with (
            patch("core.api.profile_api.request", request_mock),
            patch("core.api.profile_api.logger.info") as audit,
        ):
            result = await mixin.create_profile()

        assert result == {
            "status": "ok",
            "data": {"entity": profile.to_dict.return_value, "revision": "rev-profile"},
        }
        mixin.profile_manager.create_profile_manual.assert_awaited_once_with(
            **_complete_profile_payload()
        )
        rendered_audit = repr(audit.call_args_list)
        assert (
            "action=%s entity=profile identity=%s result=%s error_code=%s"
            in rendered_audit
        )
        assert "'success', 'none'" in rendered_audit
        assert "formal" not in rendered_audit
        assert "graphs" not in rendered_audit

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failure", "code"),
        [
            (EntityAlreadyExistsError("duplicate"), "already_exists"),
            (EntityValidationError({"user_id": "不能为空"}), "validation_error"),
            (EntityNotFoundError("missing"), "not_found"),
        ],
    )
    async def test_create_maps_domain_errors(self, failure, code: str) -> None:
        mixin = _make_mixin(detail_profile=_make_profile())
        mixin.profile_manager.create_profile_manual.side_effect = failure
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=_complete_profile_payload())

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.create_profile()

        assert result["status"] == "error"
        assert result["code"] == code
        if code == "validation_error":
            assert result["field_errors"] == {"user_id": "不能为空"}

    @pytest.mark.asyncio
    async def test_create_missing_manager_is_stable_component_error(self) -> None:
        mixin = _make_mixin(profile_manager_available=False)
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=_complete_profile_payload())

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.create_profile()

        assert result["code"] == "component_unavailable"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {**_complete_profile_payload(), "message_count": 99},
            {
                **_complete_profile_payload(),
                "preferences": {"reply_style": "formal", "last_updated": 1},
            },
            {
                **_complete_profile_payload(),
                "tags": [
                    {
                        "category": "interest",
                        "value": "private-value",
                        "confidence": 0.9,
                        "source": "readonly-source",
                    }
                ],
            },
        ],
    )
    async def test_create_rejects_read_only_fields_at_every_level(
        self, payload: dict
    ) -> None:
        mixin = _make_mixin(detail_profile=_make_profile())
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.create_profile()

        assert result["code"] == "validation_error"
        mixin.profile_manager.create_profile_manual.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", [None, [], True, "profile"])
    async def test_create_requires_json_object(self, payload) -> None:
        mixin = _make_mixin(detail_profile=_make_profile())
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.create_profile()

        assert result["status"] == "error"
        mixin.profile_manager.create_profile_manual.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_list_and_detail_include_manager_revisions(self) -> None:
        profile = _make_profile("u1", "Alice")
        mixin = _make_mixin(
            profiles_list=[profile], profiles_total=1, detail_profile=profile
        )
        with patch("core.api.profile_api.request", _mock_request()):
            listed = await mixin.list_profiles()
        with patch("core.api.profile_api.request", _mock_request(user_id="u1")):
            detail = await mixin.get_profile_detail()

        assert listed["data"]["profiles"][0]["revision"] == "rev-profile"
        assert detail["data"]["revision"] == "rev-profile"
        assert mixin.profile_manager.revision_for.call_count == 2

    @pytest.mark.asyncio
    async def test_revisioned_update_uses_manual_replacement(self) -> None:
        profile = _make_profile("u1", "Alice")
        mixin = _make_mixin(detail_profile=profile)
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=_update_envelope())

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.update_profile()

        assert result["data"]["revision"] == "rev-profile"
        mixin.profile_manager.update_profile_manual.assert_awaited_once_with(
            user_id="u1",
            display_name="Alice",
            preferences=_complete_profile_payload()["preferences"],
            tags=_complete_profile_payload()["tags"],
            expected_revision="rev-old",
        )
        mixin.profile_manager.update_profile_fields.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_revisioned_update_returns_conflict_snapshot(self) -> None:
        current = _make_profile("u1", "Current").to_dict.return_value
        mixin = _make_mixin(detail_profile=_make_profile())
        mixin.profile_manager.update_profile_manual.side_effect = EditConflictError(
            current, "rev-current"
        )
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=_update_envelope())

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.update_profile()

        assert result["code"] == "edit_conflict"
        assert result["data"] == {
            "current_entity": current,
            "current_revision": "rev-current",
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {**_update_envelope(), "user_id": "legacy-fallback"},
            {**_update_envelope(), "unexpected": True},
            {**_update_envelope(), "identity": {"user_id": "u1", "name": "x"}},
            {**_update_envelope(), "identity": {"user_id": True}},
            {**_update_envelope(), "changes": {"message_count": 1}},
            {
                **_update_envelope(),
                "changes": {
                    **_update_envelope()["changes"],
                    "preferences": {"reply_style": "formal", "updated_at": 1},
                },
            },
            {
                **_update_envelope(),
                "changes": {
                    **_update_envelope()["changes"],
                    "tags": [
                        {
                            "category": "interest",
                            "value": "graphs",
                            "confidence": 0.9,
                            "created_at": 1,
                        }
                    ],
                },
            },
            {**_update_envelope(), "expected_revision": None},
        ],
    )
    async def test_revisioned_update_strictly_rejects_malformed_envelopes(
        self, payload: dict
    ) -> None:
        mixin = _make_mixin(detail_profile=_make_profile())
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.update_profile()

        assert result["code"] == "validation_error"
        mixin.profile_manager.update_profile_manual.assert_not_awaited()
        mixin.profile_manager.update_profile_fields.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_revisioned_delete_returns_identity_envelope(self) -> None:
        mixin = _make_mixin()
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(
            return_value={
                "identity": {"user_id": "u1"},
                "expected_revision": "rev-old",
            }
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.delete_profile()

        assert result == {
            "status": "ok",
            "data": {"deleted": True, "identity": {"user_id": "u1"}},
        }
        mixin.profile_manager.delete_profile_manual.assert_awaited_once_with(
            "u1", expected_revision="rev-old"
        )
        mixin.profile_manager.delete_profile.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failure", "code"),
        [
            (EntityNotFoundError("missing"), "not_found"),
            (
                EditConflictError(
                    _make_profile("u1", "Current").to_dict.return_value,
                    "rev-current",
                ),
                "edit_conflict",
            ),
        ],
    )
    async def test_revisioned_delete_maps_not_found_and_conflict(
        self, failure, code: str
    ) -> None:
        mixin = _make_mixin()
        mixin.profile_manager.delete_profile_manual.side_effect = failure
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(
            return_value={
                "identity": {"user_id": "u1"},
                "expected_revision": "rev-old",
            }
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.delete_profile()

        assert result["code"] == code
        if code == "edit_conflict":
            assert result["data"]["current_revision"] == "rev-current"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {"identity": {"user_id": "u1"}},
            {"identity": {"user_id": "u1", "extra": 1}, "expected_revision": "r"},
            {"identity": "u1", "expected_revision": "r"},
            {"identity": {"user_id": True}, "expected_revision": "r"},
            {
                "identity": {"user_id": "u1"},
                "expected_revision": "r",
                "user_id": "legacy-fallback",
            },
        ],
    )
    async def test_malformed_revisioned_delete_never_falls_back_to_legacy(
        self, payload: dict
    ) -> None:
        mixin = _make_mixin()
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.delete_profile()

        assert result["code"] == "validation_error"
        mixin.profile_manager.delete_profile_manual.assert_not_awaited()
        mixin.profile_manager.delete_profile.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method_name",
        ["create_profile", "update_profile", "delete_profile", "batch_delete_profiles"],
    )
    async def test_maintenance_guard_runs_before_json_and_engine_lookup(
        self, method_name: str
    ) -> None:
        blocked = {"status": "error", "code": "maintenance"}
        mixin = _make_mixin()
        mixin._maintenance_write_guard = MagicMock(return_value=blocked)
        mixin._ensure_plugin_ready = AsyncMock(
            side_effect=AssertionError("engine lookup must not run")
        )
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(
            side_effect=AssertionError("JSON parsing must not run")
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await getattr(mixin, method_name)()

        assert result is blocked
        mixin._maintenance_write_guard.assert_called_once_with()
        request_mock.get_json.assert_not_awaited()
        mixin._ensure_plugin_ready.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mutation_audit_covers_early_validation_without_payload_leak(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin(detail_profile=_make_profile())
        secret = "profile-payload-secret-e684"
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(
            return_value={**_complete_profile_payload(), "unknown": secret}
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.create_profile()

        audits = [
            record.getMessage()
            for record in caplog.records
            if "[画像 AUDIT]" in record.getMessage()
        ]
        assert result["code"] == "validation_error"
        assert len(audits) == 1
        assert "action=create" in audits[0]
        assert "entity=profile" in audits[0]
        assert "identity={'user_id': 'u1'}" in audits[0]
        assert "result=failure" in audits[0]
        assert "error_code=validation_error" in audits[0]
        assert secret not in caplog.text
        request_mock.get_json.assert_awaited_once_with(silent=True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("failure", "expected_code", "exception_secret"),
        [
            (
                EntityNotFoundError("profile-domain-secret-53ad"),
                "not_found",
                "profile-domain-secret-53ad",
            ),
            (
                RuntimeError("profile-generic-secret-a722"),
                "internal_error",
                "profile-generic-secret-a722",
            ),
        ],
    )
    async def test_mutation_audit_backend_failure_is_single_and_redacted(
        self,
        caplog: pytest.LogCaptureFixture,
        failure: Exception,
        expected_code: str,
        exception_secret: str,
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin(detail_profile=_make_profile())
        payload_secret = "profile-payload-secret-07bf"
        mixin.profile_manager.create_profile_manual.side_effect = failure
        payload = _complete_profile_payload()
        payload["display_name"] = payload_secret
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.create_profile()

        audits = [
            record.getMessage()
            for record in caplog.records
            if "[画像 AUDIT]" in record.getMessage()
        ]
        assert result["code"] == expected_code
        assert len(audits) == 1
        assert "action=create" in audits[0]
        assert "entity=profile" in audits[0]
        assert "identity={'user_id': 'u1'}" in audits[0]
        assert "result=failure" in audits[0]
        assert f"error_code={expected_code}" in audits[0]
        rendered = caplog.text + repr(result)
        assert payload_secret not in rendered
        assert exception_secret not in rendered

    @pytest.mark.asyncio
    async def test_mutation_audit_preserves_cancellation(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin(detail_profile=_make_profile())
        mixin.profile_manager.create_profile_manual.side_effect = (
            asyncio.CancelledError()
        )
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=_complete_profile_payload())

        with (
            patch("core.api.profile_api.request", request_mock),
            pytest.raises(asyncio.CancelledError),
        ):
            await mixin.create_profile()

        assert not [
            record for record in caplog.records if "[画像 AUDIT]" in record.getMessage()
        ]

    @pytest.mark.asyncio
    async def test_unexpected_failure_is_redacted_from_response_and_logs(self) -> None:
        mixin = _make_mixin(detail_profile=_make_profile())
        mixin.profile_manager.create_profile_manual.side_effect = RuntimeError(
            "exception-secret"
        )
        payload = _complete_profile_payload()
        payload["display_name"] = "free-text-secret"
        payload["preferences"]["reply_style"] = "preference-secret"
        payload["tags"][0]["value"] = "tag-secret"
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)

        with (
            patch("core.api.profile_api.request", request_mock),
            patch("core.api.profile_api.logger.error") as log_error,
        ):
            result = await mixin.create_profile()

        assert result["code"] == "internal_error"
        rendered = str(result) + str(log_error.call_args_list)
        for secret in (
            "exception-secret",
            "free-text-secret",
            "preference-secret",
            "tag-secret",
        ):
            assert secret not in rendered


class TestProfileMutationAuditContract:
    @pytest.mark.asyncio
    async def test_detail_revision_failure_has_no_audit_or_scope_leak(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        read_mixin = _make_mixin(detail_profile=_make_profile("read-user"))
        read_mixin.profile_manager.revision_for.side_effect = RuntimeError(
            "detail-revision-secret-85bc"
        )

        with patch("core.api.profile_api.request", _mock_request(user_id="read-user")):
            read_result = await read_mixin.get_profile_detail()

        assert read_result["status"] == "error"
        assert read_result["code"] == "internal_error"
        assert _profile_audit_messages(caplog) == []

        returned = _make_profile("write-user")
        write_mixin = _make_mixin(detail_profile=returned)
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(
            return_value=_complete_profile_payload("write-user")
        )
        with patch("core.api.profile_api.request", request_mock):
            write_result = await write_mixin.create_profile()

        assert write_result["status"] == "ok"
        assert _profile_audit_messages(caplog) == [
            _profile_audit("create", {"user_id": "write-user"}, result="success")
        ]
        assert "detail-revision-secret-85bc" not in caplog.text

    @pytest.mark.asyncio
    async def test_detail_cancellation_propagates_without_audit(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin(detail_profile=_make_profile())
        mixin.profile_manager.get_profile.side_effect = asyncio.CancelledError()

        with (
            patch("core.api.profile_api.request", _mock_request(user_id="u1")),
            pytest.raises(asyncio.CancelledError),
        ):
            await mixin.get_profile_detail()

        assert _profile_audit_messages(caplog) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "payload", "expected_audit"),
        [
            (
                "create_profile",
                _complete_profile_payload(),
                _profile_audit("create", {"user_id": "u1"}, result="success"),
            ),
            (
                "update_profile",
                _update_envelope(),
                _profile_audit("update", {"user_id": "u1"}, result="success"),
            ),
            (
                "delete_profile",
                {
                    "identity": {"user_id": "u1"},
                    "expected_revision": "rev-old",
                },
                _profile_audit("delete", {"user_id": "u1"}, result="success"),
            ),
            (
                "batch_delete_profiles",
                {
                    "action": "delete",
                    "items": [
                        {
                            "identity": {"user_id": "u1"},
                            "expected_revision": "rev-old",
                        }
                    ],
                },
                _profile_batch_audit(
                    "batch_delete",
                    result="success",
                    error_code="none",
                    succeeded_count=1,
                    failed_count=0,
                ),
            ),
            (
                "manage_profile_tags",
                {
                    "user_id": "u1",
                    "action": "add",
                    "tag": {
                        "category": "interest",
                        "value": "graphs",
                        "confidence": 0.9,
                    },
                },
                _profile_audit("tag_add", {"user_id": "u1"}, result="success"),
            ),
        ],
    )
    async def test_each_mutation_route_success_emits_exactly_once(
        self,
        caplog: pytest.LogCaptureFixture,
        method_name: str,
        payload: dict,
        expected_audit: str,
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin(detail_profile=_make_profile())
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)

        with patch("core.api.profile_api.request", request_mock):
            result = await getattr(mixin, method_name)()

        assert result["status"] == "ok"
        assert _profile_audit_messages(caplog) == [expected_audit]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "action", "response_code", "audit_code"),
        [
            ("create_profile", "create", "invalid_request", "invalid_request"),
            ("update_profile", "update", None, "request_error"),
            ("delete_profile", "delete", None, "request_error"),
            ("batch_delete_profiles", "batch", "invalid_request", "invalid_request"),
            ("manage_profile_tags", "manage_tags", None, "request_error"),
        ],
    )
    async def test_each_mutation_route_early_failure_emits_exactly_once(
        self,
        caplog: pytest.LogCaptureFixture,
        method_name: str,
        action: str,
        response_code: str | None,
        audit_code: str,
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin(detail_profile=_make_profile())
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=None)

        with patch("core.api.profile_api.request", request_mock):
            result = await getattr(mixin, method_name)()

        assert result["status"] == "error"
        assert result.get("code") == response_code
        assert _profile_audit_messages(caplog) == [
            _profile_audit(
                action,
                "unavailable",
                result="failure",
                error_code=audit_code,
            )
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "payload_fields"),
        [
            ("update_profile", {"display_name": "New"}),
            ("delete_profile", {}),
            (
                "manage_profile_tags",
                {
                    "action": "add",
                    "tag": {
                        "category": "interest",
                        "value": "graphs",
                        "confidence": 0.9,
                    },
                },
            ),
        ],
    )
    @pytest.mark.parametrize(
        "structured_user_id",
        [
            {"secret": "AUDIT_LEAK_MARKER"},
            ["AUDIT_LEAK_MARKER"],
            None,
        ],
    )
    async def test_legacy_mutations_reject_structured_user_id_without_audit_leak(
        self,
        caplog: pytest.LogCaptureFixture,
        method_name: str,
        payload_fields: dict,
        structured_user_id,
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin(detail_profile=_make_profile())
        mixin._ensure_plugin_ready = AsyncMock(
            side_effect=AssertionError("engine lookup must not run")
        )
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(
            return_value={"user_id": structured_user_id, **payload_fields}
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await getattr(mixin, method_name)()

        assert result["status"] == "error"
        mixin._ensure_plugin_ready.assert_not_awaited()
        audits = _profile_audit_messages(caplog)
        assert len(audits) == 1
        assert "identity=unavailable" in audits[0]
        rendered = caplog.text + repr(result)
        assert "AUDIT_LEAK_MARKER" not in rendered

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "payload", "manager_method"),
        [
            ("create_profile", _complete_profile_payload(), "create_profile_manual"),
            ("update_profile", _update_envelope(), "update_profile_manual"),
            (
                "delete_profile",
                {
                    "identity": {"user_id": "u1"},
                    "expected_revision": "rev-old",
                },
                "delete_profile_manual",
            ),
            (
                "batch_delete_profiles",
                {
                    "action": "delete",
                    "items": [
                        {
                            "identity": {"user_id": "u1"},
                            "expected_revision": "rev-old",
                        }
                    ],
                },
                "delete_profile_manual",
            ),
            (
                "manage_profile_tags",
                {
                    "user_id": "u1",
                    "action": "add",
                    "tag": {
                        "category": "interest",
                        "value": "graphs",
                        "confidence": 0.9,
                    },
                },
                "add_tag",
            ),
        ],
    )
    async def test_each_mutation_route_cancellation_has_no_completed_audit(
        self,
        caplog: pytest.LogCaptureFixture,
        method_name: str,
        payload: dict,
        manager_method: str,
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin(detail_profile=_make_profile())
        getattr(
            mixin.profile_manager, manager_method
        ).side_effect = asyncio.CancelledError()
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)

        with (
            patch("core.api.profile_api.request", request_mock),
            pytest.raises(asyncio.CancelledError),
        ):
            await getattr(mixin, method_name)()

        assert _profile_audit_messages(caplog) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "payload", "manager_method", "failure", "code"),
        [
            (
                "update_profile",
                _update_envelope(" audited-user "),
                "update_profile_manual",
                EditConflictError({"user_id": "audited-user"}, "rev-current"),
                "edit_conflict",
            ),
            (
                "delete_profile",
                {
                    "identity": {"user_id": " audited-user "},
                    "expected_revision": "rev-old",
                },
                "delete_profile_manual",
                EntityNotFoundError("revision-delete-secret-776a"),
                "not_found",
            ),
        ],
    )
    async def test_revisioned_failures_use_canonical_action_identity_and_class(
        self,
        caplog: pytest.LogCaptureFixture,
        method_name: str,
        payload: dict,
        manager_method: str,
        failure: Exception,
        code: str,
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin(detail_profile=_make_profile())
        getattr(mixin.profile_manager, manager_method).side_effect = failure
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)

        with patch("core.api.profile_api.request", request_mock):
            result = await getattr(mixin, method_name)()

        action = "update" if method_name == "update_profile" else "delete"
        assert result["code"] == code
        assert _profile_audit_messages(caplog) == [
            _profile_audit(
                action,
                {"user_id": "audited-user"},
                result="failure",
                error_code=code,
                error_class=type(failure).__name__,
            )
        ]
        assert "revision-delete-secret-776a" not in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "payload", "manager_method", "expected_action"),
        [
            (
                "update_profile",
                {"user_id": " legacy-user ", "display_name": "Renamed"},
                "update_profile_fields",
                "legacy_update",
            ),
            (
                "delete_profile",
                {"user_id": " legacy-user "},
                "delete_profile",
                "legacy_delete",
            ),
        ],
    )
    async def test_legacy_backend_failures_keep_branch_action_and_safe_identity(
        self,
        caplog: pytest.LogCaptureFixture,
        method_name: str,
        payload: dict,
        manager_method: str,
        expected_action: str,
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin(detail_profile=_make_profile("legacy-user"))
        getattr(mixin.profile_manager, manager_method).side_effect = RuntimeError(
            "legacy-backend-secret-d01e"
        )
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)

        with patch("core.api.profile_api.request", request_mock):
            result = await getattr(mixin, method_name)()

        assert result["code"] == "internal_error"
        assert _profile_audit_messages(caplog) == [
            _profile_audit(
                expected_action,
                {"user_id": "legacy-user"},
                result="failure",
                error_code="internal_error",
                error_class="RuntimeError",
            )
        ]
        assert "legacy-backend-secret-d01e" not in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "payload", "expected_action"),
        [
            (
                "update_profile",
                {"user_id": " legacy-user ", "display_name": "Renamed"},
                "legacy_update",
            ),
            (
                "delete_profile",
                {"user_id": " legacy-user "},
                "legacy_delete",
            ),
        ],
    )
    async def test_legacy_component_failure_uses_selected_branch_and_safe_identity(
        self,
        caplog: pytest.LogCaptureFixture,
        method_name: str,
        payload: dict,
        expected_action: str,
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin(profile_manager_available=False)
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)

        with patch("core.api.profile_api.request", request_mock):
            result = await getattr(mixin, method_name)()

        assert result["code"] == "component_unavailable"
        assert _profile_audit_messages(caplog) == [
            _profile_audit(
                expected_action,
                {"user_id": "legacy-user"},
                result="failure",
                error_code="component_unavailable",
            )
        ]

    @pytest.mark.asyncio
    async def test_legacy_delete_false_keeps_response_and_audits_not_deleted(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin()
        mixin.profile_manager.delete_profile.return_value = False
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value={"user_id": "u1"})

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.delete_profile()

        assert result == {
            "status": "ok",
            "data": {"deleted": False, "user_id": "u1"},
        }
        assert _profile_audit_messages(caplog) == [
            _profile_audit(
                "legacy_delete",
                {"user_id": "u1"},
                result="failure",
                error_code="not_deleted",
            )
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method_name", "payload", "manager_method", "expected_action"),
        [
            (
                "create_profile",
                {
                    **_complete_profile_payload(),
                    "preferences": {
                        **_complete_profile_payload()["preferences"],
                        "reply_style": None,
                    },
                },
                "create_profile_manual",
                "create",
            ),
            (
                "update_profile",
                {"user_id": "u1", "preferences": {"reply_style": None}},
                "update_profile_fields",
                "legacy_update",
            ),
            (
                "update_profile",
                {
                    **_update_envelope(),
                    "changes": {
                        **_update_envelope()["changes"],
                        "preferences": {"reply_style": None},
                    },
                },
                "update_profile_manual",
                "update",
            ),
        ],
    )
    async def test_reply_style_null_is_validation_error_without_mutation(
        self,
        caplog: pytest.LogCaptureFixture,
        method_name: str,
        payload: dict,
        manager_method: str,
        expected_action: str,
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin(detail_profile=_make_profile())
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)

        with patch("core.api.profile_api.request", request_mock):
            result = await getattr(mixin, method_name)()

        assert result["code"] == "validation_error"
        getattr(mixin.profile_manager, manager_method).assert_not_awaited()
        assert _profile_audit_messages(caplog) == [
            _profile_audit(
                expected_action,
                {"user_id": "u1"},
                result="failure",
                error_code="validation_error",
            )
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure_point", ["to_dict", "revision"])
    async def test_create_post_return_mapper_failure_uses_returned_identity(
        self,
        caplog: pytest.LogCaptureFixture,
        failure_point: str,
    ) -> None:
        caplog.set_level(logging.INFO)
        failure_secret = f"mapper-{failure_point}-secret-a91f"
        returned = _make_profile("returned-user")
        mixin = _make_mixin(detail_profile=returned)
        if failure_point == "to_dict":
            returned.to_dict.side_effect = RuntimeError(failure_secret)
        else:
            mixin.profile_manager.revision_for.side_effect = RuntimeError(
                failure_secret
            )
        payload = _complete_profile_payload("requested-user")
        payload["display_name"] = "mapper-payload-secret-5d4c"
        payload["preferences"]["reply_style"] = "mapper-preference-secret-c843"
        payload["tags"][0]["value"] = "mapper-tag-secret-088b"
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.create_profile()

        assert result["code"] == "internal_error"
        assert _profile_audit_messages(caplog) == [
            _profile_audit(
                "create",
                {"user_id": "returned-user"},
                result="failure",
                error_code="internal_error",
                error_class="RuntimeError",
            )
        ]
        rendered = caplog.text + repr(result)
        for secret in (
            failure_secret,
            "mapper-payload-secret-5d4c",
            "mapper-preference-secret-c843",
            "mapper-tag-secret-088b",
        ):
            assert secret not in rendered

    @pytest.mark.asyncio
    async def test_manage_tags_backend_failure_uses_selected_action_and_identity(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin(detail_profile=_make_profile("u1"))
        mixin.profile_manager.add_tag.side_effect = RuntimeError(
            "manage-tag-secret-23ae"
        )
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(
            return_value={
                "user_id": " u1 ",
                "action": "add",
                "tag": {
                    "category": "interest",
                    "value": "graphs",
                    "confidence": 0.9,
                },
            }
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.manage_profile_tags()

        assert result["code"] == "internal_error"
        assert _profile_audit_messages(caplog) == [
            _profile_audit(
                "tag_add",
                {"user_id": "u1"},
                result="failure",
                error_code="internal_error",
                error_class="RuntimeError",
            )
        ]
        assert "manage-tag-secret-23ae" not in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("side_effect", "expected_result", "succeeded_count", "failed_count"),
        [
            (
                [EntityNotFoundError("batch-item-secret-9d2f"), True],
                "partial",
                1,
                1,
            ),
            (
                [
                    EntityNotFoundError("batch-item-secret-9d2f"),
                    RuntimeError("batch-runtime-secret-d42a"),
                ],
                "failure",
                0,
                2,
            ),
        ],
    )
    async def test_batch_failure_emits_one_aggregate_without_item_audits(
        self,
        caplog: pytest.LogCaptureFixture,
        side_effect,
        expected_result: str,
        succeeded_count: int,
        failed_count: int,
    ) -> None:
        caplog.set_level(logging.INFO)
        mixin = _make_mixin()
        mixin.profile_manager.delete_profile_manual.side_effect = side_effect
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(
            return_value={
                "action": "delete",
                "items": [
                    {
                        "identity": {"user_id": "u1"},
                        "expected_revision": "batch-revision-secret-351f",
                    },
                    {
                        "identity": {"user_id": "u2"},
                        "expected_revision": "rev-2",
                    },
                ],
            }
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.batch_delete_profiles()

        assert result["status"] == "ok"
        assert result["data"]["succeeded_count"] == succeeded_count
        assert result["data"]["failed_count"] == failed_count
        assert _profile_audit_messages(caplog) == [
            _profile_batch_audit(
                "batch_delete",
                result=expected_result,
                error_code="item_failure",
                succeeded_count=succeeded_count,
                failed_count=failed_count,
            )
        ]
        rendered = caplog.text + repr(result)
        for secret in (
            "batch-item-secret-9d2f",
            "batch-runtime-secret-d42a",
            "batch-revision-secret-351f",
        ):
            assert secret not in rendered


class TestRevisionedProfileBatch:
    def _request(self, action: str, *, items=None, params=None):
        request_mock = _mock_request()
        payload = {
            "action": action,
            "items": items
            or [{"identity": {"user_id": "u1"}, "expected_revision": "rev-old"}],
        }
        if params is not None:
            payload["params"] = params
        request_mock.get_json = AsyncMock(return_value=payload)
        return request_mock

    @pytest.mark.asyncio
    async def test_batch_delete_uses_each_items_revision(self) -> None:
        mixin = _make_mixin()
        request_mock = self._request(
            "delete",
            items=[
                {"identity": {"user_id": "u1"}, "expected_revision": "r1"},
                {"identity": {"user_id": "u2"}, "expected_revision": "r2"},
            ],
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.batch_delete_profiles()

        assert result["data"] == {
            "total": 2,
            "succeeded_count": 2,
            "failed_count": 0,
            "succeeded_ids": [{"user_id": "u1"}, {"user_id": "u2"}],
            "failures": [],
        }
        assert mixin.profile_manager.delete_profile_manual.await_args_list[0].args == (
            "u1",
        )
        assert mixin.profile_manager.delete_profile_manual.await_args_list[
            1
        ].kwargs == {"expected_revision": "r2"}
        mixin.profile_manager.delete_profile.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("action", "current_tags", "expected_tags"),
        [
            (
                "tags_add",
                [],
                [{"category": "interest", "value": "graphs", "confidence": 0.9}],
            ),
            (
                "tags_add",
                [{"category": "interest", "value": "graphs", "confidence": 0.9}],
                [{"category": "interest", "value": "graphs", "confidence": 0.9}],
            ),
            (
                "tags_remove",
                [
                    {"category": "interest", "value": "graphs", "confidence": 0.9},
                    {"category": "custom", "value": "keep", "confidence": 0.5},
                ],
                [{"category": "custom", "value": "keep", "confidence": 0.5}],
            ),
            (
                "tags_remove",
                [{"category": "custom", "value": "keep", "confidence": 0.5}],
                [{"category": "custom", "value": "keep", "confidence": 0.5}],
            ),
        ],
    )
    async def test_batch_tag_actions_are_idempotent_revisioned_replacements(
        self, action: str, current_tags: list[dict], expected_tags: list[dict]
    ) -> None:
        preferences = _complete_profile_payload()["preferences"]
        current = _make_profile(
            "u1", "Alice", preferences=preferences, tags=current_tags
        )
        mixin = _make_mixin(detail_profile=current)
        request_mock = self._request(
            action,
            params={
                "tag": {
                    "category": "interest",
                    "value": "graphs",
                    "confidence": 0.9,
                }
            },
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.batch_delete_profiles()

        assert result["data"]["succeeded_count"] == 1
        mixin.profile_manager.update_profile_manual.assert_awaited_once_with(
            user_id="u1",
            display_name="Alice",
            preferences=preferences,
            tags=expected_tags,
            expected_revision="rev-old",
        )
        mixin.profile_manager.add_tag.assert_not_awaited()
        mixin.profile_manager.remove_tag.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_preserves_partial_failure_and_input_order(self) -> None:
        mixin = _make_mixin()
        mixin.profile_manager.delete_profile_manual.side_effect = [
            EntityNotFoundError("missing-secret"),
            True,
            EditConflictError(_make_profile("u3").to_dict.return_value, "r-current"),
        ]
        request_mock = self._request(
            "delete",
            items=[
                {"identity": {"user_id": "u1"}, "expected_revision": "r1"},
                {"identity": {"user_id": "u2"}, "expected_revision": "r2"},
                {"identity": {"user_id": "u3"}, "expected_revision": "r3"},
            ],
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.batch_delete_profiles()

        assert result["data"]["succeeded_ids"] == [{"user_id": "u2"}]
        assert [failure["identity"] for failure in result["data"]["failures"]] == [
            {"user_id": "u1"},
            {"user_id": "u3"},
        ]
        assert [failure["code"] for failure in result["data"]["failures"]] == [
            "not_found",
            "edit_conflict",
        ]
        assert result["data"]["failures"][1]["current_revision"] == "r-current"

    @pytest.mark.asyncio
    async def test_batch_invalid_item_does_not_stop_valid_item(self) -> None:
        mixin = _make_mixin()
        request_mock = self._request(
            "delete",
            items=[
                {"identity": {"user_id": True}, "expected_revision": "r1"},
                {"identity": {"user_id": "u2"}, "expected_revision": "r2"},
            ],
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.batch_delete_profiles()

        assert result["data"]["succeeded_ids"] == [{"user_id": "u2"}]
        assert result["data"]["failures"][0]["identity"] == {"item_index": 0}
        assert result["data"]["failures"][0]["code"] == "validation_error"

    @pytest.mark.asyncio
    async def test_batch_cap_is_checked_before_manager_lookup_or_mutation(self) -> None:
        mixin = _make_mixin()
        mixin._ensure_plugin_ready = AsyncMock(
            side_effect=AssertionError("manager lookup must not run")
        )
        request_mock = self._request(
            "delete",
            items=[
                {"identity": {"user_id": f"u{i}"}, "expected_revision": "r"}
                for i in range(101)
            ],
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.batch_delete_profiles()

        assert result["code"] == "validation_error"
        mixin._ensure_plugin_ready.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("payload", "item_failure"),
        [
            ({"action": "unknown", "items": []}, False),
            ({"action": "delete", "items": "bad"}, False),
            ({"action": "delete", "items": [], "params": {"tag": {}}}, False),
            (
                {
                    "action": "tags_add",
                    "items": [
                        {"identity": {"user_id": "u1"}, "expected_revision": "r"}
                    ],
                    "params": {"tags": []},
                },
                False,
            ),
            (
                {
                    "action": "tags_add",
                    "items": [
                        {
                            "identity": {"user_id": "u1", "extra": 1},
                            "expected_revision": "r",
                        }
                    ],
                    "params": {
                        "tag": {
                            "category": "interest",
                            "value": "graphs",
                            "confidence": 0.9,
                        }
                    },
                },
                True,
            ),
            (
                {
                    "action": "delete",
                    "items": [
                        {
                            "identity": {"user_id": "u1"},
                            "expected_revision": "r",
                            "extra": 1,
                        }
                    ],
                },
                True,
            ),
            ({"action": "delete", "items": [], "extra": 1}, False),
        ],
    )
    async def test_batch_strictly_validates_action_items_and_params(
        self, payload: dict, item_failure: bool
    ) -> None:
        mixin = _make_mixin()
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(return_value=payload)

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.batch_delete_profiles()

        if item_failure:
            assert result["data"]["failed_count"] == 1
        else:
            assert result["status"] == "error"
        mixin.profile_manager.delete_profile_manual.assert_not_awaited()
        mixin.profile_manager.update_profile_manual.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_manager_unavailable_is_stable_component_error(self) -> None:
        mixin = _make_mixin(profile_manager_available=False)
        request_mock = self._request("delete")

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.batch_delete_profiles()

        assert result["code"] == "component_unavailable"

    @pytest.mark.asyncio
    async def test_legacy_action_delete_batch_response_remains_compatible(self) -> None:
        mixin = _make_mixin()
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(
            return_value={"action": "delete", "user_ids": ["u1", "u2"]}
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await mixin.batch_delete_profiles()

        assert result["data"] == {
            "deleted_count": 2,
            "failed_count": 0,
            "total": 2,
            "failed_ids": [],
        }
        assert mixin.profile_manager.delete_profile.await_count == 2


class TestRevisionedProfileBatchIntegration:
    @staticmethod
    def _mixin(manager: ProfileManager):
        engine = SimpleNamespace(profile_manager=manager)

        class Stub:
            batch_delete_profiles = ProfileApiMixin.batch_delete_profiles
            _batch_profile_actions = ProfileApiMixin._batch_profile_actions

            @staticmethod
            def _maintenance_write_guard():
                return None

            async def _ensure_plugin_ready(self):
                return {"memory_engine": engine}, None

        return Stub()

    @staticmethod
    def _batch_payload(action: str, revision: str) -> dict:
        return {
            "action": action,
            "items": [
                {
                    "identity": {"user_id": "real-user"},
                    "expected_revision": revision,
                }
            ],
            "params": {
                "tag": {
                    "category": "interest",
                    "value": "graphs",
                    "confidence": 0.9,
                }
            },
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("action", "initial_tags", "expected_values"),
        [
            ("tags_add", [], ["graphs"]),
            (
                "tags_remove",
                [
                    {
                        "category": "interest",
                        "value": "graphs",
                        "confidence": 0.9,
                    },
                    {
                        "category": "custom",
                        "value": "keep",
                        "confidence": 0.5,
                    },
                ],
                ["keep"],
            ),
        ],
    )
    async def test_real_manager_batch_tag_action_persists(
        self,
        tmp_db_path: str,
        action: str,
        initial_tags: list[dict],
        expected_values: list[str],
    ) -> None:
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        manager = ProfileManager(store)
        current = await manager.create_profile_manual(
            "real-user",
            display_name="Alice",
            preferences={
                "reply_style": "formal",
                "preferred_topics": ["graphs"],
                "avoided_topics": ["spoilers"],
                "active_hours": [9, 10],
            },
            tags=initial_tags,
        )
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(
            return_value=self._batch_payload(action, manager.revision_for(current))
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await self._mixin(manager).batch_delete_profiles()

        assert result["data"]["failures"] == [], result
        assert result["data"]["succeeded_count"] == 1
        persisted = await manager.get_profile("real-user")
        assert persisted is not None
        assert [tag.value for tag in persisted.tags] == expected_values
        assert persisted.preferences.reply_style == "formal"
        assert persisted.preferences.preferred_topics == ["graphs"]
        assert persisted.preferences.avoided_topics == ["spoilers"]
        assert persisted.preferences.active_hours == [9, 10]

    @pytest.mark.asyncio
    async def test_real_manager_batch_tag_action_preserves_revision_conflict(
        self, tmp_db_path: str
    ) -> None:
        store = ProfileStore(tmp_db_path)
        await store.init_table()
        manager = ProfileManager(store)
        original = await manager.create_profile_manual(
            "real-user",
            display_name="Before",
            preferences={
                "reply_style": "casual",
                "preferred_topics": [],
                "avoided_topics": [],
                "active_hours": [],
            },
            tags=[],
        )
        stale_revision = manager.revision_for(original)
        concurrent = await manager.update_profile_manual(
            "real-user",
            display_name="Concurrent",
            preferences={
                "reply_style": "formal",
                "preferred_topics": ["current"],
                "avoided_topics": [],
                "active_hours": [12],
            },
            tags=[],
            expected_revision=stale_revision,
        )
        request_mock = _mock_request()
        request_mock.get_json = AsyncMock(
            return_value=self._batch_payload("tags_add", stale_revision)
        )

        with patch("core.api.profile_api.request", request_mock):
            result = await self._mixin(manager).batch_delete_profiles()

        failure = result["data"]["failures"][0]
        assert failure["code"] == "edit_conflict"
        assert failure["current_revision"] == manager.revision_for(concurrent)
        persisted = await manager.get_profile("real-user")
        assert persisted is not None
        assert persisted.display_name == "Concurrent"
        assert persisted.preferences.preferred_topics == ["current"]
        assert persisted.tags == []


@pytest.mark.asyncio
@pytest.mark.parametrize("display_name", [True, {}, [], 123, "x" * 129])
async def test_legacy_update_rejects_invalid_display_name_without_mutation(
    display_name,
) -> None:
    mixin = _make_mixin(detail_profile=_make_profile())
    request_mock = _mock_request()
    request_mock.get_json = AsyncMock(
        return_value={"user_id": "u1", "display_name": display_name}
    )
    with patch("core.api.profile_api.request", request_mock):
        result = await mixin.update_profile()
    assert result["code"] == "validation_error"
    mixin.profile_manager.update_profile_fields.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "preferences",
    [
        {"unknown": "x"},
        {"reply_style": True},
        {"preferred_topics": "graphs"},
        {"preferred_topics": ["ok", True]},
        {"active_hours": [9, True]},
        {"active_hours": [24]},
    ],
)
async def test_legacy_update_rejects_invalid_nested_preferences_without_mutation(
    preferences,
) -> None:
    mixin = _make_mixin(detail_profile=_make_profile())
    request_mock = _mock_request()
    request_mock.get_json = AsyncMock(
        return_value={"user_id": "u1", "preferences": preferences}
    )
    with patch("core.api.profile_api.request", request_mock):
        result = await mixin.update_profile()
    assert result["code"] == "validation_error"
    mixin.profile_manager.update_profile_fields.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tag",
    [
        {"category": True, "value": "x", "confidence": 0.5},
        {"category": "bogus", "value": "x", "confidence": 0.5},
        {"category": "interest", "value": True, "confidence": 0.5},
        {"category": " interest ", "value": "   ", "confidence": 0.5},
    ],
)
async def test_legacy_tags_reject_invalid_strings_without_mutation(tag) -> None:
    mixin = _make_mixin(detail_profile=_make_profile())
    request_mock = _mock_request()
    request_mock.get_json = AsyncMock(
        return_value={"user_id": "u1", "action": "add", "tag": tag}
    )
    with patch("core.api.profile_api.request", request_mock):
        result = await mixin.manage_profile_tags()
    assert result["status"] == "error"
    mixin.profile_manager.add_tag.assert_not_awaited()
