"""提示词保护请求作用域的共享事件键。"""

PROMPT_PROTECTION_SCOPE_EXTRA_KEY = "memora_prompt_protection_scope"
PROMPT_PROTECTION_REQUIRED_EXTRA_KEY = "memora_prompt_protection_required"
PROMPT_PROTECTION_SCOPE_ATTR = "_memora_prompt_protection_scope"
PROMPT_PROTECTION_REQUIRED_ATTR = "_memora_prompt_protection_required"

__all__ = [
    "PROMPT_PROTECTION_REQUIRED_ATTR",
    "PROMPT_PROTECTION_REQUIRED_EXTRA_KEY",
    "PROMPT_PROTECTION_SCOPE_ATTR",
    "PROMPT_PROTECTION_SCOPE_EXTRA_KEY",
]
