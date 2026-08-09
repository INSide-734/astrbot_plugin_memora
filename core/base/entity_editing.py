"""共享实体编辑契约的旧路径兼容导出。"""

from ..shared.entity_editing import (
    EditConflictError,
    EntityAlreadyExistsError,
    EntityEditingError,
    EntityNotFoundError,
    EntityValidationError,
    compute_entity_revision,
)

__all__ = [
    "EditConflictError",
    "EntityAlreadyExistsError",
    "EntityEditingError",
    "EntityNotFoundError",
    "EntityValidationError",
    "compute_entity_revision",
]
