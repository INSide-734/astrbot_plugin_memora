"""
注入策略适配层

按 Provider/模型自动选择记忆注入策略，将兼容性降级规则与业务逻辑解耦。
"""

from dataclasses import dataclass
from typing import Any

from ..injection.models import DeliveryMode
from ..shared.adapter_capabilities import (
    AdapterCapability,
    AdapterCapabilityContract,
    AdapterKind,
)


@dataclass(frozen=True, slots=True)
class ProviderCapabilitySnapshot:
    """兼容旧身份字段的 Provider 能力快照。"""

    provider_type: str
    model_name: str
    contract: AdapterCapabilityContract


class InjectionAdapter:
    """解析投递兼容性并保守声明 Provider 能力。"""

    _GEMINI_PROVIDER_TYPES = frozenset({"googlegenai_chat_completion"})
    _TOOL_PROVIDER_TYPES = frozenset(
        {
            "openai_chat_completion",
            "anthropic_chat_completion",
            "deepseek_chat_completion",
            "googlegenai_chat_completion",
        }
    )

    def resolve(
        self,
        provider: Any,
        configured_mode: DeliveryMode | str,
    ) -> tuple[DeliveryMode, str | None]:
        """返回受支持的临时投递模式，永不接受 System Prompt。

        参数:
            provider: 当前聊天 Provider；只读取兼容性身份和稳定入口。
            configured_mode: 已配置的动态记忆投递模式。

        返回:
            实际投递模式和可选的稳定降级原因码。
        """

        mode = DeliveryMode(configured_mode)
        if mode is DeliveryMode.AUTO:
            return DeliveryMode.EXTRA_USER_CONTENT, None
        if mode not in {
            DeliveryMode.FAKE_TOOL_CALL,
            DeliveryMode.FAKE_TOOL_CALL_DEEPSEEK_V4,
        }:
            return mode, None

        snapshot = self.describe_capabilities(provider)
        provider_type = snapshot.provider_type
        model_name = snapshot.model_name
        tools_supported = snapshot.contract.supports(AdapterCapability.TOOL_DELIVERY)
        if self._is_gemini(provider_type, model_name):
            return (
                DeliveryMode.USER_MESSAGE_BEFORE,
                f"{mode.value} is not fully compatible with Gemini "
                f"(type={provider_type}, model={model_name})",
            )
        if not tools_supported:
            return (
                DeliveryMode.EXTRA_USER_CONTENT,
                f"Unknown Provider cannot safely use {mode.value}; "
                "using extra_user_content",
            )
        return mode, None

    def capabilities(self, provider: Any) -> tuple[str, str, bool]:
        """返回旧调用方使用的 Provider 类型、模型名和工具支持标记。"""

        snapshot = self.describe_capabilities(provider)
        return (
            snapshot.provider_type,
            snapshot.model_name,
            snapshot.contract.supports(AdapterCapability.TOOL_DELIVERY),
        )

    def describe_capabilities(self, provider: Any) -> ProviderCapabilitySnapshot:
        """构建 Provider 身份快照和不含实例标识的能力 contract。

        参数:
            provider: 当前聊天 Provider；异常或未知对象按不支持处理。

        返回:
            保留兼容身份字段并携带安全能力 contract 的不可变快照。
        """

        try:
            provider_type, model_name = self._extract_provider_info(provider)
        except (AttributeError, TypeError, ValueError):
            provider_type, model_name = "", ""
        native: set[AdapterCapability] = set()
        if provider_type and callable(getattr(provider, "text_chat", None)):
            native.add(AdapterCapability.TEXT_GENERATION)
        if provider_type in self._TOOL_PROVIDER_TYPES:
            native.add(AdapterCapability.TOOL_DELIVERY)
        caller_enforced = (
            frozenset({AdapterCapability.CANCELLATION})
            if AdapterCapability.TEXT_GENERATION in native
            else frozenset()
        )
        contract = AdapterCapabilityContract(
            kind=AdapterKind.LLM_PROVIDER,
            native=frozenset(native),
            caller_enforced=caller_enforced,
        )
        return ProviderCapabilitySnapshot(provider_type, model_name, contract)

    @staticmethod
    def _extract_provider_info(provider: Any) -> tuple[str, str]:
        if provider is None:
            return "", ""
        config = getattr(provider, "provider_config", {})
        provider_type = str(config.get("type", "")) if isinstance(config, dict) else ""
        get_model = getattr(provider, "get_model", None)
        raw_model = get_model() if callable(get_model) else ""
        model_name = str(raw_model) if raw_model is not None else ""
        return provider_type, model_name

    @classmethod
    def _is_gemini(cls, provider_type: str, model_name: str) -> bool:
        return (
            provider_type in cls._GEMINI_PROVIDER_TYPES
            or "gemini" in model_name.casefold()
        )

    @staticmethod
    def _matches_rule(
        rule: dict[str, Any], provider_type: str, model_name: str
    ) -> bool:
        """为 executor 外部调用方保留表规则匹配。"""

        return provider_type in rule.get("provider_types", []) or any(
            str(pattern).casefold() in model_name.casefold()
            for pattern in rule.get("model_patterns", [])
        )


__all__ = ["InjectionAdapter", "ProviderCapabilitySnapshot"]
