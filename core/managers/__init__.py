"""
管理器模块
包含会话管理器、记忆引擎等管理组件
"""

from typing import Any

from .conversation_manager import ConversationManager, create_conversation_manager
from .graph_memory_manager import GraphMemoryManager

__all__ = [
    "ConversationManager",
    "GraphMemoryManager",
    "MemoryEngine",
    "create_conversation_manager",
]


def __getattr__(name: str) -> Any:
    """首次访问时加载会反向组合 manager mixin 的记忆引擎。"""

    if name == "MemoryEngine":
        from .memory_engine import MemoryEngine as _MemoryEngine

        globals()[name] = _MemoryEngine
        return _MemoryEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
