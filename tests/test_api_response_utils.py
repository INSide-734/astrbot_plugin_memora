"""core/api/response_utils.py — 纯辅助函数测试。"""

from __future__ import annotations

import pytest

from core.api.response_utils import error_response, ok_response


class TestOkResponse:
    """ok_response(data) returns {"status": "ok", "data": data}."""

    def test_ok_with_dict_data(self) -> None:
        result = ok_response({"items": [1, 2, 3]})
        assert result == {"status": "ok", "data": {"items": [1, 2, 3]}}

    def test_ok_with_none_data(self) -> None:
        result = ok_response(None)
        assert result == {"status": "ok", "data": None}

    def test_ok_with_list_data(self) -> None:
        result = ok_response([1, 2, 3])
        assert result["status"] == "ok"
        assert result["data"] == [1, 2, 3]

    def test_ok_with_string_data(self) -> None:
        result = ok_response("test")
        assert result == {"status": "ok", "data": "test"}

    def test_ok_without_argument(self) -> None:
        result = ok_response()
        assert result == {"status": "ok", "data": None}

    def test_ok_preserves_data_types(self) -> None:
        result = ok_response({"count": 0, "flag": False})
        assert result["data"]["count"] == 0
        assert result["data"]["flag"] is False


class TestErrorResponse:
    """error_response(message) returns {"status": "error", "message": str(message)}."""

    def test_error_with_string_message(self) -> None:
        result = error_response("something went wrong")
        assert result == {"status": "error", "message": "something went wrong"}

    def test_error_with_empty_message(self) -> None:
        result = error_response("")
        assert result == {"status": "error", "message": ""}

    def test_error_converts_non_string_to_string(self) -> None:
        result = error_response(ValueError("bad value"))
        assert result["status"] == "error"
        assert "bad value" in result["message"]

    def test_error_with_none_message(self) -> None:
        result = error_response(None)
        assert result == {"status": "error", "message": "None"}

    def test_error_structure_keys_only(self) -> None:
        result = error_response("test")
        assert set(result.keys()) == {"status", "message"}


class TestResponseRemainsImmutable:
    """ok_response / error_response must not share mutable state across calls."""

    def test_ok_responses_are_independent(self) -> None:
        a = ok_response({"a": 1})
        b = ok_response({"b": 2})
        assert a["data"]["a"] == 1
        assert b["data"]["b"] == 2

    def test_error_responses_are_independent(self) -> None:
        a = error_response("a")
        b = error_response("b")
        assert a["message"] == "a"
        assert b["message"] == "b"
