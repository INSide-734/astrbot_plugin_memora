"""控制台 API：用户画像的列表、详情、更新、删除与标签管理。"""

from __future__ import annotations

from typing import Any

from quart import request

from astrbot.api import logger

from ..models.user_profile import TagCategory, UserTag
from .response_utils import error_response, ok_response


def _coerce_user_id(raw_user_id: Any) -> str:
    """将外部传入的用户 ID 转换为字符串，同时拒绝 JSON 布尔值。"""
    if isinstance(raw_user_id, bool):
        return ""
    return str(raw_user_id).strip()


def _coerce_confidence(raw_confidence: Any) -> float:
    """将外部传入的置信度值转换为浮点数，同时拒绝 JSON 布尔值。"""
    if isinstance(raw_confidence, bool):
        raise TypeError("boolean values are not valid confidence scores")
    return float(raw_confidence)


def _safe_profile_to_dict(profile: Any) -> dict[str, Any] | None:
    try:
        return profile.to_dict()
    except Exception:
        return None


def _safe_profile_list(profiles: Any) -> list[Any]:
    try:
        return list(profiles or [])
    except Exception:
        return []


def _safe_total(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _profile_response_or_error(profile: Any):
    payload = _safe_profile_to_dict(profile)
    if payload is None:
        return error_response("profile serialization failed: 画像序列化失败")
    return ok_response(payload)


def _json_object_payload_or_error(payload: Any):
    if isinstance(payload, dict):
        return payload, None
    return None, error_response("请求体必须为 JSON 对象")


class ProfileApiMixin:
    async def list_profiles(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        if not getattr(engine, "profile_manager", None):
            return ok_response({"profiles": [], "total": 0})
        args = request.args
        try:
            limit = int(args.get("limit", 50))
            offset = int(args.get("offset", 0))
        except (TypeError, ValueError):
            return error_response("limit 和 offset 必须为整数")
        profiles, total = await engine.profile_manager.list_profiles(
            limit=limit, offset=offset
        )
        profiles = _safe_profile_list(profiles)
        total = _safe_total(total, 0)
        serialized_profiles = [
            item
            for item in (_safe_profile_to_dict(p) for p in profiles)
            if item is not None
        ]
        return ok_response(
            {
                "profiles": serialized_profiles,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    async def get_profile_detail(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        if not getattr(engine, "profile_manager", None):
            return error_response("用户画像功能未启用")
        args = request.args
        user_id = _coerce_user_id(args.get("user_id", ""))
        if not user_id:
            return error_response("user_id required: 缺少必填参数 user_id")
        profile = await engine.profile_manager.get_profile(user_id)
        if profile is None:
            return error_response("画像不存在")
        return _profile_response_or_error(profile)

    async def update_profile(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        if not getattr(engine, "profile_manager", None):
            return error_response("用户画像功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        user_id = _coerce_user_id(payload.get("user_id", ""))
        if not user_id:
            return error_response("user_id required: 缺少必填参数 user_id")
        profile = await engine.profile_manager.get_profile(user_id)
        if profile is None:
            return error_response("画像不存在")
        preferences = None
        if "preferences" in payload:
            if not isinstance(payload["preferences"], dict):
                return error_response("preferences 必须为对象")
            preferences = dict(payload["preferences"] or {})
        profile = await engine.profile_manager.update_profile_fields(
            user_id,
            display_name=(
                str(payload["display_name"]) if "display_name" in payload else None
            ),
            preferences=preferences,
        )
        if profile is None:
            return error_response("画像不存在")
        return _profile_response_or_error(profile)

    async def delete_profile(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        if not getattr(engine, "profile_manager", None):
            return error_response("用户画像功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        user_id = _coerce_user_id(payload.get("user_id", ""))
        if not user_id:
            return error_response("user_id required: 缺少必填参数 user_id")
        deleted = await engine.profile_manager.delete_profile(user_id)
        return ok_response({"deleted": deleted, "user_id": user_id})

    async def batch_delete_profiles(self):
        """POST /profiles/batch {user_ids, action: "delete"} — 批量删除用户画像"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        if not getattr(engine, "profile_manager", None):
            return error_response("用户画像功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        user_ids = payload.get("user_ids", [])
        if not isinstance(user_ids, list) or not user_ids:
            return error_response("需要提供用户 ID 列表")
        deleted_count = 0
        failed_ids: list[Any] = []
        for raw_id in user_ids:
            try:
                uid = _coerce_user_id(raw_id)
                if not uid:
                    failed_ids.append(raw_id)
                    continue
                if await engine.profile_manager.delete_profile(uid):
                    deleted_count += 1
                else:
                    failed_ids.append(raw_id)
            except Exception as e:
                logger.debug(f"批量删除画像失败 (raw_id={raw_id}): {e}")
                failed_ids.append(raw_id)
        return ok_response(
            {
                "deleted_count": deleted_count,
                "failed_count": len(failed_ids),
                "total": len(user_ids),
                "failed_ids": failed_ids,
            }
        )

    async def manage_profile_tags(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        if not getattr(engine, "profile_manager", None):
            return error_response("用户画像功能未启用")
        payload = await request.get_json(silent=True) or {}
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        user_id = _coerce_user_id(payload.get("user_id", ""))
        action = str(payload.get("action", ""))
        if not user_id:
            return error_response("user_id required: 缺少必填参数 user_id")
        if action not in ("add", "remove"):
            return error_response("action 必须为 'add' 或 'remove'")
        profile = await engine.profile_manager.get_profile(user_id)
        if profile is None:
            return error_response("画像不存在")
        tag = payload.get("tag", {})
        if not isinstance(tag, dict):
            return error_response("tag 必须为对象 {category, value, confidence}")
        category = str(tag.get("category", ""))
        value = str(tag.get("value", ""))
        if not category or not value:
            return error_response("tag 的 category 和 value 为必填项")
        category_value = (
            category if category in {item.value for item in TagCategory} else "custom"
        )
        if action == "add":
            try:
                confidence = _coerce_confidence(tag.get("confidence", 0.5))
            except (TypeError, ValueError):
                return error_response("confidence 必须为数字")
            profile = await engine.profile_manager.add_tag(
                user_id,
                UserTag.from_dict(
                    {
                        "category": category_value,
                        "value": value,
                        "confidence": confidence,
                        "source": str(tag.get("source", "manual")),
                    }
                )
            )
        else:
            profile = await engine.profile_manager.remove_tag(
                user_id, category_value, value
            )
        if profile is None:
            return error_response("画像不存在")
        return _profile_response_or_error(profile)


__all__ = ["ProfileApiMixin"]
