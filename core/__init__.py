"""
Memora 核心模块
提供统一的记忆管理引擎

目录结构:
- base/: 基础组件（异常、配置、常量）
- models/: 数据模型
- managers/: 管理器（会话管理、记忆引擎）
- processors/: 处理器（记忆处理、文本处理）
- validators/: 验证器（索引验证）
- retrieval/: 检索系统
- utils/: 工具函数

所有公共导出均为懒加载 — 仅在被实际访问时才导入对应子模块。
"""

from typing import Any

__all__ = [
    # 基础组件
    "ConfigManager",
    "ConfigurationError",
    "DatabaseError",
    "InitializationError",
    "MemoraException",
    "MemoryProcessingError",
    "ProviderNotReadyError",
    "RetrievalError",
    "ValidationError",
    # 数据模型
    "MemoryEvent",
    "Message",
    "Session",
    "GraphNode",
    "GraphEdge",
    "GraphEntry",
    "ExtractedGraph",
    # 管理器
    "ConversationManager",
    "GraphMemoryManager",
    "MemoryEngine",
    # 处理器
    "ChatroomContextParser",
    "EntityResolver",
    "GraphExtractor",
    "MemoryProcessor",
    "TextProcessor",
    "store_round_with_length_check",
    # 验证器
    "IndexValidator",
]

_lazy: dict[str, Any] = {}


def __getattr__(name: str) -> Any:
    """Lazy-import heavy subpackages on first access."""
    global _lazy

    if name in _lazy:
        return _lazy[name]

    # ── base ──
    if name in (
        "ConfigManager",
        "ConfigurationError",
        "DatabaseError",
        "InitializationError",
        "MemoraException",
        "MemoryProcessingError",
        "ProviderNotReadyError",
        "RetrievalError",
        "ValidationError",
    ):
        from .base import (  # type: ignore[no-redef]  # noqa: F811
            ConfigManager,
            ConfigurationError,
            DatabaseError,
            InitializationError,
            MemoraException,
            MemoryProcessingError,
            ProviderNotReadyError,
            RetrievalError,
            ValidationError,
        )
        _lazy.update({k: v for k, v in locals().items() if k in __all__})
        return _lazy[name]

    # ── models ──
    if name in ("MemoryEvent", "Message", "Session", "GraphNode", "GraphEdge", "GraphEntry", "ExtractedGraph"):
        from .models import (  # type: ignore[no-redef]  # noqa: F811
            ExtractedGraph,
            GraphEdge,
            GraphEntry,
            GraphNode,
            MemoryEvent,
            Message,
            Session,
        )
        _lazy.update({k: v for k, v in locals().items() if k in __all__})
        return _lazy[name]

    # ── managers ──
    if name in ("ConversationManager", "GraphMemoryManager", "MemoryEngine"):
        from .managers import ConversationManager, GraphMemoryManager, MemoryEngine  # type: ignore[no-redef]  # noqa: F811
        _lazy.update({k: v for k, v in locals().items() if k in __all__})
        return _lazy[name]

    # ── processors ──
    if name in (
        "ChatroomContextParser",
        "EntityResolver",
        "GraphExtractor",
        "MemoryProcessor",
        "TextProcessor",
        "store_round_with_length_check",
    ):
        from .processors import (  # type: ignore[no-redef]  # noqa: F811
            ChatroomContextParser,
            EntityResolver,
            GraphExtractor,
            MemoryProcessor,
            TextProcessor,
            store_round_with_length_check,
        )
        _lazy.update({k: v for k, v in locals().items() if k in __all__})
        return _lazy[name]

    # ── validators ──
    if name == "IndexValidator":
        from .validators import IndexValidator  # type: ignore[no-redef]  # noqa: F811
        _lazy["IndexValidator"] = IndexValidator
        return IndexValidator

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
