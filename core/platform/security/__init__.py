"""平台层提示词保护组合与适配器。"""

from .lifecycle import close_prompt_protection
from .prompt_protection import PromptProtectionAdapter, build_prompt_protection_port

__all__ = [
    "build_prompt_protection_port",
    "close_prompt_protection",
    "PromptProtectionAdapter",
]
