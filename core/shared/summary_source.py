"""跨 feature 共享的总结来源摘要工具。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from .contracts.conversation import Message


def source_window_digest(
    messages: Sequence[Message], message_seqs: Sequence[int]
) -> str:
    """按内部序号和完整持久消息形状计算稳定 SHA-256 摘要。"""
    if len(messages) != len(message_seqs):
        raise ValueError("消息与 message_seq 数量不一致")
    digest = hashlib.sha256()
    for message, message_seq in zip(messages, message_seqs, strict=True):
        if not isinstance(message, Message):
            raise TypeError("source window 只能包含 Message")
        payload = (message_seq, message.to_dict())
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


__all__ = ["source_window_digest"]
