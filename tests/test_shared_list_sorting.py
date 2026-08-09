"""共享列表排序原语的旧路径兼容契约。"""

from core.base import list_sorting as legacy_list_sorting
from core.shared import list_sorting as shared_list_sorting


def test_base_list_sorting_reexports_shared_objects() -> None:
    """旧排序模块必须只重新导出 shared 的唯一实现。"""

    assert legacy_list_sorting.__all__ == shared_list_sorting.__all__
    for name in shared_list_sorting.__all__:
        assert getattr(legacy_list_sorting, name) is getattr(
            shared_list_sorting,
            name,
        )
