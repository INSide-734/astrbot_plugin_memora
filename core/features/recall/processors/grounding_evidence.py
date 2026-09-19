"""来源证据的构造与定位。

把 LLM 的受控引用解析为带稳定消息身份、角色与正文范围的证据条目，并在
复核时按稳定标识或指纹重新定位已持久化证据。这里不做任何放行/隔离判定。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from ....shared.contracts.conversation import Message, message_evidence_fingerprint
from ...quality.domain.gate_config import GateProfile

USER_ROLE = "user"
_SUPPORT_SCORE = Callable[[str, str, "GateProfile"], float]


def evidence_fingerprint(message: Message) -> str:
    """生成不暴露正文或身份的稳定消息证据指纹。"""

    return message_evidence_fingerprint(
        message.role, Message.content_to_text(message.content)
    )


def reference_seq(
    message_index: int,
    message_seqs: Sequence[int | None] | None,
) -> int | None:
    """读取与消息同序的窗口序号，缺失或非法时不伪造。"""

    if message_seqs is None or message_index >= len(message_seqs):
        return None
    value = message_seqs[message_index]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def resolve_references(
    raw_refs: list[Any],
    messages: list[Message],
    *,
    inferred: bool,
    max_refs: int,
    message_seqs: Sequence[int | None] | None = None,
) -> tuple[list[dict[str, Any]], str, list[Message]] | None:
    """校验引用边界并构造内部证据，不接受布尔值冒充整数。

    返回的正文只由 ``user`` 角色片段组成，作为事实的支持正文；
    其他角色的引用仍进入证据，但不单独支撑声明。
    """

    evidence: list[dict[str, Any]] = []
    snippets: list[str] = []
    referenced_messages: list[Message] = []
    for raw_ref in raw_refs[:max_refs]:
        if not isinstance(raw_ref, dict):
            continue  # 坏引用过滤化：单条非法不再毁整条候选
        message_index = raw_ref.get("message_index")
        start = raw_ref.get("start")
        end = raw_ref.get("end")
        if (
            isinstance(message_index, bool)
            or isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(message_index, int)
            or not isinstance(start, int)
            or not isinstance(end, int)
        ):
            continue
        if message_index < 0 or message_index >= len(messages):
            continue
        message = messages[message_index]
        content = Message.content_to_text(message.content)
        if start < 0 or end <= start or end > len(content):
            continue
        snippet = content[start:end].strip()
        if not snippet:
            continue
        role = message.role
        evidence.append(
            {
                "message_index": message_index,
                "message_id": message.id,
                "message_seq": reference_seq(message_index, message_seqs),
                "role": role,
                "start": start,
                "end": end,
                "message_fingerprint": evidence_fingerprint(message),
                "inferred": inferred,
            }
        )
        if role == USER_ROLE:
            snippets.append(snippet)
            referenced_messages.append(replace(message, content=content[start:end]))
    if not evidence:
        return None
    return evidence, "\n".join(snippets), referenced_messages


def infer_references(
    claim_text: str,
    messages: list[Message],
    profile: GateProfile,
    support_score: _SUPPORT_SCORE,
) -> list[dict[str, int]]:
    """仅在模型缺少引用时从当前窗口的 user 消息推断高相关受控引用。"""

    min_score = profile.thresholds.min_inference_score
    scored: list[tuple[float, int, str]] = []
    for index, message in enumerate(messages):
        if str(message.role or "").strip().lower() != USER_ROLE:
            continue
        content = Message.content_to_text(message.content)
        if not content.strip():
            continue
        score = support_score(claim_text, content, profile)
        scored.append((score, index, content))
    if not scored:
        return []
    scored.sort(reverse=True)
    best_score = scored[0][0]
    if best_score < min_score:
        return []
    selected = [item for item in scored if item[0] >= max(min_score, best_score - 0.08)]
    return [
        {"message_index": index, "start": 0, "end": len(content)}
        for _, index, content in selected[:3]
    ]


def match_stored_evidence(
    item: Mapping[str, Any],
    messages: list[Message],
) -> int | None:
    """Relocate only the exact message identity, role and fingerprint."""
    return next(
        (
            index
            for index, message in enumerate(messages)
            if message.id == item.get("message_id")
            and message.role == item.get("role")
            and evidence_fingerprint(message) == item.get("message_fingerprint")
        ),
        None,
    )


__all__ = [
    "USER_ROLE",
    "evidence_fingerprint",
    "infer_references",
    "match_stored_evidence",
    "reference_seq",
    "resolve_references",
]
