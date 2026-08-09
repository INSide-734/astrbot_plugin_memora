"""共享注入边界常量的旧路径兼容契约。"""

from core.base import constants as legacy_constants
from core.shared import constants as shared_constants


def test_base_constants_reexport_shared_objects() -> None:
    """旧常量模块必须只重新导出 shared 的唯一实现。"""

    assert legacy_constants.__all__ == shared_constants.__all__
    for name in shared_constants.__all__:
        assert getattr(legacy_constants, name) is getattr(shared_constants, name)
