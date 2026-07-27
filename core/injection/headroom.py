"""估算当前 AstrBot 请求可用于动态记忆注入的字符空间。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


# AstrBot 将 ``max_context_tokens <= 0`` 定义为不限制。这里使用所有注入层的
# 配置硬上限（记忆 10000 + 认知 2000 + 前瞻 1000），既避免把不限制误判为
# 零预算，也不会因此放宽 Memora 自身的载荷上限。
UNBOUNDED_CONTEXT_HEADROOM_CHARS = 13_000


def estimate_context_headroom_chars(provider: Any, req: Any) -> int:
    """按显式请求覆盖或 Provider 上下文配置估算剩余字符空间。"""

    raw_override = getattr(req, "context_headroom_chars", None)
    if isinstance(raw_override, (int, float, str)) and not isinstance(
        raw_override, bool
    ):
        try:
            return max(0, int(raw_override))
        except (OverflowError, TypeError, ValueError):
            pass

    config = getattr(provider, "provider_config", None)
    if not isinstance(config, Mapping):
        return UNBOUNDED_CONTEXT_HEADROOM_CHARS
    max_context_tokens = _nonnegative_int(config.get("max_context_tokens"))
    if max_context_tokens <= 0:
        return UNBOUNDED_CONTEXT_HEADROOM_CHARS
    output_reserve = max(
        _nonnegative_int(config.get("max_tokens")),
        _nonnegative_int(config.get("max_completion_tokens")),
    )
    request_chars = sum(
        _text_chars(getattr(req, field, None))
        for field in (
            "prompt",
            "system_prompt",
            "contexts",
            "extra_user_content_parts",
            "tool_calls_result",
            "image_urls",
            "audio_urls",
        )
    )
    tool_set = getattr(req, "func_tool", None)
    for tool in getattr(tool_set, "tools", ()):
        request_chars += _text_chars(
            {
                "name": getattr(tool, "name", ""),
                "description": getattr(tool, "description", ""),
                "parameters": getattr(tool, "parameters", {}),
            }
        )
    # 未提供 Provider tokenizer 时按每个文本字符计一个 token，确保估算保守。
    return max(0, max_context_tokens - output_reserve - request_chars)


def _nonnegative_int(value: Any) -> int:
    """把任意配置值转换为非负整数，非法值按零处理。"""

    try:
        return max(0, int(value))
    except (OverflowError, TypeError, ValueError):
        return 0


def _text_chars(value: Any) -> int:
    """递归统计请求中模型可见文本的保守字符数。"""

    if isinstance(value, str):
        return len(value)
    if isinstance(value, Mapping):
        return sum(
            len(str(key)) + _text_chars(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return sum(_text_chars(item) for item in value)
    text = getattr(value, "text", None)
    return len(text) if isinstance(text, str) else 0


__all__ = ["UNBOUNDED_CONTEXT_HEADROOM_CHARS", "estimate_context_headroom_chars"]
