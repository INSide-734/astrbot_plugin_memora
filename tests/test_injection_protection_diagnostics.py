"""注入保护失败的脱敏诊断回归测试。"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from astrbot.api import logger as astrbot_logger

from core.injection import executor as executor_module
from core.injection.executor import InjectionExecutionContext, InjectionExecutor
from core.injection.models import (
    DeliveryMode,
    InjectionDecision,
    InjectionOutcome,
    PresetName,
    RoutingMode,
)
from core.injection.presets import get_preset
from core.platform.security import prompt_sanitizer as prompt_sanitizer_module
from core.utils.injection_adapter import InjectionAdapter


def test_injection_protection_modules_use_astrbot_logger() -> None:
    """注入与保护模块必须使用带 AstrBot 运行时字段的官方 logger。"""
    assert executor_module.logger is astrbot_logger
    assert prompt_sanitizer_module.logger is astrbot_logger


@pytest.mark.asyncio
async def test_protection_failure_logs_only_safe_metadata(caplog) -> None:
    """保护异常日志应包含定位字段，但不得泄露载荷、scope 或异常消息。"""
    preset = get_preset(PresetName.QUALITY)
    decision = InjectionDecision(
        routing_mode=RoutingMode.MANUAL,
        configured_preset=PresetName.QUALITY,
        recommended_preset=PresetName.QUALITY,
        resolved_preset=PresetName.QUALITY,
        content_level=preset.content_level,
        memory_budget_chars=preset.memory_budget_chars,
        max_memories=preset.max_memories,
        preferred_delivery=DeliveryMode.EXTRA_USER_CONTENT,
        resolved_delivery=DeliveryMode.EXTRA_USER_CONTENT,
        skip_passive_recall=False,
        allow_tool_fallback=preset.allow_tool_fallback,
        memory_max_chars=preset.memory_max_chars,
        metadata_max_chars=preset.metadata_max_chars,
        include_key_facts=preset.include_key_facts,
        include_topics=preset.include_topics,
        include_participants=preset.include_participants,
        compact_header=preset.compact_header,
        reason_codes=("ADMIN_MANUAL",),
    )
    request = SimpleNamespace(
        prompt="普通用户消息",
        contexts=[],
        extra_user_content_parts=[],
    )
    payload_canary = "private payload canary alpha beta gamma"
    scope_canary = "private-scope-canary"
    exception_canary = "private exception canary"
    protection = MagicMock()
    protection.wrap_prompt.side_effect = RuntimeError(exception_canary)

    with caplog.at_level(
        logging.ERROR,
        logger="astrbot.memora.injection.executor",
    ):
        result = await InjectionExecutor(InjectionAdapter(), protection).execute(
            request,
            decision,
            InjectionExecutionContext(
                query="普通查询",
                memories=[
                    {
                        "id": "diagnostic-memory",
                        "content": payload_canary,
                        "score": 1.0,
                        "metadata": {},
                    }
                ],
                scope_id=scope_canary,
            ),
        )

    assert result.outcome is InjectionOutcome.ERROR
    assert result.error_code == "PROTECTION_FAILED"
    log_text = caplog.text
    assert "stage=prompt_protection" in log_text
    assert "exception_type=RuntimeError" in log_text
    assert "scope_present=True" in log_text
    assert "payload_chars=" in log_text
    assert payload_canary not in log_text
    assert scope_canary not in log_text
    assert exception_canary not in log_text
