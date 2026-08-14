"""记忆演化 feature 的惰性应用服务边界。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .derived_relation_expander import DerivedRelationExpander
    from .memory_consolidator import MemoryConsolidator
    from .memory_evolution_candidates import MemoryEvolutionCandidateGenerator
    from .memory_evolution_gate import MemoryEvolutionGate
    from .memory_evolution_manager import EvolutionLeaseLost, MemoryEvolutionManager
    from .memory_evolution_projection import (
        EvolutionProposalRejected,
        MemoryEvolutionProjectionProposalMixin,
    )
    from .projection_reader import (
        ProjectionBudget,
        ProjectionReader,
        ProjectionReadStats,
        ProjectionScope,
    )
    from .semantic_compressor import SemanticCompressor

__all__ = [
    "DerivedRelationExpander",
    "EvolutionLeaseLost",
    "EvolutionProposalRejected",
    "MemoryConsolidator",
    "MemoryEvolutionCandidateGenerator",
    "MemoryEvolutionGate",
    "MemoryEvolutionManager",
    "MemoryEvolutionProjectionProposalMixin",
    "ProjectionBudget",
    "ProjectionReader",
    "ProjectionReadStats",
    "ProjectionScope",
    "SemanticCompressor",
]

_EXPORT_MODULES = {
    "DerivedRelationExpander": ".derived_relation_expander",
    "EvolutionLeaseLost": ".memory_evolution_manager",
    "EvolutionProposalRejected": ".memory_evolution_projection",
    "MemoryConsolidator": ".memory_consolidator",
    "MemoryEvolutionCandidateGenerator": ".memory_evolution_candidates",
    "MemoryEvolutionGate": ".memory_evolution_gate",
    "MemoryEvolutionManager": ".memory_evolution_manager",
    "MemoryEvolutionProjectionProposalMixin": ".memory_evolution_projection",
    "ProjectionBudget": ".projection_reader",
    "ProjectionReader": ".projection_reader",
    "ProjectionReadStats": ".projection_reader",
    "ProjectionScope": ".projection_reader",
    "SemanticCompressor": ".semantic_compressor",
}


def __getattr__(name: str) -> Any:
    """首次访问公开应用符号时从唯一所有者模块延迟导入。

    参数：
        name: 待解析的应用层公开符号名。

    返回：
        对应所有者模块中的真实符号对象。

    异常：
        AttributeError: 名称不属于公开应用边界。
    """

    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
