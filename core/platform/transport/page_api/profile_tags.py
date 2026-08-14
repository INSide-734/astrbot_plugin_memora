"""画像标签管理 Page API 端点。"""

from __future__ import annotations

from typing import Any

from ....features.profiles.domain.models import TagCategory, UserTag
from ....shared.entity_editing import EntityValidationError
from .editing_utils import finite_float, reject_unknown_fields
from .response_utils import error_response

_TAG_FIELDS = frozenset({"category", "value", "confidence"})


class ProfileTagsApiMixin:
    """承载画像标签增删，复用原模块审计和响应 helper。"""

    _ensure_plugin_ready: Any

    async def _manage_profile_tags(self):
        """增加或移除一个人工画像标签。"""
        api = self._profile_api_module()
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        payload = await api.request.get_json(silent=True)
        payload, error = api._json_object_payload_or_error(payload)
        if error:
            return error
        assert payload is not None
        user_id = api._coerce_user_id(payload.get("user_id", ""))
        action = str(payload.get("action", ""))
        if not user_id:
            return error_response("user_id required: 缺少必填参数 user_id")
        if action not in ("add", "remove"):
            return error_response("action 必须为 'add' 或 'remove'")
        api._select_audit("tag_" + action, {"user_id": user_id})
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        manager = getattr(engines["memory_engine"], "profile_manager", None)
        if manager is None:
            return api._component_unavailable()
        profile = await manager.get_profile(user_id)
        if profile is None:
            return error_response("画像不存在")
        tag = payload.get("tag", {})
        if not isinstance(tag, dict):
            return error_response("tag 必须为对象 {category, value, confidence}")
        unknown = reject_unknown_fields(tag, _TAG_FIELDS)
        if unknown:
            return unknown
        raw_category = tag.get("category")
        raw_value = tag.get("value")
        if not isinstance(raw_category, str) or not isinstance(raw_value, str):
            return api._field_error("tag", "category 和 value 必须为字符串")
        category_value = raw_category.strip()
        value = raw_value.strip()
        if not category_value or not value or len(value) > 128:
            return api._field_error("tag", "category 和 value 必须为非空有界字符串")
        if category_value not in {item.value for item in TagCategory}:
            return api._field_error("tag.category", "未知标签分类")
        if action == "add":
            try:
                confidence = finite_float(
                    tag.get("confidence", 0.5), field="tag.confidence"
                )
            except EntityValidationError:
                return error_response("confidence 必须为数字", code="validation_error")
            if not 0.0 <= confidence <= 1.0:
                return api._field_error("tag.confidence", "必须在 0.0 到 1.0 之间")
            profile = await manager.add_tag(
                user_id,
                UserTag.from_dict(
                    {
                        "category": category_value,
                        "value": value,
                        "confidence": confidence,
                        "source": "manual",
                    }
                ),
            )
        else:
            profile = await manager.remove_tag(user_id, category_value, value)
        if profile is None:
            return error_response("画像不存在")
        response = api._profile_response_or_error(profile)
        api._audit_event("tag_" + action, {"user_id": user_id}, result="success")
        return response

    @staticmethod
    def _profile_api_module():
        """动态返回原 Profile API 模块，保留测试替换点。"""
        from . import profile_api

        return profile_api
