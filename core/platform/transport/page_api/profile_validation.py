"""画像 Page API 的字段、身份和批量动作校验。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ....features.profiles.domain.models import TagCategory
from ....shared.entity_editing import (
    EntityNotFoundError,
    EntityValidationError,
)
from .editing_utils import (
    finite_float,
    reject_unknown_fields,
    require_object,
    required_text,
)

_PREFERENCE_FIELDS = frozenset(
    {"reply_style", "preferred_topics", "avoided_topics", "active_hours"}
)
_TAG_FIELDS = frozenset({"category", "value", "confidence"})
_IDENTITY_FIELDS = frozenset({"user_id"})
_BATCH_ITEM_FIELDS = frozenset({"identity", "expected_revision"})
_MAX_TAGS = 100


def _profile_api_module():
    """返回原 API 模块以复用稳定响应和序列化辅助。"""
    from . import profile_api

    return profile_api


def _field_error(field: str, message: str) -> dict[str, Any]:
    """调用原 API 模块的稳定字段错误响应。"""
    return _profile_api_module()._field_error(field, message)


def _validation_error(exc: EntityValidationError) -> dict[str, Any]:
    """调用原 API 模块的稳定校验错误响应。"""
    return _profile_api_module()._validation_error(exc)


def _safe_profile_to_dict(profile: Any) -> dict[str, Any] | None:
    """调用原 API 模块的安全画像序列化。"""
    return _profile_api_module()._safe_profile_to_dict(profile)


def _validate_preferences(value: Any, *, field: str) -> dict | None:
    if not isinstance(value, Mapping):
        return _field_error(field, "必须为对象")
    unknown = sorted(set(value) - _PREFERENCE_FIELDS)
    if unknown:
        return _field_error(field + "." + unknown[0], "字段不可写")
    if "reply_style" in value:
        reply_style = value["reply_style"]
        if (
            not isinstance(reply_style, str)
            or not reply_style.strip()
            or len(reply_style.strip()) > 128
        ):
            return _field_error(
                field + ".reply_style",
                "必须为非空字符串且不超过 128 字符",
            )
    for name in ("preferred_topics", "avoided_topics"):
        if name not in value:
            continue
        items = value[name]
        if not isinstance(items, list) or len(items) > 100:
            return _field_error(field + "." + name, "必须为最多 100 项的数组")
        normalized: list[str] = []
        for item in items:
            if not isinstance(item, str) or not item.strip() or len(item.strip()) > 128:
                return _field_error(
                    field + "." + name, "每项必须为非空字符串且不超过 128 字符"
                )
            if item.strip() in normalized:
                return _field_error(field + "." + name, "项目不得重复")
            normalized.append(item.strip())
    if "active_hours" in value:
        hours = value["active_hours"]
        if not isinstance(hours, list) or len(hours) > 24:
            return _field_error(field + ".active_hours", "必须为最多 24 项的数组")
        if any(
            isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23
            for hour in hours
        ):
            return _field_error(field + ".active_hours", "每项必须为 0 到 23 的整数")
        if len(set(hours)) != len(hours):
            return _field_error(field + ".active_hours", "项目不得重复")
    return None


def _validate_tags(value: Any, *, field: str) -> dict | None:
    if not isinstance(value, list):
        return _field_error(field, "必须为数组")
    if len(value) > _MAX_TAGS:
        return _field_error(field, "项目过多")
    for index, item in enumerate(value):
        item_field = field + "." + str(index)
        if not isinstance(item, Mapping):
            return _field_error(item_field, "必须为对象")
        unknown = reject_unknown_fields(item, _TAG_FIELDS)
        if unknown:
            unknown["field_errors"] = {
                item_field + "." + name: message
                for name, message in unknown.get("field_errors", {}).items()
            }
            return unknown
    return None


def _validate_editable_payload(
    payload: Mapping[str, Any], *, prefix: str = ""
) -> dict | None:
    if "preferences" in payload:
        error = _validate_preferences(
            payload["preferences"], field=prefix + "preferences"
        )
        if error:
            return error
    if "tags" in payload:
        return _validate_tags(payload["tags"], field=prefix + "tags")
    return None


def _parse_identity(
    value: Any,
) -> tuple[dict[str, str] | None, str | None, dict | None]:
    identity, error = require_object(value)
    if error:
        return None, None, _field_error("identity", "必须为对象")
    assert identity is not None
    unknown = reject_unknown_fields(identity, _IDENTITY_FIELDS)
    if unknown:
        return None, None, unknown
    try:
        user_id = required_text(identity.get("user_id"), field="identity.user_id")
    except EntityValidationError as exc:
        return None, None, _validation_error(exc)
    normalized = {"user_id": user_id}
    return normalized, user_id, None


def _parse_revision(value: Any) -> tuple[str | None, dict | None]:
    try:
        return (
            required_text(value, field="expected_revision", maximum=256),
            None,
        )
    except EntityValidationError as exc:
        return None, _validation_error(exc)


def _parse_batch_item(
    value: Any,
) -> tuple[dict[str, str] | None, str | None, str | None, dict | None]:
    item, error = require_object(value)
    if error:
        return None, None, None, _field_error("item", "必须为对象")
    assert item is not None
    unknown = reject_unknown_fields(item, _BATCH_ITEM_FIELDS)
    if unknown:
        return None, None, None, unknown
    identity, user_id, identity_error = _parse_identity(item.get("identity"))
    if identity_error:
        return None, None, None, identity_error
    revision, revision_error = _parse_revision(item.get("expected_revision"))
    if revision_error:
        return identity, user_id, None, revision_error
    return identity, user_id, revision, None


def _batch_failure(identity: Mapping[str, Any], error: Mapping[str, Any]) -> dict:
    failure: dict[str, Any] = {
        "identity": dict(identity),
        "code": error.get("code", "internal_error"),
        "message": error.get("message", "用户画像操作失败"),
    }
    if error.get("field_errors"):
        failure["field_errors"] = dict(error["field_errors"])
    data = error.get("data")
    if isinstance(data, Mapping):
        if isinstance(data.get("current_entity"), Mapping):
            failure["current_entity"] = dict(data["current_entity"])
        if data.get("current_revision") is not None:
            failure["current_revision"] = data["current_revision"]
    return failure


def _normalize_batch_tag(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EntityValidationError({"params.tag": "必须为对象"})
    unknown = sorted(set(value) - _TAG_FIELDS)
    if unknown:
        raise EntityValidationError(
            {"params.tag." + name: "字段不可写" for name in unknown}
        )
    category = required_text(
        value.get("category"), field="params.tag.category", maximum=64
    )
    if category not in {item.value for item in TagCategory}:
        raise EntityValidationError({"params.tag.category": "不支持的标签分类"})
    tag_value = required_text(value.get("value"), field="params.tag.value")
    confidence = finite_float(
        value.get("confidence", 0.5), field="params.tag.confidence"
    )
    if not 0.0 <= confidence <= 1.0:
        raise EntityValidationError({"params.tag.confidence": "必须在 0.0 到 1.0 之间"})
    return {"category": category, "value": tag_value, "confidence": confidence}


async def _apply_revisioned_tag_action(
    manager: Any,
    profile_ref: tuple,
    action: str,
    tag: Mapping[str, Any],
) -> None:
    user_id, expected_revision = profile_ref
    current = await manager.get_profile(user_id)
    if current is None:
        raise EntityNotFoundError("用户画像不存在")
    current_entity = _safe_profile_to_dict(current)
    if current_entity is None:
        raise RuntimeError("profile serialization failed")
    current_preferences = current_entity.get("preferences", {})
    editable_preferences = {
        "reply_style": current_preferences.get("reply_style", "casual"),
        "preferred_topics": current_preferences.get("preferred_topics", []),
        "avoided_topics": current_preferences.get("avoided_topics", []),
        "active_hours": current_preferences.get("active_hours", []),
    }
    current_tags = list(current_entity.get("tags", []) or [])
    identity = (tag["category"], tag["value"])
    if action == "tags_add":
        if not any(
            isinstance(item, Mapping)
            and (item.get("category"), item.get("value")) == identity
            for item in current_tags
        ):
            current_tags.append(dict(tag))
    else:
        current_tags = [
            item
            for item in current_tags
            if not (
                isinstance(item, Mapping)
                and (item.get("category"), item.get("value")) == identity
            )
        ]
    await manager.update_profile_manual(
        user_id=user_id,
        display_name=current_entity.get("display_name", ""),
        preferences=editable_preferences,
        tags=current_tags,
        expected_revision=expected_revision,
    )
