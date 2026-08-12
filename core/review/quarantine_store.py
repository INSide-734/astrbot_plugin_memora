"""隔离候选 Store 的旧路径兼容导出。"""

from ..features.quality.infrastructure.quarantine_store import (
    MemoryQuarantineStore,
)

__all__ = ["MemoryQuarantineStore"]
