"""协议身份运行时失败清理的旧路径兼容导出。"""

from ..platform.composition.identity_lifecycle import (
    close_identity_runtime_after_failure,
)

__all__ = ["close_identity_runtime_after_failure"]
