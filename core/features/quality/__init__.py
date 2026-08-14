"""记忆质量门与人工处置 feature 的惰性公开边界。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .application.memory_quality_gate import (
        MemoryGateResult,
        MemoryQualityGate,
    )
    from .application.review_detector import ReviewDetector
    from .domain.models import (
        ReviewAction,
        ReviewActionResult,
        ReviewItem,
        ReviewReason,
        ReviewSeverity,
        ReviewStatus,
    )
    from .infrastructure.quarantine_store import MemoryQuarantineStore
    from .infrastructure.review_store import ReviewStore

__all__ = [
    "MemoryGateResult",
    "MemoryQualityGate",
    "MemoryQuarantineStore",
    "ReviewAction",
    "ReviewActionResult",
    "ReviewDetector",
    "ReviewItem",
    "ReviewReason",
    "ReviewSeverity",
    "ReviewStatus",
    "ReviewStore",
]

_EXPORTS = {
    "MemoryGateResult": (
        ".application.memory_quality_gate",
        "MemoryGateResult",
    ),
    "MemoryQualityGate": (
        ".application.memory_quality_gate",
        "MemoryQualityGate",
    ),
    "ReviewDetector": (".application.review_detector", "ReviewDetector"),
    "ReviewAction": (".domain.models", "ReviewAction"),
    "ReviewActionResult": (".domain.models", "ReviewActionResult"),
    "ReviewItem": (".domain.models", "ReviewItem"),
    "ReviewReason": (".domain.models", "ReviewReason"),
    "ReviewSeverity": (".domain.models", "ReviewSeverity"),
    "ReviewStatus": (".domain.models", "ReviewStatus"),
    "MemoryQuarantineStore": (
        ".infrastructure.quarantine_store",
        "MemoryQuarantineStore",
    ),
    "ReviewStore": (".infrastructure.review_store", "ReviewStore"),
}


def __getattr__(name: str) -> Any:
    """首次访问公开符号时从其真实 owner 延迟导入，避免拖入 FAISS 等重依赖。

    参数：
        name: 待解析的包级公开符号名。

    返回：
        真实 owner 模块中的符号对象。

    异常：
        AttributeError: 名称不属于公开 feature 边界。
    """

    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value
