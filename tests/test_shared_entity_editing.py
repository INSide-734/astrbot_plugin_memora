"""共享实体编辑契约测试。"""

from core.shared import entity_editing as shared_entity_editing


def test_shared_entity_editing_exports_public_contract() -> None:
    """shared 模块必须稳定导出完整实体编辑契约。"""

    assert shared_entity_editing.__all__ == [
        "EditConflictError",
        "EntityAlreadyExistsError",
        "EntityEditingError",
        "EntityNotFoundError",
        "EntityValidationError",
        "compute_entity_revision",
    ]
