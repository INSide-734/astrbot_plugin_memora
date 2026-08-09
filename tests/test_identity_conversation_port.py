"""identity 与 conversation 共享端口的兼容性回归。"""

from __future__ import annotations


def test_protocol_identity_runtime_satisfies_identity_conversation_port() -> None:
    """既有唯一身份运行时必须提供共享端口声明的全部协作能力。"""

    from core.identity.runtime import ProtocolIdentityRuntime
    from core.shared import IdentityConversationPort as SharedIdentityConversationPort
    from core.shared.contracts import IdentityConversationPort

    assert SharedIdentityConversationPort is IdentityConversationPort
    assert isinstance(ProtocolIdentityRuntime(), IdentityConversationPort)
