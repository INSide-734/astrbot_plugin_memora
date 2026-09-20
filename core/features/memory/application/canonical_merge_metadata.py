"""canonical merge 的纯 metadata 规范化、并集与幂等键助手。

从 `canonical_merge.py` 按已记录的拆分点抽出：这些函数只做字典/JSON 值的
规范化与并集去重，不接触 Store、锁或指标端口，因此可以独立测试与被迁移/
重建路径复用。`merge_fact_evidence` 是唯一带业务护栏的助手：只有两侧
``key_facts`` 与 ``fact_source_evidence`` 一一对应、逐条具备用户来源证据且
候选事实是 owner 事实子集时才返回并集，否则返回 ``None`` 表示不可合并。
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from typing import Any, Final

from ..domain.memory_atom import has_user_source_evidence

MAX_SOURCE_EVIDENCE: Final = 32
MAX_MERGED_IDEMPOTENCY_KEYS: Final = 16


def load_metadata(value: Any) -> dict[str, Any] | None:
    """解析落库 metadata；非法 JSON 或非对象返回 ``None``。"""

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return None
    if not isinstance(value, dict):
        return None
    return value


def normalize_metadata(value: Any) -> dict[str, Any]:
    """把 canonical metadata 规范化为字典；缺失或非法时返回空字典。"""

    return load_metadata(value) or {}


def importance(value: Any) -> float:
    """把重要性规范化为 0..1 浮点；非法值按 0 处理（max 只升不降）。"""

    if isinstance(value, bool):
        return 0.0
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def merge_count(metadata: Mapping[str, Any]) -> int:
    """读取既有合并计数；非法值按 0 处理。"""

    value = metadata.get("merge_count")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def items(value: Any) -> tuple[Any, ...]:
    """把并集输入规范化为元组；非法类型按空处理。"""

    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()


def identity(item: Any) -> str:
    """构造稳定去重键；不可 JSON 序列化时回退 repr。"""

    try:
        return json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(item)


def union(existing: Any, incoming: Any, *, limit: int) -> list[Any]:
    """按“既有在前、新增在后”合并去重并截断到上限。"""

    merged: list[Any] = []
    seen: set[str] = set()
    for item in (*items(existing), *items(incoming)):
        key = identity(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[:limit]


def merge_fact_evidence(
    existing: Mapping[str, Any], incoming: Mapping[str, Any]
) -> list[list[dict[str, Any]]] | None:
    """Union evidence only for the same normalized fact, preserving owner order."""
    paired: list[tuple[list[str], list[list[dict[str, Any]]]]] = []
    for metadata in (existing, incoming):
        facts = metadata.get("key_facts")
        evidence = metadata.get("fact_source_evidence")
        if (
            not isinstance(facts, list)
            or not facts
            or any(not isinstance(fact, str) or not fact.strip() for fact in facts)
            or not isinstance(evidence, list)
            or len(facts) != len(evidence)
            or not all(has_user_source_evidence(group) for group in evidence)
        ):
            return None
        paired.append((facts, evidence))
    owner_facts, owner_evidence = paired[0]
    additions: dict[str, list[dict[str, Any]]] = {}
    for fact, group in zip(*paired[1], strict=True):
        additions.setdefault(fact_key(fact), []).extend(group)
    if not additions.keys() <= {fact_key(fact) for fact in owner_facts}:
        return None
    merged: list[list[dict[str, Any]]] = []
    for fact, group in zip(owner_facts, owner_evidence, strict=True):
        seen: set[tuple[Any, ...]] = set()
        refs: list[dict[str, Any]] = []
        for item in (*group, *additions.get(fact_key(fact), ())):
            key = tuple(
                item[field]
                for field in (
                    "message_id",
                    "message_seq",
                    "role",
                    "start",
                    "end",
                    "message_fingerprint",
                )
            )
            if key not in seen:
                seen.add(key)
                refs.append(dict(item))
        retained = refs[:MAX_SOURCE_EVIDENCE]
        if not has_user_source_evidence(retained):
            return None
        merged.append(retained)
    return merged


def fact_key(fact: str) -> str:
    """规范化事实比较键（NFKC + casefold + 空白折叠）。"""

    return " ".join(unicodedata.normalize("NFKC", fact).casefold().split())


def merged_keys(metadata: Mapping[str, Any]) -> list[str]:
    """读取 canonical metadata 中已合并的幂等键，忽略非法项。"""

    return [
        item
        for item in items(metadata.get("merged_idempotency_keys"))
        if isinstance(item, str) and item.strip()
    ]


def merge_keys(metadata: Mapping[str, Any], key: str) -> list[str]:
    """追加候选幂等键并保留最近 ``MAX_MERGED_IDEMPOTENCY_KEYS`` 项。"""

    keys = merged_keys(metadata)
    if key and key not in keys:
        keys.append(key)
    return keys[-MAX_MERGED_IDEMPOTENCY_KEYS:]


__all__ = [
    "MAX_MERGED_IDEMPOTENCY_KEYS",
    "MAX_SOURCE_EVIDENCE",
    "fact_key",
    "identity",
    "importance",
    "items",
    "load_metadata",
    "merge_count",
    "merge_fact_evidence",
    "merge_keys",
    "merged_keys",
    "normalize_metadata",
    "union",
]
