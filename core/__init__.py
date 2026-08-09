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
    """首次访问公开符号时延迟导入对应子模块。"""
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
        from .base import (
            ConfigManager as ConfigManager,
        )
        from .base import (
            ConfigurationError as ConfigurationError,
        )
        from .base import (
            DatabaseError as DatabaseError,
        )
        from .base import (
            InitializationError as InitializationError,
        )
        from .base import (
            MemoraException as MemoraException,
        )
        from .base import (
            MemoryProcessingError as MemoryProcessingError,
        )
        from .base import (
            ProviderNotReadyError as ProviderNotReadyError,
        )
        from .base import (
            RetrievalError as RetrievalError,
        )
        from .base import (
            ValidationError as ValidationError,
        )

        _lazy.update({k: v for k, v in locals().items() if k in __all__})
        return _lazy[name]

    # ── models ──
    if name in (
        "MemoryEvent",
        "Message",
        "Session",
        "GraphNode",
        "GraphEdge",
        "GraphEntry",
        "ExtractedGraph",
    ):
        from .models import (
            ExtractedGraph as ExtractedGraph,
        )
        from .models import (
            GraphEdge as GraphEdge,
        )
        from .models import (
            GraphEntry as GraphEntry,
        )
        from .models import (
            GraphNode as GraphNode,
        )
        from .models import (
            MemoryEvent as MemoryEvent,
        )
        from .models import (
            Message as Message,
        )
        from .models import (
            Session as Session,
        )

        _lazy.update({k: v for k, v in locals().items() if k in __all__})
        return _lazy[name]

    # ── managers ──
    if name in ("ConversationManager", "GraphMemoryManager", "MemoryEngine"):
        from .managers import (
            ConversationManager as ConversationManager,
        )
        from .managers import (
            GraphMemoryManager as GraphMemoryManager,
        )
        from .managers import (
            MemoryEngine as MemoryEngine,
        )

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
        from .processors import (
            ChatroomContextParser as ChatroomContextParser,
        )
        from .processors import (
            EntityResolver as EntityResolver,
        )
        from .processors import (
            GraphExtractor as GraphExtractor,
        )
        from .processors import (
            MemoryProcessor as MemoryProcessor,
        )
        from .processors import (
            TextProcessor as TextProcessor,
        )
        from .processors import (
            store_round_with_length_check as store_round_with_length_check,
        )

        _lazy.update({k: v for k, v in locals().items() if k in __all__})
        return _lazy[name]

    # ── validators ──
    if name == "IndexValidator":
        from .features.memory.infrastructure.validators import (
            IndexValidator as IndexValidator,
        )

        _lazy["IndexValidator"] = IndexValidator
        return IndexValidator

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
