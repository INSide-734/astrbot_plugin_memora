"""canonical memory revision 的无状态提取规则。"""

from __future__ import annotations

import json
from collections.abc import Mapping
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


def revision_snapshot(record: dict[str, Any]) -> str:
    """提取记录携带的 revision 快照，供与候选/派生对象保存的快照比对。

    先按 ``memory_revision`` 取记录自身的权威 revision（优先 SQLite 原始时间
    字段），再回退到行内明确记录的 ``metadata.revision_token``；两者都没有时
    返回空字符串，表示无法做同源比较，调用方不得据此判定失效。
    """

    revision = memory_revision(record)
    if revision:
        return revision
    metadata = record.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, json.JSONDecodeError):
            metadata = None
    if isinstance(metadata, dict):
        token = metadata.get("revision_token")
        if isinstance(token, str) and token.strip():
            return token.strip()
    return ""


def revision_is_stale(
    cached_metadata: Mapping[str, Any] | None,
    current_metadata: Mapping[str, Any] | None,
    current_revision: str,
    *,
    require_snapshot: bool = False,
) -> bool:
    """判断缓存/派生快照记录的 revision 是否已不是 canonical 当前 revision。

    条目自带 ``revision_token`` 时与 ``current_revision`` 做同源字符串比较；
    没有 token 时按两侧同一 metadata 字段 ``updated_at`` 比较（同一表示，不做
    日期猜测）。``require_snapshot=True`` 供缓存条目使用（条目按契约必须自带
    快照）：完全没有可比快照而 canonical 行已有快照时同样按失效处理；实时候选
    缺快照是合法形状（metadata 与 canonical 行同源），不据此剔除。
    """

    if not isinstance(cached_metadata, Mapping):
        return False
    token = cached_metadata.get("revision_token")
    if isinstance(token, str) and token.strip():
        return bool(current_revision) and token.strip() != current_revision
    cached_updated_at = cached_metadata.get("updated_at")
    current_updated_at = (
        current_metadata.get("updated_at")
        if isinstance(current_metadata, Mapping)
        else None
    )
    if cached_updated_at is not None and current_updated_at is not None:
        return str(cached_updated_at) != str(current_updated_at)
    if not require_snapshot:
        return False
    # 缓存条目没有可比快照，而 canonical 行现在有：不能证明仍是当前 revision。
    return bool(current_revision) or current_updated_at is not None


__all__ = ["memory_revision", "revision_is_stale", "revision_snapshot"]
