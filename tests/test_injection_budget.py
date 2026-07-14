"""记忆注入预算与紧凑格式测试。"""

from core.base.constants import MEMORY_INJECTION_FOOTER, MEMORY_INJECTION_HEADER
from core.utils.injection_budget import format_compact_footer, format_compact_header


def test_compact_format_preserves_cleanup_boundaries() -> None:
    """紧凑格式仍须保留 InjectionCleaner 依赖的稳定边界。"""
    assert format_compact_header().startswith(MEMORY_INJECTION_HEADER)
    assert format_compact_footer().endswith(MEMORY_INJECTION_FOOTER)
