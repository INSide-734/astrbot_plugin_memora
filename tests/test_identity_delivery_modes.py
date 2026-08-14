"""临时身份参考在统一注入执行器中的五种交付契约。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from astrbot.core.agent.message import TextPart

from core.features.injection.application.executor import (
    InjectionExecutionContext,
    InjectionExecutor,
)
from core.features.injection.domain.models import (
    ContentLevel,
    DeliveryMode,
    InjectionDecision,
    InjectionOutcome,
    PresetName,
    RoutingMode,
)


class _FixedAdapter:
    """让测试指定的交付模式保持不变。"""

    def resolve(self, _provider, delivery: DeliveryMode):
        """返回原交付模式且不触发降级。"""

        return delivery, None


def _decision(delivery: DeliveryMode) -> InjectionDecision:
    """构造有足够身份 metadata 预算的固定注入决策。"""

    return InjectionDecision(
        routing_mode=RoutingMode.MANUAL,
        configured_preset=PresetName.BALANCED,
        recommended_preset=PresetName.BALANCED,
        resolved_preset=PresetName.BALANCED,
        content_level=ContentLevel.COMPACT,
        memory_budget_chars=1200,
        max_memories=1,
        preferred_delivery=delivery,
        resolved_delivery=delivery,
        skip_passive_recall=False,
        allow_tool_fallback=False,
        memory_max_chars=300,
        metadata_max_chars=300,
        include_key_facts=True,
        include_topics=True,
        include_participants=False,
        compact_header=True,
    )


def _request() -> SimpleNamespace:
    """构造可观察五类临时写入面的 ProviderRequest 替身。"""

    return SimpleNamespace(
        system_prompt="SYSTEM-BYTE-IDENTICAL",
        prompt="当前问题",
        contexts=[{"role": "user", "content": "较早消息"}],
        extra_user_content_parts=[],
    )


def _extra_user_payload(req: SimpleNamespace) -> str:
    """兼容真实 TextPart 与测试环境 MagicMock，读取临时附加文本。"""

    assert req.extra_user_content_parts
    part_text = getattr(req.extra_user_content_parts[-1], "text", None)
    if isinstance(part_text, str):
        return part_text
    call_args = getattr(TextPart, "call_args", None)
    assert call_args is not None
    if "text" in call_args.kwargs:
        return str(call_args.kwargs["text"])
    return str(call_args.args[0])


def _model_visible_text(req: SimpleNamespace, delivery: DeliveryMode) -> str:
    """读取指定交付模式最终提供给模型的动态用户侧文本。"""

    if delivery is DeliveryMode.EXTRA_USER_CONTENT:
        return _extra_user_payload(req)
    if delivery in {
        DeliveryMode.USER_MESSAGE_BEFORE,
        DeliveryMode.USER_MESSAGE_AFTER,
    }:
        return req.prompt
    return "\n".join(str(item.get("content") or "") for item in req.contexts)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delivery",
    [
        DeliveryMode.EXTRA_USER_CONTENT,
        DeliveryMode.USER_MESSAGE_BEFORE,
        DeliveryMode.USER_MESSAGE_AFTER,
        DeliveryMode.FAKE_TOOL_CALL,
        DeliveryMode.FAKE_TOOL_CALL_DEEPSEEK_V4,
    ],
)
async def test_identity_reference_reaches_every_delivery_without_system_prompt(
    delivery: DeliveryMode,
) -> None:
    """五种交付都应复用同一受保护 payload，且不得修改 System Prompt。"""

    reset_mock = getattr(TextPart, "reset_mock", None)
    if callable(reset_mock):
        reset_mock()
    req = _request()
    original_system = req.system_prompt
    line = "- “旧名称”是历史名称；当前显示为“当前名称”（QQ:10001）。"
    result = await InjectionExecutor(_FixedAdapter()).execute(
        req,
        _decision(delivery),
        InjectionExecutionContext(
            query="当前问题",
            memories=[
                {
                    "id": 17,
                    "content": "历史事实",
                    "score": 0.9,
                    "metadata": {
                        "importance": 0.8,
                        "identity_reference_lines": [line],
                    },
                }
            ],
            cognitive_context="",
            prospective_context="",
            cognitive_budget_chars=0,
            prospective_budget_chars=0,
            context_headroom_chars=2000,
        ),
    )

    visible = _model_visible_text(req, delivery)
    assert result.outcome is InjectionOutcome.INJECTED
    assert "<memora-untrusted-memory>" in visible
    assert "身份参考：" in visible
    assert line in visible
    assert req.system_prompt == original_system
