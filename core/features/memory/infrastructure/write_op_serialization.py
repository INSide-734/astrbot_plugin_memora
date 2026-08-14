"""
写操作序列化辅助函数
提供 JSON 安全转换和 MemoryAtom 的序列化/反序列化
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..domain.memory_atom import AtomStatus, AtomType, DecayType, MemoryAtom


def safe_json_dict(value: Any) -> dict[str, Any]:
    """安全地将值转换为 dict，用于解析 JSON 元数据字段"""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def serialize_atom_for_repair(atom: Any) -> dict[str, Any]:
    """将类 MemoryAtom 对象转换为 JSON 安全的修复载荷。"""
    atom_type = getattr(atom, "atom_type", AtomType.UNKNOWN)
    decay_type = getattr(atom, "decay_type", DecayType.EXPONENTIAL)
    status = getattr(atom, "status", AtomStatus.ACTIVE)
    return {
        "parent_memory_id": int(getattr(atom, "parent_memory_id", 0) or 0),
        "parent_revision": getattr(atom, "parent_revision", None),
        "parent_scope_key": getattr(atom, "parent_scope_key", None),
        "parent_privacy_level": getattr(atom, "parent_privacy_level", None),
        "atom_type": getattr(atom_type, "value", str(atom_type)),
        "content": str(getattr(atom, "content", "")),
        "entities": list(getattr(atom, "entities", []) or []),
        "importance": float(getattr(atom, "importance", 0.5) or 0.5),
        "confidence": float(getattr(atom, "confidence", 0.7) or 0.7),
        "created_at": float(getattr(atom, "created_at", time.time()) or time.time()),
        "last_accessed_at": float(
            getattr(atom, "last_accessed_at", time.time()) or time.time()
        ),
        "last_reinforced_at": getattr(atom, "last_reinforced_at", None),
        "event_time": getattr(atom, "event_time", None),
        "ttl_days": float(getattr(atom, "ttl_days", 30.0) or 30.0),
        "expires_at": float(getattr(atom, "expires_at", 0.0) or 0.0),
        "status": getattr(status, "value", str(status)),
        "reinforcement_count": int(getattr(atom, "reinforcement_count", 0) or 0),
        "decay_type": getattr(decay_type, "value", str(decay_type)),
        "session_id": getattr(atom, "session_id", None),
        "persona_id": getattr(atom, "persona_id", None),
        "metadata": dict(getattr(atom, "metadata", {}) or {}),
    }


def _deserialize_atom_from_repair(
    payload: dict[str, Any],
    parent_memory_id: int,
    session_id: str | None,
    persona_id: str | None,
) -> MemoryAtom | None:
    """从修复载荷重建 MemoryAtom。"""
    content = str(payload.get("content") or "")
    if not content.strip():
        return None

    try:
        atom_type = AtomType(payload.get("atom_type") or AtomType.UNKNOWN.value)
    except ValueError:
        atom_type = AtomType.UNKNOWN
    try:
        decay_type = DecayType(payload.get("decay_type") or DecayType.EXPONENTIAL.value)
    except ValueError:
        decay_type = DecayType.EXPONENTIAL
    try:
        status = AtomStatus(payload.get("status") or AtomStatus.ACTIVE.value)
    except ValueError:
        status = AtomStatus.ACTIVE

    return MemoryAtom(
        parent_memory_id=parent_memory_id,
        parent_revision=payload.get("parent_revision"),
        parent_scope_key=payload.get("parent_scope_key"),
        parent_privacy_level=payload.get("parent_privacy_level"),
        atom_type=atom_type,
        content=content,
        entities=[str(item) for item in payload.get("entities", []) if item],
        importance=float(payload.get("importance", 0.5) or 0.5),
        confidence=float(payload.get("confidence", 0.7) or 0.7),
        created_at=float(payload.get("created_at", time.time()) or time.time()),
        last_accessed_at=float(
            payload.get("last_accessed_at", time.time()) or time.time()
        ),
        last_reinforced_at=payload.get("last_reinforced_at"),
        event_time=payload.get("event_time"),
        ttl_days=float(payload.get("ttl_days", 30.0) or 30.0),
        expires_at=float(payload.get("expires_at", 0.0) or 0.0),
        status=status,
        reinforcement_count=int(payload.get("reinforcement_count", 0) or 0),
        decay_type=decay_type,
        session_id=payload.get("session_id") or session_id,
        persona_id=payload.get("persona_id") or persona_id,
        metadata=dict(payload.get("metadata") or {}),
    )
