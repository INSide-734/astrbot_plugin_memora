"""canonical 目标表示的纯计算：metadata 解析、事实对齐与改写判定。

目标表示与 ``StorageBuilder`` 当前写入口径一致：

- 正文是准入事实的连接文本（``"；".join(key_facts)``）；没有准入事实时不改写正文，
  只归一化 metadata。
- ``canonical_summary`` 与正文同步；叙述保留在 ``persona_summary``。
- 改写只有在能证明旧正文不会丢失时才允许：缺少 ``persona_summary`` 时先把旧正文
  写入 ``persona_summary``，已有叙述且与旧正文不一致时判为不可迁移。
- ``key_facts`` 与 ``fact_source_evidence`` 保持原有顺序的一一对应；非空证据列表
  长度不匹配的行不可迁移，既不猜测也不补造证据。

本模块是纯函数：不访问数据库、不产生日志，输入输出都只包含内存中的表示增量。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ....shared.memory_status import is_memory_recallable
from .representation_migration_contracts import (
    REASON_CONTENT_PRESERVATION_UNPROVEN,
    REASON_CONTENT_UNAVAILABLE,
    REASON_EVIDENCE_MAPPING_MISMATCH,
    REASON_METADATA_INVALID,
    REASON_NOT_RECALLABLE,
    TARGET_REPRESENTATION_VERSION,
    WRITE_KIND_CONTENT,
    WRITE_KIND_METADATA,
    WRITE_KIND_NONE,
    RepresentationTarget,
)


def parse_metadata(raw: Any) -> dict[str, Any] | None:
    """解析 canonical metadata；非对象或非法 JSON 返回 ``None``。"""

    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        return dict(decoded) if isinstance(decoded, Mapping) else None
    return None


def compute_representation_target(
    content: Any, metadata_raw: Any
) -> RepresentationTarget:
    """计算单条 canonical 的目标表示；无法证明时返回固定原因码。

    判定顺序：metadata 非法 → 不可召回（含总结来源 orphan）→ 逐事实证据错位 →
    目标正文为空 → 旧正文无法证明保留。任何一步无法证明都返回 ``unavailable``，
    不使用启发式猜测，也不丢弃既有文本。
    """

    metadata = parse_metadata(metadata_raw)
    if metadata is None:
        return _unavailable(REASON_METADATA_INVALID)
    if not is_memory_recallable(metadata):
        return _unavailable(REASON_NOT_RECALLABLE)
    facts = _normalize_facts(metadata.get("key_facts"))
    if not _evidence_aligned(metadata.get("fact_source_evidence"), len(facts)):
        return _unavailable(REASON_EVIDENCE_MAPPING_MISMATCH)
    text = content if isinstance(content, str) else ""
    persona = _first_text(metadata.get("persona_summary"), metadata.get("summary"))
    if facts:
        target_content = "；".join(facts)
    else:
        # 无准入事实时不改写正文，只归一化 metadata。
        target_content = text
    if not target_content.strip():
        return _unavailable(REASON_CONTENT_UNAVAILABLE)
    updates: dict[str, Any] = {}
    content_changed = bool(facts) and text != target_content
    if content_changed:
        if persona and persona != text:
            # 已有叙述与旧正文不一致：改写唯一可检索正文会丢失其中之一，拒绝猜测。
            return _unavailable(REASON_CONTENT_PRESERVATION_UNPROVEN)
        if text.strip() and metadata.get("persona_summary") != text:
            updates["persona_summary"] = text
    elif "persona_summary" not in metadata:
        narrative = persona or text
        if narrative.strip():
            updates["persona_summary"] = narrative
    if metadata.get("canonical_summary") != target_content:
        updates["canonical_summary"] = target_content
    if metadata.get("summary_schema_version") != TARGET_REPRESENTATION_VERSION:
        updates["summary_schema_version"] = TARGET_REPRESENTATION_VERSION
    if content_changed:
        write_kind = WRITE_KIND_CONTENT
    elif updates:
        write_kind = WRITE_KIND_METADATA
    else:
        write_kind = WRITE_KIND_NONE
    return RepresentationTarget(
        write_kind=write_kind,
        content=target_content,
        metadata_updates=updates,
        changed=bool(content_changed or updates),
    )


def _unavailable(reason_code: str) -> RepresentationTarget:
    """构造不可迁移目标；不携带任何正文。"""

    return RepresentationTarget(
        write_kind=WRITE_KIND_NONE,
        content="",
        metadata_updates={},
        changed=False,
        reason_code=reason_code,
    )


def _normalize_facts(value: Any) -> list[str]:
    """规范化 key_facts；非法类型按空列表处理，保留原有顺序。"""

    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _evidence_aligned(value: Any, fact_count: int) -> bool:
    """校验逐事实证据与 key_facts 一一对应；缺失或空列表按未记录处理。"""

    if value is None:
        return True
    if not isinstance(value, list):
        return False
    if not value:
        return True
    return len(value) == fact_count


def _first_text(*values: Any) -> str:
    """返回首个非空文本；非字符串按缺失处理。"""

    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


__all__ = [
    "compute_representation_target",
    "parse_metadata",
]
