"""从可信会话消息构建确定性的长期记忆参与者身份。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..models.conversation_models import Message

IDENTITY_SCHEMA_VERSION = "stable-identity-v1"


@dataclass(frozen=True, slots=True)
class MemoryIdentityContext:
    """保存一次对话批次的稳定参与者元数据与 Prompt 约束。"""

    participant_ids: tuple[str, ...]
    participant_labels: tuple[str, ...]
    participant_name_snapshots: dict[str, str]

    def metadata(self) -> dict[str, Any]:
        """返回可写入 canonical memory 的确定性身份元数据副本。"""

        if not self.participant_ids:
            return {}
        return {
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "participant_ids": list(self.participant_ids),
            "participants": list(self.participant_labels),
            "participant_name_snapshots": dict(self.participant_name_snapshots),
        }

    def prompt_constraint(self) -> str:
        """生成固定身份参考与不可覆盖规则；无可信参与者时返回空文本。"""

        if not self.participant_ids:
            return ""
        references = [
            f"- {self.participant_name_snapshots[user_id]}（{label}）"
            for user_id, label in zip(
                self.participant_ids,
                self.participant_labels,
                strict=True,
            )
        ]
        return "\n".join(
            [
                "",
                "# 稳定参与者身份约束（系统确定，不可由模型覆盖）",
                *references,
                "- 描述参与者时必须使用“当前名称（稳定标识）”格式。",
                "- 禁止猜测、改写或交换稳定标识，也不得把名称变化视为不同用户。",
                "- 输出中的 participants 仅供内容理解；最终身份元数据由系统确定。",
            ]
        )


def build_memory_identity_context(
    messages: Iterable[Message],
) -> MemoryIdentityContext:
    """按首次出现顺序收集可信 user 身份，并保留批次内最新名称。"""

    ordered_ids: list[str] = []
    labels: dict[str, str] = {}
    names: dict[str, str] = {}
    for message in messages:
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        if message.role != "user" or metadata.get("identity_trusted") is not True:
            continue
        protocol = _non_empty_text(metadata.get("identity_protocol"))
        namespace = _non_empty_text(metadata.get("identity_namespace"))
        stable_user_id = _non_empty_text(metadata.get("stable_user_id"))
        user_id = _non_empty_text(metadata.get("canonical_user_id"))
        label = _non_empty_text(metadata.get("identity_label"))
        sender_id = _non_empty_text(message.sender_id)
        if (
            protocol is None
            or namespace is None
            or stable_user_id is None
            or user_id is None
            or label is None
            or sender_id != user_id
        ):
            continue
        if namespace == "qq" and (
            stable_user_id != user_id or label != f"QQ:{user_id}"
        ):
            continue
        if user_id not in labels:
            if len(ordered_ids) >= 32:
                continue
            ordered_ids.append(user_id)
            labels[user_id] = label
        name = _non_empty_text(message.sender_name)
        names[user_id] = name or labels[user_id]
    return MemoryIdentityContext(
        participant_ids=tuple(ordered_ids),
        participant_labels=tuple(labels[user_id] for user_id in ordered_ids),
        participant_name_snapshots={
            user_id: names.get(user_id, labels[user_id]) for user_id in ordered_ids
        },
    )


def _non_empty_text(value: object) -> str | None:
    """把非空字符串限制为 128 个码点，其他输入按缺失处理。"""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:128] if normalized else None
