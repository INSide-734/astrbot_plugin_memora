"""
注入策略适配层

按 Provider/模型自动选择记忆注入策略，将兼容性降级规则与业务逻辑解耦。
"""

from typing import Any

from ..injection.models import DeliveryMode


class InjectionAdapter:
    """Resolve delivery compatibility and conservative Provider capabilities."""

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
        """Return a supported delivery mode without ever accepting System Prompt."""

        mode = DeliveryMode(configured_mode)
        if mode is DeliveryMode.AUTO:
            return DeliveryMode.EXTRA_USER_CONTENT, None
        if mode not in {
            DeliveryMode.FAKE_TOOL_CALL,
            DeliveryMode.FAKE_TOOL_CALL_DEEPSEEK_V4,
        }:
            return mode, None

        provider_type, model_name, tools_supported = self.capabilities(provider)
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
        """Return Provider identity and conservative synthetic-tool support."""

        try:
            provider_type, model_name = self._extract_provider_info(provider)
        except (AttributeError, TypeError, ValueError):
            return "", "", False
        tools_supported = provider_type in self._TOOL_PROVIDER_TYPES
        return provider_type, model_name, tools_supported

    @staticmethod
    def _extract_provider_info(provider: Any) -> tuple[str, str]:
        if provider is None:
            return "", ""
        config = getattr(provider, "provider_config", {})
        provider_type = (
            str(config.get("type", "")) if isinstance(config, dict) else ""
        )
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
        """Retain the table-rule predicate for callers outside the executor."""

        return provider_type in rule.get("provider_types", []) or any(
            str(pattern).casefold() in model_name.casefold()
            for pattern in rule.get("model_patterns", [])
        )
