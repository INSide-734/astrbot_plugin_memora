"""长期记忆稳定参与者身份的纯函数契约。"""

from __future__ import annotations

from core.identity.memory import build_memory_identity_context
from core.models.conversation_models import Message


def _message(metadata: dict[str, object], *, role: str = "user") -> Message:
    """构造不依赖数据库的身份消息。"""

    return Message(
        id=1,
        session_id="session",
        role=role,
        content="消息",
        sender_id=str(metadata.get("canonical_user_id") or "sender"),
        sender_name="当前名称",
        metadata=metadata,
    )


def test_future_protocol_uses_adapter_supplied_canonical_label() -> None:
    """未来协议可复用相同元数据协议，不需要修改长期记忆处理器。"""

    context = build_memory_identity_context(
        [
            _message(
                {
                    "identity_trusted": True,
                    "identity_protocol": "future-protocol",
                    "identity_namespace": "future-user",
                    "stable_user_id": "member-7",
                    "canonical_user_id": "future-user:member-7",
                    "identity_label": "Future:member-7",
                }
            )
        ]
    )

    metadata = context.metadata()
    assert metadata["participant_ids"] == ["future-user:member-7"]
    assert metadata["participants"] == ["Future:member-7"]
    assert metadata["participant_identity_sources"] == {
        "future-user:member-7": {
            "protocol": "future-protocol",
            "identity_namespace": "future-user",
            "stable_user_id": "member-7",
            "identity_label": "Future:member-7",
        }
    }


def test_conflicting_sources_for_same_canonical_participant_are_rejected() -> None:
    """同一 canonical 的协议来源互相冲突时不得留下可猜测参与者。"""

    first = {
        "identity_trusted": True,
        "identity_protocol": "future-protocol",
        "identity_namespace": "future-user",
        "stable_user_id": "member-7",
        "canonical_user_id": "future-user:member-7",
        "identity_label": "Future:member-7",
    }
    conflicting = {
        **first,
        "identity_namespace": "forged-user",
        "stable_user_id": "forged-7",
        "identity_label": "Forged:member-7",
    }

    context = build_memory_identity_context([_message(first), _message(conflicting)])

    assert context.metadata() == {}
    assert context.prompt_constraint() == ""


def test_malformed_qq_label_and_assistant_metadata_are_ignored() -> None:
    """不一致 QQ 标签和 assistant 身份元数据都不能成为记忆参与者。"""

    malformed = {
        "identity_trusted": True,
        "identity_protocol": "onebot11",
        "identity_namespace": "qq",
        "stable_user_id": "10001",
        "canonical_user_id": "10001",
        "identity_label": "QQ:10002",
    }
    valid = {**malformed, "identity_label": "QQ:10001"}

    context = build_memory_identity_context(
        [_message(malformed), _message(valid, role="assistant")]
    )

    assert context.metadata() == {}
    assert context.prompt_constraint() == ""
