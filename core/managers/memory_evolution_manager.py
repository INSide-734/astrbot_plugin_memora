"""Memory Evolution Manager 的旧路径兼容导出。"""

from ..features.evolution.application import memory_evolution_manager as _implementation
from ..features.evolution.application.memory_evolution_manager import (
    EvolutionLeaseLost,
    EvolutionProposalRejected,
    MemoryEvolutionManager,
)

random = _implementation.random

__all__ = [
    "EvolutionLeaseLost",
    "EvolutionProposalRejected",
    "MemoryEvolutionManager",
]
