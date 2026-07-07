"""统一 API 响应格式"""

from typing import Any


def ok_response(data: Any = None) -> dict[str, Any]:
    return {"status": "ok", "data": data}


def error_response(message: str) -> dict[str, Any]:
    return {"status": "error", "message": str(message)}
