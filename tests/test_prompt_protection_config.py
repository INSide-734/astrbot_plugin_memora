"""提示词保护运行时开关的初始化契约测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from core.plugin_initializer import PluginInitializer


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
