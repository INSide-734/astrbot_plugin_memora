"""共享异常契约的旧路径兼容测试。"""

from core.base import exceptions as legacy_exceptions
from core.shared import errors as shared_errors


def test_base_exception_path_reuses_shared_error_objects() -> None:
    """旧异常模块只能重新导出 shared errors 的唯一实现。"""

    assert legacy_exceptions.__all__ == shared_errors.__all__
    for name in shared_errors.__all__:
        assert getattr(legacy_exceptions, name) is getattr(shared_errors, name)
