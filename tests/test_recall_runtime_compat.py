"""召回链路与真实 AstrBot 请求默认值的兼容契约。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch


def test_unlimited_provider_context_keeps_injection_budget_available() -> None:
    """AstrBot 的零上下文上限表示不限制，不能被误判为零注入预算。"""

    from core.injection.headroom import estimate_context_headroom_chars

    request = SimpleNamespace(
        prompt="当前消息",
        system_prompt="",
        contexts=[],
        extra_user_content_parts=[],
        func_tool=None,
        tool_calls_result=None,
        image_urls=[],
        audio_urls=[],
    )
    provider = SimpleNamespace(provider_config={"max_context_tokens": 0})

    assert estimate_context_headroom_chars(provider, request) == 13_000


def test_missing_provider_context_limit_keeps_injection_budget_available() -> None:
    """常见 Provider 未声明上下文上限时仍应允许受硬上限保护的召回注入。"""

    from core.injection.headroom import estimate_context_headroom_chars

    request = SimpleNamespace(
        prompt="当前消息",
        system_prompt="",
        contexts=[],
        extra_user_content_parts=[],
        func_tool=None,
        tool_calls_result=None,
        image_urls=[],
        audio_urls=[],
    )
    provider = SimpleNamespace(provider_config={})

    assert estimate_context_headroom_chars(provider, request) == 13_000


def test_injection_result_is_visible_without_debug_mode() -> None:
    """普通 AstrBot INFO 日志应给出脱敏注入摘要，便于定位静默跳过。"""

    from core.handlers.recall_handler import RecallHandler

    decision = SimpleNamespace(
        resolved_delivery=SimpleNamespace(value="extra_user_content"),
        resolved_preset=SimpleNamespace(value="balanced"),
    )
    signals = SimpleNamespace(candidate_count=2)
    result = SimpleNamespace(
        actual_resolved_delivery=None,
        outcome=SimpleNamespace(value="empty"),
        error_code=None,
        selected_count=0,
        configured_budget_chars=1_740,
        effective_budget_chars=0,
        actual_payload_chars=0,
    )

    with patch("core.handlers.recall_handler.logger.info") as log_info:
        RecallHandler._report_injection_result(decision, signals, result)

    log_info.assert_called_once_with(
        "[召回流程] 注入结果：outcome=%s, route=%s, delivery=%s, "
        "candidates=%d, selected=%d, budget=%d/%d",
        "empty",
        "balanced",
        "extra_user_content",
        2,
        0,
        0,
        1_740,
    )
