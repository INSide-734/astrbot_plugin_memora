"""控制台社交关系浏览与人工编辑 API。"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from contextvars import ContextVar
from functools import wraps
from typing import Any

from astrbot.api import logger
from quart import request

from ..base.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)
from ..shared.list_sorting import parse_sort_query
from ..social.relation_store import SOCIAL_SORT_COLUMNS
from .editing_utils import (
    conflict_error,
    entity_ok,
    finite_float,
    normalized_string_list,
    reject_unknown_fields,
    require_object,
    required_text,
)
from .response_utils import error_response, ok_response

_CREATE_FIELDS = frozenset(
    {"from_user", "to_user", "group_id", "relation_type", "strength", "tags"}
)
_UPDATE_FIELDS = frozenset({"identity", "changes", "expected_revision"})
_DELETE_FIELDS = frozenset({"identity", "expected_revision"})
_IDENTITY_KEYS = ("from_user", "to_user", "group_id", "relation_type")
_IDENTITY_FIELDS = frozenset(_IDENTITY_KEYS)
_EDITABLE_FIELDS = frozenset({"relation_type", "strength", "tags"})
_BATCH_FIELDS = frozenset({"action", "items", "params"})
_BATCH_ACTIONS = frozenset({"delete", "add_tags", "remove_tags"})


class _AuditScope:
    __slots__ = ("action", "identity", "error_class", "emitted")

    def __init__(self, action: str) -> None:
        self.action = action
        self.identity: dict[str, str] | str = "unavailable"
        self.error_class = "none"
        self.emitted = False


_AUDIT_SCOPE: ContextVar[_AuditScope | None] = ContextVar(
    "social_mutation_audit_scope", default=None
)


def _set_audit_identity(identity: Mapping[str, Any] | None) -> None:
    scope = _AUDIT_SCOPE.get()
    if scope is None or scope.emitted or scope.identity != "unavailable":
        return
    try:
        safe_identity = {field: identity[field] for field in _IDENTITY_KEYS}
    except Exception:
        return
    if not all(isinstance(value, str) for value in safe_identity.values()):
        return
    scope.identity = safe_identity


def _set_audit_relation_identity(relation: Any) -> None:
    try:
        identity = {field: getattr(relation, field) for field in _IDENTITY_KEYS}
    except Exception:
        return
    _set_audit_identity(identity)


def _set_batch_audit_action(action: str) -> None:
    scope = _AUDIT_SCOPE.get()
    if scope is None or scope.emitted or action not in _BATCH_ACTIONS:
        return
    scope.action = f"batch_{action}"
    scope.identity = "batch"


def _set_audit_error_class(exc: Exception) -> None:
    scope = _AUDIT_SCOPE.get()
    if scope is None or scope.emitted or scope.error_class != "none":
        return
    error_class = type(exc).__name__
    if (
        len(error_class) > 128
        or not error_class.isascii()
        or not error_class.isidentifier()
    ):
        error_class = "Exception"
    scope.error_class = error_class


def _audit_event(
    *,
    result: str,
    error_code: str = "none",
    succeeded_count: int | None = None,
    failed_count: int | None = None,
) -> None:
    scope = _AUDIT_SCOPE.get()
    if scope is None or scope.emitted:
        return
    scope.emitted = True
    if succeeded_count is not None or failed_count is not None:
        logger.info(
            "[社交关系 AUDIT] action=%s entity=social_relation identity=%s result=%s error_code=%s error_class=%s succeeded_count=%d failed_count=%d",
            scope.action,
            scope.identity,
            result,
            error_code,
            scope.error_class,
            0 if succeeded_count is None else succeeded_count,
            0 if failed_count is None else failed_count,
        )
        return
    logger.info(
        "[社交关系 AUDIT] action=%s entity=social_relation identity=%s result=%s error_code=%s error_class=%s count=1",
        scope.action,
        scope.identity,
        result,
        error_code,
        "none" if result == "success" else scope.error_class,
    )


def _audit_boundary(action: str):
    def decorate(handler):
        @wraps(handler)
        async def wrapped(*args, **kwargs):
            scope = _AUDIT_SCOPE.get()
            outermost = scope is None
            token = None
            if outermost:
                scope = _AuditScope(action)
                token = _AUDIT_SCOPE.set(scope)
            try:
                try:
                    response = await handler(*args, **kwargs)
                except Exception as exc:
                    _set_audit_error_class(exc)
                    response = _exception_response(exc)
                if (
                    outermost
                    and isinstance(response, dict)
                    and response.get("status") == "error"
                ):
                    code = response.get("code")
                    _audit_event(
                        result="failure",
                        error_code=(
                            code if isinstance(code, str) and code else "request_error"
                        ),
                    )
                return response
            finally:
                if token is not None:
                    _AUDIT_SCOPE.reset(token)

        return wrapped

    return decorate


def _relation_to_dict(rel: Any) -> dict[str, Any]:
    """将 SocialRelation 转换为 JSON 响应所需字典。"""
    category = "unknown"
    try:
        from ..social.models import get_relation_category

        category = (
            get_relation_category(getattr(rel, "relation_type", "stranger"))
            or "unknown"
        )
    except Exception as exc:
        logger.debug(
            "[社交关系 API] operation=relation_category error_class=%s",
            type(exc).__name__,
        )
    return {
        "from_user": getattr(rel, "from_user", ""),
        "to_user": getattr(rel, "to_user", ""),
        "relation_type": getattr(rel, "relation_type", "stranger"),
        "strength": getattr(rel, "strength", 0.0),
        "frequency": getattr(rel, "frequency", 0),
        "last_interaction": getattr(rel, "last_interaction", 0.0),
        "group_id": getattr(rel, "group_id", ""),
        "tags": getattr(rel, "tags", []) or [],
        "category": category,
    }


def _safe_relation_to_dict(rel: Any) -> dict[str, Any] | None:
    try:
        return _relation_to_dict(rel)
    except Exception:
        return None


def _safe_relation_list(relations: Any) -> list[Any]:
    try:
        return list(relations or [])
    except Exception:
        return []


def _optional_bounded_text(value: Any, *, field: str) -> str:
    """规范化允许为空的有界文本字段，同时拒绝布尔值等非字符串。"""
    if not isinstance(value, str):
        raise EntityValidationError({field: "必须为字符串"})
    normalized = value.strip()
    if len(normalized) > 128:
        raise EntityValidationError({field: "文本过长"})
    return normalized


def _validation_error(exc: EntityValidationError) -> dict[str, Any]:
    return error_response(
        "社交关系校验失败",
        code="validation_error",
        field_errors=exc.field_errors,
    )


def _sort_query_error(exc: ValueError) -> dict[str, Any]:
    message = str(exc)
    field = "sort_order" if message == "sort_order must be asc or desc" else "sort_by"
    return error_response(
        message,
        code="invalid_query",
        field_errors={field: message},
    )


def _component_unavailable() -> dict[str, Any]:
    return error_response("关系管理器不可用", code="component_unavailable")


def _exception_response(exc: Exception) -> dict[str, Any]:
    """将领域异常映射为稳定响应，不产生任何日志副作用。"""
    if isinstance(exc, EntityValidationError):
        return _validation_error(exc)
    if isinstance(exc, EntityAlreadyExistsError):
        return error_response("社交关系已存在", code="already_exists")
    if isinstance(exc, EntityNotFoundError):
        return error_response("社交关系不存在", code="not_found")
    if isinstance(exc, EditConflictError):
        return conflict_error(
            exc.current_entity,
            current_revision=exc.current_revision,
        )
    return error_response("社交关系操作失败", code="internal_error")


def _parse_identity(
    value: Any,
) -> tuple[dict[str, str] | None, tuple[str, str, str, str] | None, dict | None]:
    identity, error = require_object(value)
    if error:
        return None, None, error
    unknown_error = reject_unknown_fields(identity, _IDENTITY_FIELDS)
    if unknown_error:
        return None, None, unknown_error
    try:
        normalized = {
            "from_user": required_text(
                identity.get("from_user"), field="identity.from_user"
            ),
            "to_user": required_text(identity.get("to_user"), field="identity.to_user"),
            "group_id": _optional_bounded_text(
                identity.get("group_id"), field="identity.group_id"
            ),
            "relation_type": required_text(
                identity.get("relation_type"), field="identity.relation_type"
            ),
        }
    except EntityValidationError as exc:
        return None, None, _validation_error(exc)
    identity_tuple = (
        normalized["from_user"],
        normalized["to_user"],
        normalized["relation_type"],
        normalized["group_id"],
    )
    return normalized, identity_tuple, None


def _parse_batch_item(
    value: Any,
) -> tuple[
    dict[str, str] | None, tuple[str, str, str, str] | None, str | None, dict | None
]:
    item, error = require_object(value)
    if error:
        return None, None, None, error
    unknown_error = reject_unknown_fields(item, _DELETE_FIELDS)
    if unknown_error:
        return None, None, None, unknown_error
    identity, identity_tuple, identity_error = _parse_identity(item.get("identity"))
    if identity_error:
        return None, None, None, identity_error
    try:
        revision = required_text(
            item.get("expected_revision"),
            field="expected_revision",
            maximum=256,
        )
    except EntityValidationError as exc:
        return identity, identity_tuple, None, _validation_error(exc)
    return identity, identity_tuple, revision, None


def _batch_failure(identity: Mapping[str, Any], error: Mapping[str, Any]) -> dict:
    failure = {
        "identity": dict(identity),
        "code": error.get("code", "internal_error"),
        "message": error.get("message", "社交关系操作失败"),
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


async def _find_social_relation(
    manager: Any,
    identity: tuple[str, str, str, str],
) -> Any:
    """通过管理器现有列表读取能力解析当前关系。"""
    relations = manager.list_all()
    if inspect.isawaitable(relations):
        relations = await relations
    for rel in _safe_relation_list(relations):
        candidate = (
            getattr(rel, "from_user", None),
            getattr(rel, "to_user", None),
            getattr(rel, "relation_type", None),
            getattr(rel, "group_id", None),
        )
        if candidate == identity:
            return rel
    raise EntityNotFoundError("社交关系不存在")


class SocialApiMixin:
    """为 Memora 控制台提供社交关系 REST 端点的混入类。"""

    def _get_relation_manager(self) -> Any | None:
        """按需从插件属性中解析 RelationManager。"""
        plugin = getattr(self, "plugin", None)
        if plugin is None:
            return None
        for attr_name in ("_relation_manager", "relation_manager"):
            obj = getattr(plugin, attr_name, None)
            if obj is not None:
                return obj
        initializer = getattr(plugin, "initializer", None)
        if initializer is not None:
            for attr_name in ("relation_manager", "_relation_manager"):
                obj = getattr(initializer, attr_name, None)
                if obj is not None:
                    return obj
        return None

    async def get_social_relations(self):
        """返回社交关系数据，并可按群组和分类过滤。"""
        manager = self._get_relation_manager()
        if manager is None:
            return _component_unavailable()

        args = request.args
        group_id = (args.get("group_id", "") or "").strip()
        category = (args.get("category", "") or "").strip().lower()
        try:
            sort = parse_sort_query(
                args,
                allowed=SOCIAL_SORT_COLUMNS,
                default_by="last_interaction",
                default_order="desc",
            )
        except ValueError as exc:
            return _sort_query_error(exc)

        try:
            if group_id:
                relations = manager.get_relations_by_group(group_id, sort=sort)
            else:
                relations = (
                    manager.list_all(sort=sort) if hasattr(manager, "list_all") else []
                )
            if inspect.isawaitable(relations):
                relations = await relations

            result_relations = []
            for rel in _safe_relation_list(relations):
                item = _safe_relation_to_dict(rel)
                if item is None:
                    continue
                try:
                    item["revision"] = manager.revision_for(rel)
                except Exception:
                    continue
                if not category or item.get("category") == category:
                    result_relations.append(item)

            return ok_response(
                {
                    "relations": result_relations,
                    "total": len(result_relations),
                }
            )
        except Exception as exc:
            return _exception_response(exc)

    @_audit_boundary("create")
    async def create_social_relation(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        payload, error = require_object(await request.get_json(silent=True))
        if error:
            return error
        unknown_error = reject_unknown_fields(payload, _CREATE_FIELDS)
        if unknown_error:
            return unknown_error
        manager = self._get_relation_manager()
        if manager is None:
            return _component_unavailable()
        rel = await manager.create_manual_relation(
            from_user=required_text(payload.get("from_user"), field="from_user"),
            to_user=required_text(payload.get("to_user"), field="to_user"),
            group_id=_optional_bounded_text(payload.get("group_id"), field="group_id"),
            relation_type=required_text(
                payload.get("relation_type"), field="relation_type"
            ),
            strength=finite_float(payload.get("strength"), field="strength"),
            tags=normalized_string_list(payload.get("tags", []), field="tags"),
        )
        _set_audit_relation_identity(rel)
        response = entity_ok(
            _relation_to_dict(rel),
            revision=manager.revision_for(rel),
        )
        _audit_event(result="success")
        return response

    @_audit_boundary("update")
    async def update_social_relation(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        payload, error = require_object(await request.get_json(silent=True))
        if error:
            return error
        unknown_error = reject_unknown_fields(payload, _UPDATE_FIELDS)
        if unknown_error:
            return unknown_error
        identity, identity_tuple, identity_error = _parse_identity(
            payload.get("identity")
        )
        if identity_error:
            return identity_error
        _set_audit_identity(identity)
        changes, changes_error = require_object(payload.get("changes"))
        if changes_error:
            return changes_error
        unknown_changes = reject_unknown_fields(changes, _EDITABLE_FIELDS)
        if unknown_changes:
            return unknown_changes
        expected_revision = required_text(
            payload.get("expected_revision"),
            field="expected_revision",
            maximum=256,
        )

        normalized_changes: dict[str, Any] = {}
        if "relation_type" in changes:
            normalized_changes["relation_type"] = required_text(
                changes["relation_type"], field="changes.relation_type"
            )
        if "strength" in changes:
            normalized_changes["strength"] = finite_float(
                changes["strength"], field="changes.strength"
            )
        if "tags" in changes:
            normalized_changes["tags"] = normalized_string_list(
                changes["tags"], field="changes.tags"
            )

        manager = self._get_relation_manager()
        if manager is None:
            return _component_unavailable()
        current = await _find_social_relation(manager, identity_tuple)
        rel = await manager.update_manual_relation(
            identity=identity_tuple,
            relation_type=normalized_changes.get(
                "relation_type", getattr(current, "relation_type")
            ),
            strength=normalized_changes.get("strength", getattr(current, "strength")),
            tags=normalized_changes.get(
                "tags", list(getattr(current, "tags", []) or [])
            ),
            expected_revision=expected_revision,
        )
        response = entity_ok(
            _relation_to_dict(rel),
            revision=manager.revision_for(rel),
        )
        _audit_event(result="success")
        return response

    @_audit_boundary("delete")
    async def delete_social_relation(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        payload, error = require_object(await request.get_json(silent=True))
        if error:
            return error
        unknown_error = reject_unknown_fields(payload, _DELETE_FIELDS)
        if unknown_error:
            return unknown_error
        identity, identity_tuple, identity_error = _parse_identity(
            payload.get("identity")
        )
        if identity_error:
            return identity_error
        _set_audit_identity(identity)
        expected_revision = required_text(
            payload.get("expected_revision"),
            field="expected_revision",
            maximum=256,
        )
        manager = self._get_relation_manager()
        if manager is None:
            return _component_unavailable()
        await manager.delete_manual_relation(
            identity=identity_tuple,
            expected_revision=expected_revision,
        )
        response = ok_response({"deleted": True, "identity": identity})
        _audit_event(result="success")
        return response

    @_audit_boundary("batch")
    async def batch_social_relations(self):
        guard = getattr(self, "_maintenance_write_guard", lambda: None)()
        if guard:
            return guard
        payload, error = require_object(await request.get_json(silent=True))
        if error:
            return error
        unknown_error = reject_unknown_fields(payload, _BATCH_FIELDS)
        if unknown_error:
            return unknown_error

        action = required_text(payload.get("action"), field="action")
        if action not in _BATCH_ACTIONS:
            return error_response(
                "不支持的批量操作",
                code="validation_error",
                field_errors={"action": "仅支持 delete、add_tags 或 remove_tags"},
            )
        items = payload.get("items")
        if not isinstance(items, list):
            return error_response(
                "社交关系校验失败",
                code="validation_error",
                field_errors={"items": "必须为数组"},
            )
        if not 1 <= len(items) <= 100:
            return error_response(
                "社交关系校验失败",
                code="validation_error",
                field_errors={"items": "项目数量必须在 1 到 100 之间"},
            )

        params, params_error = require_object(payload.get("params", {}))
        if params_error:
            return params_error
        allowed_params = frozenset() if action == "delete" else frozenset({"tags"})
        unknown_params = reject_unknown_fields(params, allowed_params)
        if unknown_params:
            return unknown_params
        tag_params: list[str] = []
        if action in {"add_tags", "remove_tags"}:
            if "tags" not in params:
                raise EntityValidationError({"params.tags": "不能为空"})
            tag_params = normalized_string_list(params["tags"], field="params.tags")

        manager = self._get_relation_manager()
        if manager is None:
            return _component_unavailable()
        _set_batch_audit_action(action)

        succeeded_ids: list[dict[str, str]] = []
        failures: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            identity_ref: dict[str, Any] = {"item_index": index}
            identity, identity_tuple, revision, item_error = _parse_batch_item(item)
            if identity is not None:
                identity_ref = identity
            if item_error:
                failures.append(_batch_failure(identity_ref, item_error))
                continue
            try:
                if action == "delete":
                    await manager.delete_manual_relation(
                        identity=identity_tuple,
                        expected_revision=revision,
                    )
                else:
                    current = await _find_social_relation(manager, identity_tuple)
                    current_tags = list(getattr(current, "tags", []) or [])
                    if action == "add_tags":
                        next_tags = current_tags + [
                            tag for tag in tag_params if tag not in current_tags
                        ]
                    else:
                        removed = set(tag_params)
                        next_tags = [tag for tag in current_tags if tag not in removed]
                    await manager.update_manual_relation(
                        identity=identity_tuple,
                        relation_type=getattr(current, "relation_type"),
                        strength=getattr(current, "strength"),
                        tags=next_tags,
                        expected_revision=revision,
                    )
                succeeded_ids.append(identity)
            except Exception as exc:
                item_response = _exception_response(exc)
                failures.append(_batch_failure(identity_ref, item_response))

        batch_result = (
            "success"
            if not failures
            else ("failure" if not succeeded_ids else "partial")
        )
        response = ok_response(
            {
                "succeeded_ids": succeeded_ids,
                "succeeded_count": len(succeeded_ids),
                "failed_count": len(failures),
                "failures": failures,
                "total": len(items),
            }
        )
        _audit_event(
            result=batch_result,
            error_code="none" if not failures else "item_failure",
            succeeded_count=len(succeeded_ids),
            failed_count=len(failures),
        )
        return response


__all__ = ["SocialApiMixin"]
