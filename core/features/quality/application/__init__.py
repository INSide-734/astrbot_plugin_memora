"""记忆质量门的应用编排与候选检测（惰性公开边界）。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .memory_quality_gate import MemoryGateResult, MemoryQualityGate
    from .review_detector import ReviewDetector

__all__ = ["MemoryGateResult", "MemoryQualityGate", "ReviewDetector"]

_EXPORTS = {
    "MemoryGateResult": (".memory_quality_gate", "MemoryGateResult"),
    "MemoryQualityGate": (".memory_quality_gate", "MemoryQualityGate"),
    "ReviewDetector": (".review_detector", "ReviewDetector"),
}


def __getattr__(name: str) -> Any:
    """首次访问公开符号时从其真实 owner 延迟导入，避免导入环与重依赖。"""

    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value
