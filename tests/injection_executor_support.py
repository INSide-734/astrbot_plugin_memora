"""统一原子注入执行器测试的共享构造器与 TextPart 观测辅助。

两个执行器契约文件共享同一组请求、决策和上下文构造器，保证拆分后仍对相同
请求形状、固定预算和临时内容部件做一致断言。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock

import pytest
from astrbot.core.agent.message import TextPart

from core.features.injection.application.executor import InjectionExecutionContext
from core.features.injection.application.router import (
    InjectionRoutingConfig,
    InjectionStrategyRouter,
)
from core.features.injection.domain.models import (
    DeliveryMode,
    InjectionDecision,
    PresetName,
    RequestSignals,
    RoutingMode,
)


def request_stub() -> MagicMock:
    """构造带稳定 system prompt、用户消息和旧上下文的 Provider 请求替身。"""

    req = MagicMock()
    req.system_prompt = "stable-system-prefix"
    req.prompt = "current user message"
    req.contexts = [{"role": "user", "content": "older turn"}]
    req.extra_user_content_parts = []
    req.provider = None
    return req


def tool_capable_provider() -> MagicMock:
    """构造被识别为支持工具投递的 OpenAI Provider 替身。"""

    provider = MagicMock()
    provider.provider_config = {"type": "openai_chat_completion"}
    provider.get_model.return_value = "gpt-4.1"
    return provider


def decision_stub(
    delivery: DeliveryMode = DeliveryMode.EXTRA_USER_CONTENT,
    *,
    preset: PresetName = PresetName.BALANCED,
) -> InjectionDecision:
    """按手动路由、固定预算与工具能力信号构造不可变注入决策。"""

    tool_usable = preset is PresetName.TOOL_FIRST
    return InjectionStrategyRouter().route_final(
        InjectionRoutingConfig(
            mode=RoutingMode.MANUAL,
            manual_preset=preset,
            delivery_override=delivery,
        ),
        RequestSignals(
            candidate_count=3,
            top_confidence=0.9,
            tools_supported=tool_usable,
            memory_tool_available=tool_usable,
        ),
    )


def context_stub(
    memories: list[dict[str, Any]],
    **overrides: Any,
) -> InjectionExecutionContext:
    """以查询词 coffee 和默认分层预算构造执行上下文。"""

    values: dict[str, Any] = {
        "query": "coffee",
        "memories": memories,
        "cognitive_context": "",
        "prospective_context": "",
        "cognitive_budget_chars": 300,
        "prospective_budget_chars": 240,
        "context_headroom_chars": 10_000,
    }
    values.update(overrides)
    return InjectionExecutionContext(**values)


def delivered_part_text() -> str:
    """读取最近一次构造的临时 TextPart 的文本载荷。"""

    assert TextPart.call_args is not None
    if "text" in TextPart.call_args.kwargs:
        return str(TextPart.call_args.kwargs["text"])
    return str(TextPart.call_args.args[0])


@pytest.fixture(autouse=True)
def reset_text_part_mock() -> Generator[None, None, None]:
    """每个测试前重置 TextPart 及其临时标记的调用记录。"""

    TextPart.reset_mock()
    TextPart.return_value.mark_as_temp.reset_mock()
    yield
