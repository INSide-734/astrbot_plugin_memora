"""实体编辑契约与领域无关 API 辅助函数测试。"""

from __future__ import annotations

import pytest

from core.api.editing_utils import (
    bounded_int,
    conflict_error,
    entity_ok,
    finite_float,
    normalized_string_list,
    reject_unknown_fields,
    require_object,
    required_text,
)
from core.base.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityEditingError,
    EntityNotFoundError,
    EntityValidationError,
    compute_entity_revision,
)


def test_revision_is_canonical_for_mapping_order() -> None:
    left = {"name": "Alice", "tags": ["one", "two"], "score": 0.5}
    right = {"score": 0.5, "tags": ["one", "two"], "name": "Alice"}
    assert compute_entity_revision(left) == compute_entity_revision(right)


def test_revision_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError):
        compute_entity_revision({"score": float("nan")})


def test_entity_editing_exceptions_share_domain_base() -> None:
    assert issubclass(EntityAlreadyExistsError, EntityEditingError)
    assert issubclass(EntityNotFoundError, EntityEditingError)
    assert issubclass(EntityValidationError, EntityEditingError)
    assert issubclass(EditConflictError, EntityEditingError)


def test_validation_error_keeps_field_errors() -> None:
    exc = EntityValidationError({"name": "不能为空"})
    assert exc.field_errors == {"name": "不能为空"}
    assert str(exc) == "实体校验失败"


def test_conflict_keeps_current_entity_and_revision() -> None:
    exc = EditConflictError({"user_id": "u1"}, "rev-current")
    assert exc.current_entity == {"user_id": "u1"}
    assert exc.current_revision == "rev-current"


def test_require_object_accepts_mapping_and_rejects_other_values() -> None:
    payload, error = require_object({"user_id": "u1"})
    assert payload == {"user_id": "u1"}
    assert error is None

    payload, error = require_object(["not", "an", "object"])
    assert payload is None
    assert error == {
        "status": "error",
        "message": "请求体必须是 JSON 对象",
        "code": "invalid_request",
    }


def test_reject_unknown_fields_reports_each_disallowed_field() -> None:
    assert reject_unknown_fields({"name": "Alice"}, frozenset({"name"})) is None
    assert reject_unknown_fields(
        {"name": "Alice", "counter": 1, "timestamp": 2},
        frozenset({"name"}),
    ) == {
        "status": "error",
        "message": "请求包含不支持的字段",
        "code": "validation_error",
        "field_errors": {"counter": "字段不可写", "timestamp": "字段不可写"},
    }


def test_finite_float_normalizes_numbers_and_rejects_non_finite_values() -> None:
    assert finite_float("0.5", field="score") == 0.5
    with pytest.raises(EntityValidationError) as boolean_error:
        finite_float(True, field="score")
    assert boolean_error.value.field_errors == {"score": "必须为数字"}
    with pytest.raises(EntityValidationError) as infinite_error:
        finite_float(float("inf"), field="score")
    assert infinite_error.value.field_errors == {"score": "必须为有限数字"}


def test_required_text_trims_and_enforces_length() -> None:
    assert required_text(" Alice ", field="name") == "Alice"
    with pytest.raises(EntityValidationError) as empty_error:
        required_text("   ", field="name")
    assert empty_error.value.field_errors == {"name": "不能为空"}
    with pytest.raises(EntityValidationError) as length_error:
        required_text("abcd", field="name", maximum=3)
    assert length_error.value.field_errors == {"name": "文本过长"}


def test_bounded_int_rejects_booleans_non_integers_and_out_of_range() -> None:
    assert bounded_int(10, field="score", minimum=-100, maximum=100) == 10
    with pytest.raises(EntityValidationError):
        bounded_int(True, field="score", minimum=-100, maximum=100)
    with pytest.raises(EntityValidationError):
        bounded_int(1.5, field="score", minimum=-100, maximum=100)
    with pytest.raises(EntityValidationError) as range_error:
        bounded_int(101, field="score", minimum=-100, maximum=100)
    assert range_error.value.field_errors == {"score": "必须在 -100 到 100 之间"}


def test_normalized_string_list_trims_deduplicates_and_validates_items() -> None:
    assert normalized_string_list(
        [" one ", "", "one", "two"], field="tags"
    ) == ["one", "two"]
    with pytest.raises(EntityValidationError):
        normalized_string_list("not-a-list", field="tags")
    with pytest.raises(EntityValidationError) as item_error:
        normalized_string_list(["one", True], field="tags")
    assert item_error.value.field_errors == {"tags.1": "必须为字符串"}
    with pytest.raises(EntityValidationError) as count_error:
        normalized_string_list(["one", "two"], field="tags", maximum_items=1)
    assert count_error.value.field_errors == {"tags": "项目过多"}


def test_entity_ok_wraps_entity_with_revision() -> None:
    entity = {"user_id": "u1", "score": 10}
    result = entity_ok(entity, revision="rev-1")
    assert result == {
        "status": "ok",
        "data": {"entity": entity, "revision": "rev-1"},
    }
    assert entity_ok(entity)["data"]["revision"] == compute_entity_revision(entity)


def test_conflict_error_returns_current_entity_and_revision() -> None:
    entity = {"user_id": "u1", "score": 10}
    assert conflict_error(entity, current_revision="rev-current") == {
        "status": "error",
        "message": "记录已被后台更新，请检查最新数据",
        "code": "edit_conflict",
        "data": {
            "current_entity": entity,
            "current_revision": "rev-current",
        },
    }
