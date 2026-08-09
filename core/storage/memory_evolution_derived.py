"""Memory Evolution 派生存储 mixin 的旧路径兼容导出。"""

from ..features.evolution.infrastructure.memory_evolution_derived import (
    MemoryEvolutionDerivedMixin,
)
from ..features.evolution.infrastructure.memory_evolution_derived import (
    _serialized_write as _serialized_write,
)

__all__ = ["MemoryEvolutionDerivedMixin"]
