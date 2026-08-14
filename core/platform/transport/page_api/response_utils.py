"""统一 API 响应格式"""

from typing import Any


def ok_response(data: Any = None) -> dict[str, Any]:
    return {"status": "ok", "data": data}


def error_response(
    message: str,
    *,
    code: str | None = None,
    field_errors: dict[str, str] | None = None,
    data: Any = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {"status": "error", "message": str(message)}
    if code:
        response["code"] = code
    if field_errors:
        response["field_errors"] = dict(field_errors)
    if data is not None:
        response["data"] = data
    return response
