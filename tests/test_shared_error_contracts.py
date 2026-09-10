"""共享异常 owner 与根门面契约测试。"""

import core
from core.shared import errors as shared_errors


def test_core_error_facade_reuses_shared_error_objects() -> None:
    """core 根门面应恒等导出 shared errors 的稳定异常集合。"""

    exported_names = (
        "ConfigurationError",
        "DatabaseError",
        "InitializationError",
        "MemoraException",
        "MemoryProcessingError",
        "ProviderNotReadyError",
        "RetrievalError",
        "ValidationError",
    )
    for name in exported_names:
        assert getattr(core, name) is getattr(shared_errors, name)
