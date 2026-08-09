"""共享实体编辑契约的旧路径兼容测试。"""

from core.base import entity_editing as legacy_entity_editing
from core.shared import entity_editing as shared_entity_editing


def test_base_entity_editing_reexports_shared_objects() -> None:
    """旧实体编辑模块必须只重新导出 shared 的唯一实现。"""

    assert legacy_entity_editing.__all__ == shared_entity_editing.__all__
    for name in shared_entity_editing.__all__:
        assert getattr(legacy_entity_editing, name) is getattr(
            shared_entity_editing,
            name,
        )
