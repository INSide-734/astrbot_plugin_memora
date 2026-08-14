"""按门禁处置过滤召回结果。"""

from __future__ import annotations

from typing import Any

from ...retrieval.rrf_fusion import HybridResult


def is_mark_write(metadata: dict[str, Any]) -> bool:
    """判断 metadata 是否携带 mark_write 低置信标记。"""

    return str(metadata.get("gate_disposition") or "") == "mark_write"


def filter_mark_write(
    results: list[HybridResult], *, include_mark_write: bool = False
) -> list[HybridResult]:
    """默认排除 mark_write 记忆；include_mark_write=True 时原样返回。"""

    if include_mark_write:
        return results
    return [result for result in results if not is_mark_write(result.metadata)]


__all__ = ["filter_mark_write", "is_mark_write"]
