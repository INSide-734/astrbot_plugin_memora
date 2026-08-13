"""Provider 注入投递兼容适配器的独立契约测试。"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from core.features.injection.domain.models import DeliveryMode
from core.features.injection.application.injection_adapter import InjectionAdapter


class TestInjectionAdapter:
    """验证 Provider 能力识别和投递模式降级。"""

    def test_normal_delivery_is_preserved(self) -> None:
        """验证普通临时用户内容投递保持不变。"""

        mode, reason = InjectionAdapter().resolve(
            MagicMock(), DeliveryMode.EXTRA_USER_CONTENT
        )
        assert mode is DeliveryMode.EXTRA_USER_CONTENT
        assert reason is None

    @pytest.mark.parametrize("configured", [DeliveryMode.AUTO, "auto"])
    def test_auto_resolves_to_temporary_extra_user_content(
        self,
        configured: DeliveryMode | str,
    ) -> None:
        """验证自动模式解析为临时用户附加内容。"""

        mode, reason = InjectionAdapter().resolve(MagicMock(), configured)
        assert mode is DeliveryMode.EXTRA_USER_CONTENT
        assert reason is None

    def test_removed_system_prompt_delivery_is_rejected(self) -> None:
        """验证已移除的 System Prompt 投递不会重新进入动态注入链。"""

        with pytest.raises(ValueError):
            InjectionAdapter().resolve(MagicMock(), "system_prompt")

    def test_fake_tool_call_is_preserved_for_supported_provider(self) -> None:
        """验证已知工具 Provider 保留标准伪工具投递。"""

        provider = MagicMock()
        provider.provider_config = {"type": "openai_chat_completion"}
        provider.get_model.return_value = "gpt-4"
        mode, reason = InjectionAdapter().resolve(provider, DeliveryMode.FAKE_TOOL_CALL)
        assert mode is DeliveryMode.FAKE_TOOL_CALL
        assert reason is None

    def test_fake_tool_call_downgrades_for_gemini_provider_type(self) -> None:
        """验证 Gemini Provider 类型降级为用户消息前置。"""

        provider = MagicMock()
        provider.provider_config = {"type": "googlegenai_chat_completion"}
        provider.get_model.return_value = "gemini-2.0-flash"
        mode, reason = InjectionAdapter().resolve(provider, DeliveryMode.FAKE_TOOL_CALL)
        assert mode is DeliveryMode.USER_MESSAGE_BEFORE
        assert reason is not None
        assert "Gemini" in reason

    def test_fake_tool_call_downgrades_on_gemini_model_match(self) -> None:
        """验证模型名命中 Gemini 时同样执行兼容降级。"""

        provider = MagicMock()
        provider.provider_config = {"type": "custom_provider"}
        provider.get_model.return_value = "gemini-pro"
        mode, reason = InjectionAdapter().resolve(provider, DeliveryMode.FAKE_TOOL_CALL)
        assert mode is DeliveryMode.USER_MESSAGE_BEFORE
        assert reason is not None

    @pytest.mark.parametrize("provider", [None, MagicMock(spec=[])])
    def test_unknown_provider_uses_widest_compatible_delivery(
        self,
        provider: Any,
    ) -> None:
        """验证未知 Provider 降级到最宽兼容的临时投递。"""

        mode, reason = InjectionAdapter().resolve(provider, DeliveryMode.FAKE_TOOL_CALL)
        assert mode is DeliveryMode.EXTRA_USER_CONTENT
        assert reason is not None

    @pytest.mark.parametrize("provider", [None, MagicMock(spec=[])])
    def test_unknown_provider_capabilities_are_conservative(
        self,
        provider: Any,
    ) -> None:
        """验证未知 Provider 不会被推断为支持工具投递。"""

        provider_type, model_name, tools_supported = InjectionAdapter().capabilities(
            provider
        )
        assert provider_type == ""
        assert model_name == ""
        assert tools_supported is False

    def test_capabilities_return_provider_identity_and_known_tool_support(self) -> None:
        """验证已知 Provider 返回稳定身份和工具能力。"""

        provider = MagicMock()
        provider.provider_config = {"type": "openai_chat_completion"}
        provider.get_model.return_value = "gpt-4.1"
        assert InjectionAdapter().capabilities(provider) == (
            "openai_chat_completion",
            "gpt-4.1",
            True,
        )
