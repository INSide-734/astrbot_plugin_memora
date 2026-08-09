"""持久化健康检查实现的兼容导出。"""

from ..features.memory.infrastructure.validators.persistence_health_validator import (
    PersistenceHealthValidator,
)

__all__ = ["PersistenceHealthValidator"]
