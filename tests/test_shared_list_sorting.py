"""共享列表排序原语契约。"""

from core.shared import list_sorting as shared_list_sorting


def test_shared_list_sorting_exports_public_contract() -> None:
    """shared 模块必须稳定导出完整列表排序契约。"""

    assert shared_list_sorting.__all__ == [
        "SortOrder",
        "SortQuery",
        "order_by_clause",
        "parse_sort_query",
    ]
