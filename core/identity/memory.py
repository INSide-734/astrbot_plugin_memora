"""身份记忆增强旧路径兼容导出。

真实实现已迁至 ``core.features.identity.application.enricher``；本模块只保留
单实现 re-export，供尚未切换到 feature 路径的历史调用方与契约测试使用。
"""

from ..features.identity.application.enricher import (
    MemoryIdentityContext,
    MemoryIdentityEnricher,
    build_memory_identity_context,
)
from ..shared.contracts import IDENTITY_SCHEMA_VERSION

__all__ = [
    "IDENTITY_SCHEMA_VERSION",
    "MemoryIdentityContext",
    "MemoryIdentityEnricher",
    "build_memory_identity_context",
]
