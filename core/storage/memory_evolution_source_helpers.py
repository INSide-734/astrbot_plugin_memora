"""从 canonical metadata 提取最小演化候选证据。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def topic_keys_from_metadata(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    """提取去重、限长的话题键，不信任 metadata 容器内容。"""

    raw_topics = metadata.get("topics")
    if not isinstance(raw_topics, (list, tuple)):
        return ()
    return tuple(
        dict.fromkeys(
            topic.strip()[:128]
            for topic in raw_topics
            if isinstance(topic, str) and topic.strip()
        )
    )[:32]


def subject_key_from_metadata(metadata: Mapping[str, Any]) -> str | None:
    """把可信参与者集合压成不可逆主体键，避免在任务键中暴露身份。"""

    participant_ids = _participant_ids(metadata)
    if not participant_ids:
        return None
    encoded = json.dumps(
        sorted(participant_ids),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"subject:{hashlib.sha256(encoded).hexdigest()[:24]}"


def _participant_ids(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    """只读取系统锚定的稳定参与者字段，不使用显示名称猜测身份。"""

    raw_subject_ids = metadata.get("subject_ids")
    if isinstance(raw_subject_ids, (list, tuple)):
        ids = _plain_ids(raw_subject_ids)
        if ids:
            return ids

    raw_ids = metadata.get("participant_ids")
    if isinstance(raw_ids, (list, tuple)):
        ids = _plain_ids(raw_ids)
        if ids:
            return ids

    raw_sources = metadata.get("participant_identity_sources")
    if isinstance(raw_sources, Mapping):
        ids = _plain_ids(raw_sources.keys())
        if ids:
            return ids

    return _plain_ids(
        (metadata.get("canonical_user_id"), metadata.get("stable_user_id"))
    )


def _plain_ids(values) -> tuple[str, ...]:
    """规范稳定标识集合；非法或过长值按缺失处理。"""

    return tuple(
        dict.fromkeys(
            value.strip()
            for value in values
            if isinstance(value, str) and value.strip() and len(value.strip()) <= 256
        )
    )


__all__ = ["subject_key_from_metadata", "topic_keys_from_metadata"]
