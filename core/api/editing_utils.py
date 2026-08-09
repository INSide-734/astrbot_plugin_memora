"""领域无关的实体编辑请求校验与响应辅助函数。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..shared.entity_editing import EntityValidationError, compute_entity_revision
from .response_utils import error_response, ok_response


def require_object(payload: Any) -> tuple[dict[str, Any] | None, dict | None]:
    """要求请求体为 JSON 对象并返回其普通字典副本。"""

    if not isinstance(payload, Mapping):
        return None, error_response("请求体必须是 JSON 对象", code="invalid_request")
    return dict(payload), None


def reject_unknown_fields(
    payload: Mapping[str, Any],
    allowed: frozenset[str],
) -> dict | None:
    """拒绝负载中不在可写字段集合内的字段。"""

    unknown = sorted(set(payload) - allowed)
    if not unknown:
        return None
    return error_response(
        "请求包含不支持的字段",
        code="validation_error",
        field_errors={name: "字段不可写" for name in unknown},
    )


def finite_float(value: Any, *, field: str) -> float:
    """将值规范化为有限浮点数，同时拒绝 JSON 布尔值。"""

    if isinstance(value, bool):
        raise EntityValidationError({field: "必须为数字"})
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise EntityValidationError({field: "必须为数字"}) from exc
    if not math.isfinite(parsed):
        raise EntityValidationError({field: "必须为有限数字"})
    return parsed


def required_text(
    value: Any,
    *,
    field: str,
    maximum: int = 128,
) -> str:
    """规范化必填文本并限制最大长度。"""

    if not isinstance(value, str):
        raise EntityValidationError({field: "必须为字符串"})
    normalized = value.strip()
    if not normalized:
        raise EntityValidationError({field: "不能为空"})
    if len(normalized) > maximum:
        raise EntityValidationError({field: "文本过长"})
    return normalized


def bounded_int(
    value: Any,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    """要求值为指定闭区间内的整数，同时拒绝 JSON 布尔值。"""

    if isinstance(value, bool) or not isinstance(value, int):
        raise EntityValidationError({field: "必须为整数"})
    if value < minimum or value > maximum:
        raise EntityValidationError(
            {field: "必须在 " + str(minimum) + " 到 " + str(maximum) + " 之间"}
        )
    return value


def normalized_string_list(
    value: Any,
    *,
    field: str,
    maximum_items: int = 32,
    maximum_length: int = 64,
) -> list[str]:
    """规范化字符串数组，去除空项并按首次出现顺序去重。"""

    if not isinstance(value, list):
        raise EntityValidationError({field: "必须为字符串数组"})
    normalized: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise EntityValidationError({field + "." + str(index): "必须为字符串"})
        text = item.strip()
        if not text:
            continue
        if len(text) > maximum_length:
            raise EntityValidationError({field + "." + str(index): "文本过长"})
        if text not in normalized:
            normalized.append(text)
    if len(normalized) > maximum_items:
        raise EntityValidationError({field: "项目过多"})
    return normalized


def entity_ok(
    entity: Mapping[str, Any],
    *,
    revision: str | None = None,
) -> dict[str, Any]:
    """返回实体与其当前不透明修订版本。"""

    serial = dict(entity)
    return ok_response(
        {
            "entity": serial,
            "revision": revision or compute_entity_revision(serial),
        }
    )


def conflict_error(
    entity: Mapping[str, Any],
    *,
    current_revision: str,
) -> dict[str, Any]:
    """返回包含后台当前实体快照的编辑冲突响应。"""

    serial = dict(entity)
    return error_response(
        "记录已被后台更新，请检查最新数据",
        code="edit_conflict",
        data={
            "current_entity": serial,
            "current_revision": current_revision,
        },
    )
