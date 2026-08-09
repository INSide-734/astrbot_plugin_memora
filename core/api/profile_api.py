"""控制台 API：用户画像的列表、详情、更新、删除与标签管理。"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from functools import wraps
from typing import Any

from astrbot.api import logger
from quart import request

from ..features.profiles.domain.models import TagCategory, UserTag
from ..features.profiles.infrastructure.profile_store import PROFILE_SORT_COLUMNS
from ..shared.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)
from ..shared.list_sorting import parse_sort_query
from .editing_utils import (
    conflict_error,
    entity_ok,
    finite_float,
    reject_unknown_fields,
    require_object,
    required_text,
)
from .response_utils import error_response, ok_response

_CREATE_FIELDS = frozenset({"user_id", "display_name", "preferences", "tags"})
_UPDATE_FIELDS = frozenset({"identity", "changes", "expected_revision"})
_DELETE_FIELDS = frozenset({"identity", "expected_revision"})
_IDENTITY_FIELDS = frozenset({"user_id"})
_EDITABLE_FIELDS = frozenset({"display_name", "preferences", "tags"})
_PREFERENCE_FIELDS = frozenset(
    {"reply_style", "preferred_topics", "avoided_topics", "active_hours"}
)
_TAG_FIELDS = frozenset({"category", "value", "confidence"})
_BATCH_FIELDS = frozenset({"action", "items", "params"})
_BATCH_ITEM_FIELDS = frozenset({"identity", "expected_revision"})
_BATCH_ACTIONS = frozenset({"delete", "tags_add", "tags_remove"})
_MAX_BATCH_ITEMS = 100
_MAX_TAGS = 100


_AUDIT_EMITTED: ContextVar[bool] = ContextVar(
    "profile_mutation_audit_emitted", default=False
)
_AUDIT_ACTION: ContextVar[str] = ContextVar(
    "profile_mutation_audit_action", default="mutation"
)
_AUDIT_IDENTITY: ContextVar[Any] = ContextVar(
    "profile_mutation_audit_identity", default="unavailable"
)


def _select_audit(action: str, identity: Any = "unavailable") -> None:
    _AUDIT_ACTION.set(action)
    _AUDIT_IDENTITY.set(identity)


def _audit_event(
    action: str,
    identity: Any,
    *,
    result: str,
    error_code: str = "none",
    error_class: str = "none",
    count: int = 1,
    succeeded_count: int | None = None,
    failed_count: int | None = None,
) -> None:
    _AUDIT_EMITTED.set(True)
    if succeeded_count is not None or failed_count is not None:
        logger.info(
            "[画像 AUDIT] action=%s entity=profile identity=%s result=%s error_code=%s error_class=%s succeeded_count=%d failed_count=%d",
            action,
            identity,
            result,
            error_code,
            error_class,
            0 if succeeded_count is None else succeeded_count,
            0 if failed_count is None else failed_count,
        )
        return
    logger.info(
        "[画像 AUDIT] action=%s entity=profile identity=%s result=%s error_code=%s error_class=%s count=%d",
        action,
        identity,
        result,
        error_code,
        error_class,
        count,
    )


def _audit_boundary(action: str):
    def decorate(handler):
        @wraps(handler)
        async def wrapped(*args, **kwargs):
            emitted_token = _AUDIT_EMITTED.set(False)
            action_token = _AUDIT_ACTION.set(action)
            identity_token = _AUDIT_IDENTITY.set("unavailable")
            try:
                try:
                    response = await handler(*args, **kwargs)
                except Exception as exc:
                    response = _exception_response(
                        exc,
                        operation=_AUDIT_ACTION.get(),
                        identity=_AUDIT_IDENTITY.get(),
                    )
                if (
                    isinstance(response, dict)
                    and response.get("status") == "error"
                    and not _AUDIT_EMITTED.get()
                ):
                    code = response.get("code")
                    _audit_event(
                        _AUDIT_ACTION.get(),
                        _AUDIT_IDENTITY.get(),
                        result="failure",
                        error_code=(
                            code if isinstance(code, str) and code else "request_error"
                        ),
                    )
                return response
            finally:
                _AUDIT_IDENTITY.reset(identity_token)
                _AUDIT_ACTION.reset(action_token)
                _AUDIT_EMITTED.reset(emitted_token)

        return wrapped

    return decorate


def _coerce_user_id(raw_user_id: Any) -> str:
    """将外部用户 ID 转为字符串，同时拒绝空值、布尔值与结构化值。"""
    if raw_user_id is None or isinstance(
        raw_user_id, (bool, Mapping, list, tuple, set)
    ):
        return ""
    return str(raw_user_id).strip()


def _safe_identity(raw_user_id: Any) -> Any:
    if raw_user_id is None or isinstance(
        raw_user_id, (bool, Mapping, list, tuple, set)
    ):
        return "unavailable"
    user_id = _coerce_user_id(raw_user_id)
    return {"user_id": user_id} if user_id else "unavailable"


def _returned_profile_identity(profile: Any) -> Any:
    try:
        return _safe_identity(profile.user_id)
    except Exception:
        return "unavailable"


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


def _sort_query_error(exc: ValueError):
    message = str(exc)
    field = "sort_order" if message == "sort_order must be asc or desc" else "sort_by"
    return error_response(
        message,
        code="invalid_query",
        field_errors={field: message},
    )


def _profile_response_or_error(profile: Any, *, revision: str | None = None):
    payload = profile.to_dict()
    if not isinstance(payload, dict):
        raise TypeError("profile serialization returned a non-object")
    if revision is not None:
        payload["revision"] = revision
    return ok_response(payload)


def _json_object_payload_or_error(payload: Any):
    if isinstance(payload, dict):
        return payload, None
    return None, error_response("请求体必须为 JSON 对象")


def _validation_error(exc: EntityValidationError) -> dict[str, Any]:
    return error_response(
        "画像数据校验失败",
        code="validation_error",
        field_errors=exc.field_errors,
    )


def _field_error(field: str, message: str) -> dict[str, Any]:
    return _validation_error(EntityValidationError({field: message}))


def _component_unavailable() -> dict[str, Any]:
    return error_response("用户画像管理器不可用", code="component_unavailable")


def _exception_response(
    exc: Exception,
    *,
    operation: str,
    identity: Any = "unavailable",
    audit: bool = True,
) -> dict[str, Any]:
    error_code = (
        "validation_error"
        if isinstance(exc, EntityValidationError)
        else "already_exists"
        if isinstance(exc, EntityAlreadyExistsError)
        else "not_found"
        if isinstance(exc, EntityNotFoundError)
        else "edit_conflict"
        if isinstance(exc, EditConflictError)
        else "internal_error"
    )
    if audit:
        _audit_event(
            operation,
            identity,
            result="failure",
            error_code=error_code,
            error_class=type(exc).__name__,
        )
    if isinstance(exc, EntityValidationError):
        return _validation_error(exc)
    if isinstance(exc, EntityAlreadyExistsError):
        return error_response("用户画像已存在", code="already_exists")
    if isinstance(exc, EntityNotFoundError):
        return error_response("用户画像不存在", code="not_found")
    if isinstance(exc, EditConflictError):
        return conflict_error(
            exc.current_entity,
            current_revision=exc.current_revision,
        )
    logger.error(
        "[画像 API] operation=%s error_class=%s",
        operation,
        type(exc).__name__,
    )
    return error_response("用户画像操作失败", code="internal_error")


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


class ProfileApiMixin:
    async def list_profiles(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        manager = getattr(engine, "profile_manager", None)
        if manager is None:
            return ok_response({"profiles": [], "total": 0})
        args = request.args
        try:
            limit = int(args.get("limit", 50))
            offset = int(args.get("offset", 0))
        except (TypeError, ValueError):
            return error_response("limit 和 offset 必须为整数")
        try:
            sort = parse_sort_query(
                args,
                allowed=PROFILE_SORT_COLUMNS,
                default_by="last_seen_at",
                default_order="desc",
            )
        except ValueError as exc:
            return _sort_query_error(exc)
        profiles, total = await manager.list_profiles(
            limit=limit,
            offset=offset,
            sort=sort,
        )
        serialized_profiles: list[dict[str, Any]] = []
        for profile in _safe_profile_list(profiles):
            item = _safe_profile_to_dict(profile)
            if item is None:
                continue
            try:
                item["revision"] = manager.revision_for(profile)
            except Exception:
                continue
            serialized_profiles.append(item)
        return ok_response(
            {
                "profiles": serialized_profiles,
                "total": _safe_total(total, 0),
                "limit": limit,
                "offset": offset,
            }
        )

    async def get_profile_detail(self):
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        manager = getattr(engine, "profile_manager", None)
        if manager is None:
            return _component_unavailable()
        user_id = _coerce_user_id(request.args.get("user_id", ""))
        if not user_id:
            return error_response("user_id required: 缺少必填参数 user_id")
        profile = await manager.get_profile(user_id)
        if profile is None:
            return error_response("画像不存在")
        try:
            revision = manager.revision_for(profile)
            return _profile_response_or_error(profile, revision=revision)
        except Exception as exc:
            return _exception_response(exc, operation="detail_mapping", audit=False)

    @_audit_boundary("create")
    async def create_profile(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        try:
            payload, error = require_object(await request.get_json(silent=True))
            if error:
                return error
            _select_audit("create", _safe_identity(payload.get("user_id", "")))
            unknown = reject_unknown_fields(payload, _CREATE_FIELDS)
            if unknown:
                return unknown
            editable_error = _validate_editable_payload(payload)
            if editable_error:
                return editable_error
            engines, ready_error = await self._ensure_plugin_ready()
            if ready_error:
                return ready_error
            manager = getattr(engines["memory_engine"], "profile_manager", None)
            if manager is None:
                return _component_unavailable()
            profile = await manager.create_profile_manual(
                user_id=payload.get("user_id"),
                display_name=payload.get("display_name", ""),
                preferences=payload.get("preferences", {}),
                tags=payload.get("tags", []),
            )
            _select_audit("create", _returned_profile_identity(profile))
            entity = profile.to_dict()
            if not isinstance(entity, dict):
                raise TypeError("profile serialization returned a non-object")
            response = entity_ok(entity, revision=manager.revision_for(profile))
            _audit_event("create", _AUDIT_IDENTITY.get(), result="success")
            return response
        except Exception as exc:
            return _exception_response(
                exc,
                operation=_AUDIT_ACTION.get(),
                identity=_AUDIT_IDENTITY.get(),
            )

    @_audit_boundary("update")
    async def update_profile(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        try:
            payload = await request.get_json(silent=True)
            payload, error = _json_object_payload_or_error(payload)
            if error:
                return error
            if any(
                field in payload
                for field in ("identity", "changes", "expected_revision")
            ):
                return await self._update_profile_envelope(payload)
            user_id = _coerce_user_id(payload.get("user_id", ""))
            _select_audit("legacy_update", _safe_identity(user_id))
            if not user_id:
                return error_response("user_id required: 缺少必填参数 user_id")
            if "preferences" in payload:
                validation_error = _validate_preferences(
                    payload["preferences"], field="preferences"
                )
                if validation_error:
                    return validation_error
            preferences = None
            if "preferences" in payload:
                preferences = dict(payload["preferences"])
                if "reply_style" in preferences:
                    preferences["reply_style"] = preferences["reply_style"].strip()
                for topic_field in ("preferred_topics", "avoided_topics"):
                    if topic_field in preferences:
                        preferences[topic_field] = list(
                            dict.fromkeys(
                                item.strip() for item in preferences[topic_field]
                            )
                        )
                if "active_hours" in preferences:
                    preferences["active_hours"] = list(
                        dict.fromkeys(preferences["active_hours"])
                    )
            engines, ready_error = await self._ensure_plugin_ready()
            if ready_error:
                return ready_error
            manager = getattr(engines["memory_engine"], "profile_manager", None)
            if manager is None:
                return _component_unavailable()
            display_name = None
            if "display_name" in payload:
                raw_display_name = payload["display_name"]
                if not isinstance(raw_display_name, str):
                    return _field_error("display_name", "必须为字符串")
                display_name = raw_display_name.strip()
                if len(display_name) > 128:
                    return _field_error("display_name", "不得超过 128 字符")
            profile = await manager.update_profile_fields(
                user_id,
                display_name=display_name,
                preferences=preferences,
            )
            if profile is None:
                return error_response("画像不存在")
            response = _profile_response_or_error(profile)
            if response.get("status") == "ok":
                _audit_event("legacy_update", {"user_id": user_id}, result="success")
            return response
        except Exception as exc:
            return _exception_response(
                exc,
                operation=_AUDIT_ACTION.get(),
                identity=_AUDIT_IDENTITY.get(),
            )

    async def _update_profile_envelope(self, payload: Mapping[str, Any]):
        unknown = reject_unknown_fields(payload, _UPDATE_FIELDS)
        if unknown:
            return unknown
        identity, user_id, identity_error = _parse_identity(payload.get("identity"))
        if identity_error:
            return identity_error
        _select_audit("update", identity)
        changes, changes_error = require_object(payload.get("changes"))
        if changes_error:
            return _field_error("changes", "必须为对象")
        unknown_changes = reject_unknown_fields(changes, _EDITABLE_FIELDS)
        if unknown_changes:
            return unknown_changes
        editable_error = _validate_editable_payload(changes, prefix="changes.")
        if editable_error:
            return editable_error
        revision, revision_error = _parse_revision(payload.get("expected_revision"))
        if revision_error:
            return revision_error
        engines, ready_error = await self._ensure_plugin_ready()
        if ready_error:
            return ready_error
        manager = getattr(engines["memory_engine"], "profile_manager", None)
        if manager is None:
            return _component_unavailable()
        profile = await manager.update_profile_manual(
            user_id=user_id,
            display_name=changes.get("display_name", ""),
            preferences=changes.get("preferences", {}),
            tags=changes.get("tags", []),
            expected_revision=revision,
        )
        entity = profile.to_dict()
        if not isinstance(entity, dict):
            raise TypeError("profile serialization returned a non-object")
        response = entity_ok(entity, revision=manager.revision_for(profile))
        _audit_event("update", identity, result="success")
        return response

    @_audit_boundary("delete")
    async def delete_profile(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        try:
            payload = await request.get_json(silent=True)
            payload, error = _json_object_payload_or_error(payload)
            if error:
                return error
            if "identity" in payload or "expected_revision" in payload:
                return await self._delete_profile_envelope(payload)
            user_id = _coerce_user_id(payload.get("user_id", ""))
            _select_audit("legacy_delete", _safe_identity(user_id))
            if not user_id:
                return error_response("user_id required: 缺少必填参数 user_id")
            engines, ready_error = await self._ensure_plugin_ready()
            if ready_error:
                return ready_error
            manager = getattr(engines["memory_engine"], "profile_manager", None)
            if manager is None:
                return _component_unavailable()
            deleted = await manager.delete_profile(user_id)
            response = ok_response({"deleted": deleted, "user_id": user_id})
            if deleted:
                _audit_event("legacy_delete", {"user_id": user_id}, result="success")
            else:
                _audit_event(
                    "legacy_delete",
                    {"user_id": user_id},
                    result="failure",
                    error_code="not_deleted",
                )
            return response
        except Exception as exc:
            return _exception_response(
                exc,
                operation=_AUDIT_ACTION.get(),
                identity=_AUDIT_IDENTITY.get(),
            )

    async def _delete_profile_envelope(self, payload: Mapping[str, Any]):
        unknown = reject_unknown_fields(payload, _DELETE_FIELDS)
        if unknown:
            return unknown
        identity, user_id, identity_error = _parse_identity(payload.get("identity"))
        if identity_error:
            return identity_error
        _select_audit("delete", identity)
        revision, revision_error = _parse_revision(payload.get("expected_revision"))
        if revision_error:
            return revision_error
        engines, ready_error = await self._ensure_plugin_ready()
        if ready_error:
            return ready_error
        manager = getattr(engines["memory_engine"], "profile_manager", None)
        if manager is None:
            return _component_unavailable()
        deleted = await manager.delete_profile_manual(
            user_id, expected_revision=revision
        )
        if not deleted:
            raise EntityNotFoundError("用户画像不存在")
        response = ok_response({"deleted": True, "identity": identity})
        _audit_event("delete", identity, result="success")
        return response

    @_audit_boundary("batch")
    async def batch_delete_profiles(self):
        """兼容旧批量删除，并提供带修订版本的安全批量动作。"""
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        try:
            payload, error = require_object(await request.get_json(silent=True))
            if error:
                return error
            if "user_ids" in payload:
                return await self._legacy_batch_delete_profiles(payload)
            return await self._batch_profile_actions(payload)
        except Exception as exc:
            return _exception_response(exc, operation="batch")

    async def _legacy_batch_delete_profiles(self, payload: Mapping[str, Any]):
        unknown = reject_unknown_fields(payload, frozenset({"action", "user_ids"}))
        if unknown:
            return unknown
        if payload.get("action", "delete") != "delete":
            return _field_error("action", "仅支持 delete")
        user_ids = payload.get("user_ids", [])
        if not isinstance(user_ids, list) or not user_ids:
            return error_response("需要提供用户 ID 列表")
        if len(user_ids) > _MAX_BATCH_ITEMS:
            return _field_error("user_ids", "项目数量不能超过 100")
        engines, ready_error = await self._ensure_plugin_ready()
        if ready_error:
            return ready_error
        manager = getattr(engines["memory_engine"], "profile_manager", None)
        if manager is None:
            return _component_unavailable()
        deleted_count = 0
        failed_ids: list[Any] = []
        for raw_id in user_ids:
            try:
                user_id = _coerce_user_id(raw_id)
                if not user_id:
                    failed_ids.append(raw_id)
                    continue
                if await manager.delete_profile(user_id):
                    deleted_count += 1
                else:
                    failed_ids.append(raw_id)
            except Exception as exc:
                logger.error(
                    "[画像 API] operation=legacy_batch_delete_item error_class=%s",
                    type(exc).__name__,
                )
                failed_ids.append(raw_id)
        batch_result = (
            "success"
            if not failed_ids
            else ("failure" if not deleted_count else "partial")
        )
        _audit_event(
            "legacy_batch_delete",
            "batch",
            result=batch_result,
            error_code="none" if not failed_ids else "item_failure",
            succeeded_count=deleted_count,
            failed_count=len(failed_ids),
        )
        return ok_response(
            {
                "deleted_count": deleted_count,
                "failed_count": len(failed_ids),
                "total": len(user_ids),
                "failed_ids": failed_ids,
            }
        )

    async def _batch_profile_actions(self, payload: Mapping[str, Any]):
        unknown = reject_unknown_fields(payload, _BATCH_FIELDS)
        if unknown:
            return unknown
        try:
            action = required_text(payload.get("action"), field="action")
        except EntityValidationError as exc:
            return _validation_error(exc)
        if action not in _BATCH_ACTIONS:
            return _field_error("action", "仅支持 delete、tags_add 或 tags_remove")
        items = payload.get("items")
        if not isinstance(items, list):
            return _field_error("items", "必须为数组")
        if not 1 <= len(items) <= _MAX_BATCH_ITEMS:
            return _field_error("items", "项目数量必须在 1 到 100 之间")
        params, params_error = require_object(payload.get("params", {}))
        if params_error:
            return _field_error("params", "必须为对象")
        allowed_params = frozenset() if action == "delete" else frozenset({"tag"})
        unknown_params = reject_unknown_fields(params, allowed_params)
        if unknown_params:
            return unknown_params
        tag = None
        if action in {"tags_add", "tags_remove"}:
            try:
                tag = _normalize_batch_tag(params.get("tag"))
            except EntityValidationError as exc:
                return _validation_error(exc)

        engines, ready_error = await self._ensure_plugin_ready()
        if ready_error:
            return ready_error
        manager = getattr(engines["memory_engine"], "profile_manager", None)
        if manager is None:
            return _component_unavailable()

        succeeded_ids: list[dict[str, str]] = []
        failures: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            identity_ref: dict[str, Any] = {"item_index": index}
            identity, user_id, revision, item_error = _parse_batch_item(item)
            if identity is not None:
                identity_ref = identity
            if item_error:
                failures.append(_batch_failure(identity_ref, item_error))
                continue
            try:
                if action == "delete":
                    deleted = await manager.delete_profile_manual(
                        user_id, expected_revision=revision
                    )
                    if not deleted:
                        raise EntityNotFoundError("用户画像不存在")
                else:
                    await _apply_revisioned_tag_action(
                        manager,
                        (user_id, revision),
                        action,
                        tag,
                    )
                succeeded_ids.append(identity)
            except Exception as exc:
                item_response = _exception_response(
                    exc,
                    operation="batch_" + action + "_item",
                    audit=False,
                )
                failures.append(_batch_failure(identity_ref, item_response))
        batch_result = (
            "success"
            if not failures
            else ("failure" if not succeeded_ids else "partial")
        )
        _audit_event(
            "batch_" + action,
            "batch",
            result=batch_result,
            error_code="none" if not failures else "item_failure",
            succeeded_count=len(succeeded_ids),
            failed_count=len(failures),
        )
        return ok_response(
            {
                "total": len(items),
                "succeeded_count": len(succeeded_ids),
                "failed_count": len(failures),
                "succeeded_ids": succeeded_ids,
                "failures": failures,
            }
        )

    @_audit_boundary("manage_tags")
    async def manage_profile_tags(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        payload = await request.get_json(silent=True)
        payload, error = _json_object_payload_or_error(payload)
        if error:
            return error
        user_id = _coerce_user_id(payload.get("user_id", ""))
        action = str(payload.get("action", ""))
        if not user_id:
            return error_response("user_id required: 缺少必填参数 user_id")
        if action not in ("add", "remove"):
            return error_response("action 必须为 'add' 或 'remove'")
        _select_audit("tag_" + action, {"user_id": user_id})
        engines, err = await self._ensure_plugin_ready()
        if err:
            return err
        engine = engines["memory_engine"]
        manager = getattr(engine, "profile_manager", None)
        if manager is None:
            return _component_unavailable()
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
            return _field_error("tag", "category 和 value 必须为字符串")
        category_value = raw_category.strip()
        value = raw_value.strip()
        if not category_value or not value or len(value) > 128:
            return _field_error("tag", "category 和 value 必须为非空有界字符串")
        if category_value not in {item.value for item in TagCategory}:
            return _field_error("tag.category", "未知标签分类")
        if action == "add":
            try:
                confidence = finite_float(
                    tag.get("confidence", 0.5), field="tag.confidence"
                )
            except EntityValidationError:
                return error_response("confidence 必须为数字", code="validation_error")
            if not 0.0 <= confidence <= 1.0:
                return _field_error("tag.confidence", "必须在 0.0 到 1.0 之间")
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
        response = _profile_response_or_error(profile)
        _audit_event("tag_" + action, {"user_id": user_id}, result="success")
        return response


__all__ = ["ProfileApiMixin"]
