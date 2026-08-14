"""从持久化元数据中读取数值的小型助手函数。"""

from __future__ import annotations

import math
from typing import Any


def safe_float(value: Any, default: float = 0.0) -> float:
    """返回有限浮点数，或为旧版/非数字元数据返回默认值。"""
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def clamp_float(
    value: Any,
    *,
    default: float = 0.0,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    """读取浮点数并将其限制在指定范围内。"""
    parsed = safe_float(value, default)
    return max(minimum, min(maximum, parsed))
