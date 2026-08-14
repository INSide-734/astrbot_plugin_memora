"""canonical memory、Atom 与写入可靠性的惰性公开 feature 边界。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .domain import (
        AtomStatus,
        AtomType,
        DecayType,
        MemoryAtom,
        PrivacyLevel,
        compute_decay_score,
        compute_ttl,
        memory_revision,
    )
    from .graph import GraphReplaceResult, GraphStore
    from .infrastructure import (
        AtomStore,
        IndexValidator,
        PersistenceHealthValidator,
        SchemaManager,
        WriteOpJournal,
    )

__all__ = [
    "AtomStatus",
    "AtomStore",
    "AtomType",
    "DecayType",
    "GraphReplaceResult",
    "GraphStore",
    "IndexValidator",
    "MemoryAtom",
    "PrivacyLevel",
    "PersistenceHealthValidator",
    "SchemaManager",
    "WriteOpJournal",
    "compute_decay_score",
    "compute_ttl",
    "memory_revision",
]

_EXPORTS = {
    "AtomStatus": (".domain", "AtomStatus"),
    "AtomStore": (".infrastructure", "AtomStore"),
    "AtomType": (".domain", "AtomType"),
    "DecayType": (".domain", "DecayType"),
    "GraphReplaceResult": (".graph", "GraphReplaceResult"),
    "GraphStore": (".graph", "GraphStore"),
    "IndexValidator": (".infrastructure", "IndexValidator"),
    "MemoryAtom": (".domain", "MemoryAtom"),
    "PrivacyLevel": (".domain", "PrivacyLevel"),
    "PersistenceHealthValidator": (".infrastructure", "PersistenceHealthValidator"),
    "SchemaManager": (".infrastructure", "SchemaManager"),
    "WriteOpJournal": (".infrastructure", "WriteOpJournal"),
    "compute_decay_score": (".domain", "compute_decay_score"),
    "compute_ttl": (".domain", "compute_ttl"),
    "memory_revision": (".domain", "memory_revision"),
}


def __getattr__(name: str) -> Any:
    """首次访问公开符号时从其真实 owner 延迟导入。

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
