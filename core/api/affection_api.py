"""控制台的好感度状态与情绪概览 API。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from astrbot.api import logger
from quart import request

from ..affection.models import MoodType
from ..base.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)
from .editing_utils import (
    bounded_int,
    conflict_error,
    entity_ok,
    finite_float,
    reject_unknown_fields,
    require_object,
    required_text,
)
from .response_utils import error_response, ok_response


_CREATE_FIELDS = frozenset({"group_id", "user_id", "affection_score"})
_UPDATE_FIELDS = frozenset({"identity", "changes", "expected_revision"})
_DELETE_FIELDS = frozenset({"identity", "expected_revision"})
_IDENTITY_FIELDS = frozenset({"group_id", "user_id"})
_EDITABLE_FIELDS = frozenset({"affection_score"})
_BATCH_FIELDS = frozenset({"action", "items", "params"})
_BATCH_ITEM_FIELDS = frozenset({"identity", "expected_revision"})
_BATCH_ACTIONS = frozenset({"delete"})
_MAX_BATCH_ITEMS = 100
_MAX_PAGE_SIZE = 100


def _affection_user_to_dict(user: Any) -> dict[str, Any]:
    """将 ``UserAffection`` 转换为 JSON 响应字典。"""
    return {
        "user_id": getattr(user, "user_id", ""),
        "group_id": getattr(user, "group_id", ""),
        "affection_score": getattr(user, "affection_score", 0),
        "affection_level": getattr(user, "level", None).name if hasattr(getattr(user, "level", None), "name") else str(getattr(user, "level", "NEUTRAL")),
        "level_name": getattr(user, "level_name", ""),
        "interaction_count": getattr(user, "interaction_count", 0),
        "last_interaction": getattr(user, "last_interaction", 0.0),
    }


def _mood_to_dict(mood: Any) -> dict[str, Any]:
    """将 ``BotMood`` 转换为 JSON 响应字典。"""
    return {
        "mood_type": getattr(mood, "mood_type", None).value if hasattr(getattr(mood, "mood_type", None), "value") else str(getattr(mood, "mood_type", "NEUTRAL")),
        "intensity": getattr(mood, "intensity", 0.0),
        "description": getattr(mood, "description", ""),
        "start_time": getattr(mood, "start_time", 0.0),
        "duration_hours": getattr(mood, "duration_hours", 4.0),
        "is_active": getattr(mood, "is_active", lambda: False)() if callable(getattr(mood, "is_active", None)) else False,
    }


def _safe_affection_user_to_dict(user: Any) -> dict[str, Any] | None:
    try:
        return _affection_user_to_dict(user)
    except Exception:
        return None


def _safe_mood_to_dict(mood: Any) -> dict[str, Any] | None:
    try:
        return _mood_to_dict(mood)
    except Exception:
        return None


def _validation_error(exc: EntityValidationError) -> dict[str, Any]:
    return error_response(
        "好感度校验失败",
        code="validation_error",
        field_errors=exc.field_errors,
    )


def _component_unavailable() -> dict[str, Any]:
    return error_response("好感度管理器不可用", code="component_unavailable")


def _exception_response(exc: Exception, *, operation: str) -> dict[str, Any]:
    if isinstance(exc, EntityValidationError):
        return _validation_error(exc)
    if isinstance(exc, EntityAlreadyExistsError):
        return error_response("好感度记录已存在", code="already_exists")
    if isinstance(exc, EntityNotFoundError):
        return error_response("好感度记录不存在", code="not_found")
    if isinstance(exc, EditConflictError):
        return conflict_error(
            exc.current_entity,
            current_revision=exc.current_revision,
        )
    logger.error(
        "[好感度 API] action=%s error_class=%s",
        operation,
        type(exc).__name__,
    )
    return error_response("好感度操作失败", code="internal_error")


def _parse_identity(value: Any) -> tuple[dict[str, str] | None, dict | None]:
    identity, error = require_object(value)
    if error:
        return None, error
    unknown = reject_unknown_fields(identity, _IDENTITY_FIELDS)
    if unknown:
        return None, unknown
    try:
        return {
            "group_id": required_text(identity.get("group_id"), field="identity.group_id"),
            "user_id": required_text(identity.get("user_id"), field="identity.user_id"),
        }, None
    except EntityValidationError as exc:
        return None, _validation_error(exc)


def _parse_revision(value: Any) -> tuple[str | None, dict | None]:
    try:
        return required_text(value, field="expected_revision", maximum=256), None
    except EntityValidationError as exc:
        return None, _validation_error(exc)


def _parse_query_int(
    value: Any,
    *,
    field: str,
    default: int,
    minimum: int,
    maximum: int,
) -> tuple[int | None, dict | None]:
    if value is None or value == "":
        return default, None
    try:
        return bounded_int(int(value), field=field, minimum=minimum, maximum=maximum), None
    except (TypeError, ValueError):
        return None, _validation_error(EntityValidationError({field: "必须为整数"}))
    except EntityValidationError as exc:
        return None, _validation_error(exc)


def _batch_failure(identity: Mapping[str, Any], error: Mapping[str, Any]) -> dict[str, Any]:
    failure: dict[str, Any] = {
        "identity": dict(identity),
        "code": error.get("code", "internal_error"),
        "message": error.get("message", "好感度操作失败"),
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


class AffectionApiMixin:
    """为 Memora 控制台提供好感度 REST 端点的混入类。"""

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _get_affection_manager(self) -> Any | None:
        """从插件属性中惰性解析 ``AffectionManager``。"""
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        for attr_name in ("_affection_manager", "affection_manager"):
            obj = getattr(plugin, attr_name, None)
            if obj is not None:
                return obj
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            for attr_name in ("affection_manager", "_affection_manager"):
                obj = getattr(initializer, attr_name, None)
                if obj is not None:
                    return obj
        return None

    def _get_affection_store(self) -> Any | None:
        """从插件属性中惰性解析 ``AffectionStore``。"""
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        for attr_name in ("_affection_store", "affection_store"):
            obj = getattr(plugin, attr_name, None)
            if obj is not None:
                return obj
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            for attr_name in ("affection_store", "_affection_store"):
                obj = getattr(initializer, attr_name, None)
                if obj is not None:
                    return obj
        return None

    # ------------------------------------------------------------------
    # GET /affection/status
    # ------------------------------------------------------------------

    async def get_affection_status(self):
        """返回指定群组的好感度汇总与 Bot 当前情绪。

        查询参数：
            group_id (str, 可选): 群组标识，默认取首个可用群组。
        """
        manager = self._get_affection_manager()
        if manager is None:
            return error_response("好感度管理器不可用")

        args = request.args
        group_id = (args.get("group_id", "") or "").strip()

        try:
            # 如果未提供 group_id，则尝试回退到任意可用群组
            if not group_id:
                store = self._get_affection_store()
                if store is not None:
                    try:
                        # 尝试从存储层取第一个群组
                        groups = await store.list_groups() if hasattr(store, "list_groups") else []
                        if groups:
                            group_id = groups[0] if isinstance(groups[0], str) else getattr(groups[0], "group_id", str(groups[0]))
                    except Exception as exc:
                        logger.debug(
                            "[AffectionApi] list_groups fallback failed: %s",
                            exc,
                            exc_info=True,
                        )
                if not group_id:
                    group_id = "default"

            status = await manager.get_group_affection_status(group_id)
            if status is None:
                return error_response(f"群组 {group_id} 没有好感度数据")

            result: dict[str, Any] = {
                "group_id": status.get("group_id", group_id),
                "total_affection": status.get("total_affection", 0),
                "max_total_affection": status.get("max_total_affection", 0),
                "user_count": status.get("user_count", 0),
                "top_users": [
                    item
                    for item in (
                        _safe_affection_user_to_dict(u)
                        for u in status.get("top_users", [])
                    )
                    if item is not None
                ],
            }

            mood = status.get("current_mood")
            if mood is not None:
                if isinstance(mood, dict):
                    result["current_mood"] = mood
                else:
                    result["current_mood"] = _safe_mood_to_dict(mood)
            else:
                # 尝试直接补取情绪信息
                try:
                    mood = await manager.get_mood(group_id)
                    if mood is not None:
                        result["current_mood"] = _safe_mood_to_dict(mood)
                    else:
                        result["current_mood"] = None
                except Exception as exc:
                    logger.debug(
                        "[AffectionApi] direct mood fetch failed for group=%s: %s",
                        group_id,
                        exc,
                        exc_info=True,
                    )
                    result["current_mood"] = None

            return ok_response(result)
        except Exception as e:
            logger.error(f"[AffectionApi] 获取好感度状态失败: {e}", exc_info=True)
            return error_response(f"获取好感度状态失败: {e}")

    async def list_affection_users(self):
        manager = self._get_affection_manager()
        if manager is None:
            return _component_unavailable()
        try:
            group_id = required_text(request.args.get("group_id"), field="group_id")
            limit, error = _parse_query_int(
                request.args.get("limit"),
                field="limit",
                default=50,
                minimum=1,
                maximum=_MAX_PAGE_SIZE,
            )
            if error:
                return error
            offset, error = _parse_query_int(
                request.args.get("offset"),
                field="offset",
                default=0,
                minimum=0,
                maximum=1_000_000,
            )
            if error:
                return error
            users, total = await manager.list_user_affections(group_id, limit, offset)
            serialized = []
            for user in users:
                entity = _safe_affection_user_to_dict(user)
                if entity is None:
                    continue
                entity["revision"] = manager.revision_for_affection(user)
                serialized.append(entity)
            return ok_response(
                {
                    "group_id": group_id,
                    "users": serialized,
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                }
            )
        except Exception as exc:
            return _exception_response(exc, operation="list_users")

    async def create_affection_user(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        try:
            payload, error = require_object(await request.get_json(silent=True))
            if error:
                return error
            unknown = reject_unknown_fields(payload, _CREATE_FIELDS)
            if unknown:
                return unknown
            group_id = required_text(payload.get("group_id"), field="group_id")
            user_id = required_text(payload.get("user_id"), field="user_id")
            score = bounded_int(
                payload.get("affection_score"),
                field="affection_score",
                minimum=-100,
                maximum=100,
            )
            manager = self._get_affection_manager()
            if manager is None:
                return _component_unavailable()
            user = await manager.create_user_affection_manual(group_id, user_id, score)
            logger.info(
                "[好感度 API] action=create group_id=%s user_id=%s",
                group_id,
                user_id,
            )
            return entity_ok(
                _affection_user_to_dict(user),
                revision=manager.revision_for_affection(user),
            )
        except Exception as exc:
            return _exception_response(exc, operation="create")

    async def update_affection_user(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        try:
            payload, error = require_object(await request.get_json(silent=True))
            if error:
                return error
            unknown = reject_unknown_fields(payload, _UPDATE_FIELDS)
            if unknown:
                return unknown
            identity, error = _parse_identity(payload.get("identity"))
            if error:
                return error
            changes, error = require_object(payload.get("changes"))
            if error:
                return error
            unknown = reject_unknown_fields(changes, _EDITABLE_FIELDS)
            if unknown:
                return unknown
            score = bounded_int(
                changes.get("affection_score"),
                field="changes.affection_score",
                minimum=-100,
                maximum=100,
            )
            revision, error = _parse_revision(payload.get("expected_revision"))
            if error:
                return error
            manager = self._get_affection_manager()
            if manager is None:
                return _component_unavailable()
            user = await manager.update_user_affection_manual(
                identity["group_id"],
                identity["user_id"],
                score,
                expected_revision=revision,
            )
            logger.info(
                "[好感度 API] action=update group_id=%s user_id=%s",
                identity["group_id"],
                identity["user_id"],
            )
            return entity_ok(
                _affection_user_to_dict(user),
                revision=manager.revision_for_affection(user),
            )
        except Exception as exc:
            return _exception_response(exc, operation="update")

    async def delete_affection_user(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        try:
            payload, error = require_object(await request.get_json(silent=True))
            if error:
                return error
            unknown = reject_unknown_fields(payload, _DELETE_FIELDS)
            if unknown:
                return unknown
            identity, error = _parse_identity(payload.get("identity"))
            if error:
                return error
            revision, error = _parse_revision(payload.get("expected_revision"))
            if error:
                return error
            manager = self._get_affection_manager()
            if manager is None:
                return _component_unavailable()
            deleted = await manager.delete_user_affection_manual(
                identity["group_id"],
                identity["user_id"],
                expected_revision=revision,
            )
            if not deleted:
                raise EntityNotFoundError("好感度记录不存在")
            logger.info(
                "[好感度 API] action=delete group_id=%s user_id=%s",
                identity["group_id"],
                identity["user_id"],
            )
            return ok_response({"deleted": True, "identity": identity})
        except Exception as exc:
            return _exception_response(exc, operation="delete")

    async def batch_affection_users(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        try:
            payload, error = require_object(await request.get_json(silent=True))
            if error:
                return error
            unknown = reject_unknown_fields(payload, _BATCH_FIELDS)
            if unknown:
                return unknown
            action = required_text(payload.get("action"), field="action")
            if action not in _BATCH_ACTIONS:
                raise EntityValidationError({"action": "仅支持 delete"})
            items = payload.get("items")
            if not isinstance(items, list) or not 1 <= len(items) <= _MAX_BATCH_ITEMS:
                raise EntityValidationError({"items": "项目数量必须在 1 到 100 之间"})
            params, error = require_object(payload.get("params", {}))
            if error:
                return error
            unknown = reject_unknown_fields(params, frozenset())
            if unknown:
                return unknown
            manager = self._get_affection_manager()
            if manager is None:
                return _component_unavailable()
            succeeded_ids: list[dict[str, str]] = []
            failures: list[dict[str, Any]] = []
            for index, item in enumerate(items):
                identity_ref: dict[str, Any] = {"item_index": index}
                item_payload, item_error = require_object(item)
                if item_error:
                    failures.append(_batch_failure(identity_ref, item_error))
                    continue
                unknown = reject_unknown_fields(item_payload, _BATCH_ITEM_FIELDS)
                if unknown:
                    failures.append(_batch_failure(identity_ref, unknown))
                    continue
                identity, item_error = _parse_identity(item_payload.get("identity"))
                if identity is not None:
                    identity_ref = identity
                if item_error:
                    failures.append(_batch_failure(identity_ref, item_error))
                    continue
                revision, revision_error = _parse_revision(
                    item_payload.get("expected_revision")
                )
                if revision_error:
                    failures.append(_batch_failure(identity_ref, revision_error))
                    continue
                try:
                    deleted = await manager.delete_user_affection_manual(
                        identity["group_id"],
                        identity["user_id"],
                        expected_revision=revision,
                    )
                    if not deleted:
                        raise EntityNotFoundError("好感度记录不存在")
                    succeeded_ids.append(identity)
                    logger.info(
                        "[好感度 API] action=batch_delete group_id=%s user_id=%s",
                        identity["group_id"],
                        identity["user_id"],
                    )
                except Exception as exc:
                    failures.append(
                        _batch_failure(
                            identity_ref,
                            _exception_response(exc, operation=f"batch_{action}_item"),
                        )
                    )
            logger.info(
                "[好感度 API] action=batch_delete succeeded_count=%s failed_count=%s",
                len(succeeded_ids),
                len(failures),
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
        except Exception as exc:
            return _exception_response(exc, operation="batch")

    async def set_affection_mood(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        try:
            payload, error = require_object(await request.get_json(silent=True))
            if error:
                return error
            unknown = reject_unknown_fields(
                payload,
                frozenset({"group_id", "mood_type", "intensity", "duration_hours", "description"}),
            )
            if unknown:
                return unknown
            group_id = required_text(payload.get("group_id"), field="group_id")
            errors: dict[str, str] = {}
            raw_mood_type = payload.get("mood_type")
            try:
                mood_type = MoodType(required_text(raw_mood_type, field="mood_type"))
            except (EntityValidationError, ValueError):
                errors["mood_type"] = "不支持的情绪类型"
                mood_type = None
            try:
                intensity = finite_float(payload.get("intensity"), field="intensity")
            except EntityValidationError as exc:
                errors.update(exc.field_errors)
                intensity = None
            if intensity is not None and not 0.1 <= intensity <= 1.0:
                errors["intensity"] = "必须在 0.1 到 1.0 之间"
            try:
                duration_hours = finite_float(
                    payload.get("duration_hours"), field="duration_hours"
                )
            except EntityValidationError as exc:
                errors.update(exc.field_errors)
                duration_hours = None
            if duration_hours is not None and not 0.25 <= duration_hours <= 168.0:
                errors["duration_hours"] = "必须在 0.25 到 168.0 之间"
            description = payload.get("description")
            if description is not None and not isinstance(description, str):
                errors["description"] = "必须为字符串"
            if errors:
                raise EntityValidationError(errors)
            manager = self._get_affection_manager()
            if manager is None:
                return _component_unavailable()
            mood = await manager.set_mood(
                group_id,
                mood_type,
                intensity,
                duration_hours,
                description,
            )
            logger.info("[好感度 API] action=set_mood group_id=%s", group_id)
            return ok_response(_mood_to_dict(mood))
        except Exception as exc:
            return _exception_response(exc, operation="set_mood")

    async def reset_affection_mood(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        try:
            payload, error = require_object(await request.get_json(silent=True))
            if error:
                return error
            unknown = reject_unknown_fields(payload, frozenset({"group_id"}))
            if unknown:
                return unknown
            group_id = required_text(payload.get("group_id"), field="group_id")
            manager = self._get_affection_manager()
            if manager is None:
                return _component_unavailable()
            mood = await manager.reset_mood(group_id)
            logger.info("[好感度 API] action=reset_mood group_id=%s", group_id)
            return ok_response(_mood_to_dict(mood))
        except Exception as exc:
            return _exception_response(exc, operation="reset_mood")

    async def get_affection_mood_history(self):
        manager = self._get_affection_manager()
        if manager is None:
            return _component_unavailable()
        try:
            group_id = required_text(request.args.get("group_id"), field="group_id")
            limit, error = _parse_query_int(
                request.args.get("limit"),
                field="limit",
                default=20,
                minimum=1,
                maximum=_MAX_PAGE_SIZE,
            )
            if error:
                return error
            history = await manager.get_mood_history(group_id, limit)
            return ok_response(
                {
                    "group_id": group_id,
                    "limit": limit,
                    "history": [
                        item for mood in history if (item := _safe_mood_to_dict(mood)) is not None
                    ],
                }
            )
        except Exception as exc:
            return _exception_response(exc, operation="mood_history")


__all__ = ["AffectionApiMixin"]
