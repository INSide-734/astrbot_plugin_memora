"""
Memora 核心模块
提供统一的记忆管理引擎

目录结构按 ``platform``、``shared`` 与 ``features`` 组织；根包只保留明确的
稳定类型，并在首次访问时从唯一 owner 延迟加载。
"""

from typing import Any

__all__ = [
    # 基础组件
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

    # 共享异常门面
    if name in (
        "ConfigurationError",
        "DatabaseError",
        "InitializationError",
        "MemoraException",
        "MemoryProcessingError",
        "ProviderNotReadyError",
        "RetrievalError",
        "ValidationError",
    ):
        from .shared.errors import (
            ConfigurationError as ConfigurationError,
        )
        from .shared.errors import (
            DatabaseError as DatabaseError,
        )
        from .shared.errors import (
            InitializationError as InitializationError,
        )
        from .shared.errors import (
            MemoraException as MemoraException,
        )
        from .shared.errors import (
            MemoryProcessingError as MemoryProcessingError,
        )
        from .shared.errors import (
            ProviderNotReadyError as ProviderNotReadyError,
        )
        from .shared.errors import (
            RetrievalError as RetrievalError,
        )
        from .shared.errors import (
            ValidationError as ValidationError,
        )

        _lazy.update({k: v for k, v in locals().items() if k in __all__})
        return _lazy[name]

    # ── conversation domain ──
    if name in (
        "MemoryEvent",
        "Message",
        "Session",
    ):
        from .shared.contracts.conversation import (
            MemoryEvent as MemoryEvent,
        )
        from .shared.contracts.conversation import (
            Message as Message,
        )
        from .shared.contracts.conversation import (
            Session as Session,
        )

        _lazy.update({k: v for k, v in locals().items() if k in __all__})
        return _lazy[name]

    # ── managers（门面已迁至 features/memory 与 features/conversation）──
    if name in ("ConversationManager", "GraphMemoryManager", "MemoryEngine"):
        from .features.conversation.application.conversation_manager import (
            ConversationManager as ConversationManager,
        )
        from .features.memory.application.graph_memory_manager import (
            GraphMemoryManager as GraphMemoryManager,
        )
        from .features.memory.application.memory_engine import (
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
        from .features.recall.processors import (
            ChatroomContextParser as ChatroomContextParser,
        )
        from .features.recall.processors import (
            EntityResolver as EntityResolver,
        )
        from .features.recall.processors import (
            GraphExtractor as GraphExtractor,
        )
        from .features.recall.processors import (
            MemoryProcessor as MemoryProcessor,
        )
        from .features.recall.processors import (
            TextProcessor as TextProcessor,
        )
        from .features.recall.processors import (
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
