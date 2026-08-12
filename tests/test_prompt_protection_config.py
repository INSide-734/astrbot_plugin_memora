"""提示词保护运行时开关的初始化契约测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from core.platform.composition.plugin_initializer import PluginInitializer
from core.shared.contracts import PromptProtectionPort


def test_disabled_prompt_protection_is_not_created() -> None:
    """关闭提示词保护时不应创建或接入保护服务。"""

    initializer = object.__new__(PluginInitializer)
    initializer.config_manager = MagicMock()
    initializer.config_manager.get.side_effect = lambda key, default=None: {
        "security.prompt_protection_enabled": False,
    }.get(key, default)

    service = initializer._create_prompt_protection_service()

    assert service is None
    initializer.config_manager.get.assert_called_once_with(
        "security.prompt_protection_enabled", True
    )


def test_enabled_prompt_protection_is_published_as_shared_port() -> None:
    """启用时初始化器应发布平台 adapter，而不是泄露具体安全实现。"""

    initializer = object.__new__(PluginInitializer)
    initializer.config_manager = MagicMock()
    initializer.config_manager.get.side_effect = lambda key, default=None: {
        "security.prompt_protection_enabled": True,
        "security.wrapper_template_index": 2,
        "security.double_check_enabled": False,
    }.get(key, default)

    protection = initializer._create_prompt_protection_service()

    assert isinstance(protection, PromptProtectionPort)
    assert protection is not None
    assert protection.wrap_prompt("内部记忆", scope_id="request-1")
    assert protection.has_scope("request-1")
