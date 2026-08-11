"""identity 与 conversation 共享端口的兼容性回归。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_protocol_identity_runtime_satisfies_identity_conversation_port() -> None:
    """既有唯一身份运行时必须提供共享端口声明的全部协作能力。"""

    from core.identity.runtime import ProtocolIdentityRuntime
    from core.shared import IdentityConversationPort as SharedIdentityConversationPort
    from core.shared.contracts import IdentityConversationPort

    assert SharedIdentityConversationPort is IdentityConversationPort
    assert isinstance(ProtocolIdentityRuntime(), IdentityConversationPort)


def test_conversation_manager_does_not_create_identity_runtime_without_port() -> None:
    """缺少组合根注入时，会话管理器不得私自创建第二个身份运行时。"""

    from core.managers.conversation_manager import ConversationManager

    manager = ConversationManager(MagicMock())

    assert manager.identity_runtime is None


def test_event_handler_without_identity_port_returns_unsupported_identity() -> None:
    """事件处理器不得从会话管理器间接取得身份端口。"""

    from core.event_handler import EventHandler
    from core.features.identity.domain.models import IdentityTrust
    from core.identity.runtime import ProtocolIdentityRuntime

    config = MagicMock()
    config.get_section.return_value = {}
    conversation = MagicMock(identity_runtime=ProtocolIdentityRuntime())
    handler = EventHandler(
        context=MagicMock(),
        config_manager=config,
        memory_engine=MagicMock(),
        memory_processor=MagicMock(),
        conversation_manager=conversation,
    )

    identity = handler._resolve_identity(MagicMock(), writes_blocked=False)

    assert identity.trust_status is IdentityTrust.UNSUPPORTED
    assert handler._identity_runtime is None
    assert not handler._maintenance_tasks


def test_command_handler_retains_explicit_identity_port() -> None:
    """命令适配器必须保存组合根显式注入的身份端口。"""

    from core.command_handler import CommandHandler
    from core.identity.runtime import ProtocolIdentityRuntime

    runtime = ProtocolIdentityRuntime()
    handler = CommandHandler(
        context=MagicMock(),
        config_manager=MagicMock(),
        memory_engine=None,
        conversation_manager=MagicMock(),
        index_validator=None,
        identity_runtime=runtime,
    )

    assert handler._identity_runtime is runtime


@pytest.mark.asyncio
async def test_page_ready_context_exposes_initializer_identity_port() -> None:
    """Page 就绪上下文必须直接发布组合根拥有的身份端口。"""

    from core.page_api import PluginPageApi

    runtime = MagicMock()
    plugin = MagicMock()
    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, None))
    plugin.initializer.memory_engine = MagicMock()
    plugin.initializer.conversation_manager = MagicMock()
    plugin.initializer.identity_runtime = runtime
    plugin.initializer.index_validator = MagicMock()

    ready, error = await PluginPageApi(plugin)._ensure_plugin_ready()

    assert error is None
    assert ready is not None
    assert ready["identity_runtime"] is runtime
