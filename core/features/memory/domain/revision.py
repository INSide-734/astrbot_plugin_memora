"""canonical memory revision 的无状态提取规则。"""

from __future__ import annotations

import json
from typing import Any


def memory_revision(memory: dict[str, Any]) -> str:
    """从 canonical 记录提取稳定 revision token。

    Args:
        memory: 包含时间或 metadata 字段的 canonical 记录。

    Returns:
        首个非空 revision 字符串；记录缺少有效字段时返回空字符串。
    """

    for field in ("updated_at", "created_at", "revision_token"):
        value = memory.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    metadata = memory.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, json.JSONDecodeError):
            metadata = None
    if isinstance(metadata, dict):
        value = metadata.get("updated_at")
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


__all__ = ["memory_revision"]
