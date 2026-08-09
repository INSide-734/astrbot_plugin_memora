"""identity 与 conversation 共享端口的兼容性回归。"""

from __future__ import annotations

from unittest.mock import MagicMock


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
    """事件处理器缺少身份端口时必须拒绝身份写入与同步。"""

    from core.event_handler import EventHandler
    from core.identity import IdentityTrust

    config = MagicMock()
    config.get_section.return_value = {}
    conversation = MagicMock(identity_runtime=None)
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
