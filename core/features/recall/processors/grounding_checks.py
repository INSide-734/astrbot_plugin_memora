"""候选声明与来源片段之间的词面、数值、否定与主体检查。

这些检查只使用当前窗口的引用片段：支持度只由 ``user`` 角色片段给出，
群聊主体按稳定标识归属，数字与否定沿用确定性规则。
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from ....shared.contracts.conversation import Message
from ...quality.domain.gate_config import (
    BUILTIN_NEGATION_MARKERS,
    BUILTIN_NEGATION_WHITELIST,
    GateProfile,
)
from .grounding_dates import _CJK_NUM_RE, _cjk_to_int, supported_claim_date_numbers
from .grounding_evidence import USER_ROLE

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?")
_LATIN_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
_CJK_CHUNK_RE = re.compile(r"[\u3400-\u9fff]+")
_GENERIC_TOKENS = {
    "用户",
    "对方",
    "成员",
    "群成员",
    "表示",
    "说道",
    "提到",
    "assistant",
    "user",
}
_SYNONYM_REPLACEMENTS = (
    ("星期五", "周五"),
    ("礼拜五", "周五"),
    ("准备", "计划"),
    ("打算", "计划"),
    ("前往", "去"),
    ("喜爱", "喜欢"),
    ("偏爱", "喜欢"),
    ("更换", "换"),
)


def normalize_text(value: str, profile: GateProfile | None = None) -> str:
    """统一大小写、兼容字符和同义表达，并保留英文词元边界。"""

    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    replacements = _SYNONYM_REPLACEMENTS
    if profile is not None:
        replacements = replacements + tuple(
            (pair.source.casefold(), pair.target.casefold())
            for pair in profile.word_lists.synonym_pairs
        )
    for source, target in replacements:
        normalized = normalized.replace(source, target)
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", normalized).strip()


def tokens(normalized: str) -> set[str]:
    """提取英文词元与中文二元片段，过滤无信息泛称。"""

    found = set(_LATIN_TOKEN_RE.findall(normalized))
    for chunk in _CJK_CHUNK_RE.findall(normalized):
        if len(chunk) == 1:
            found.add(chunk)
            continue
        found.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return {token for token in found if token not in _GENERIC_TOKENS}


def support_score(claim_text: str, source_text: str, profile: GateProfile) -> float:
    """组合词元覆盖与字符序列相似度，权重由 profile 控制。"""

    claim_normalized = normalize_text(claim_text, profile)
    source_normalized = normalize_text(source_text, profile)
    if not claim_normalized or not source_normalized:
        return 0.0
    if claim_normalized in source_normalized or source_normalized in claim_normalized:
        return 1.0
    claim_tokens = tokens(claim_normalized)
    source_tokens = tokens(source_normalized)
    token_score = (
        len(claim_tokens.intersection(source_tokens)) / len(claim_tokens)
        if claim_tokens
        else 0.0
    ) * profile.scoring.token_weight
    if not profile.scoring.sequence_enabled:
        return token_score
    sequence_score = SequenceMatcher(None, claim_normalized, source_normalized).ratio()
    return max(token_score, sequence_score * profile.scoring.sequence_weight)


def subject_key(message: Message) -> str:
    """只用稳定标识给消息主体分组，显示名不参与归属。"""

    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    for key in ("canonical_user_id", "stable_user_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    sender_id = message.sender_id
    return sender_id.strip() if isinstance(sender_id, str) and sender_id.strip() else ""


def validate_group_subject(
    candidate: dict[str, Any],
    referenced_messages: list[Message],
    *,
    is_group_chat: bool,
    profile: GateProfile,
) -> str | None:
    """用引用消息的稳定标识验证群聊主体，显示名不参与归属。"""

    if not is_group_chat:
        return None
    users: dict[str, set[str]] = {}
    for message in referenced_messages:
        if message.role != USER_ROLE:
            continue
        key = subject_key(message)
        if not key:
            continue
        labels = {
            normalize_text(value, profile)
            for value in (
                message.sender_id,
                message.sender_name,
                message.metadata.get("identity_label")
                if isinstance(message.metadata, dict)
                else None,
            )
            if isinstance(value, str) and value.strip()
        }
        users.setdefault(key, set()).update(labels)
    if len(users) <= 1:
        return None
    participants = {
        normalize_text(item, profile)
        for item in (candidate.get("participants") or [])
        if isinstance(item, str) and item.strip()
    }
    if not participants:
        return "grounding_subject_ambiguous"
    # 同一标签同时属于多个稳定主体（群聊同名成员）时不能用于归属。
    ambiguous = {
        label
        for label in participants
        if sum(1 for labels in users.values() if label in labels) > 1
    }
    usable = participants - ambiguous
    if not usable:
        return "grounding_subject_ambiguous"
    if any(not labels.intersection(usable) for labels in users.values()):
        return "grounding_subject_mismatch"
    return None


def validate_numbers(
    claim_text: str,
    source_text: str,
    referenced_messages: list[Message],
) -> str | None:
    """严格匹配普通数值，仅放行可信身份标签与可靠日期规范化。"""

    claim_without_identity_labels = claim_text
    for label in trusted_identity_labels(referenced_messages):
        claim_without_identity_labels = claim_without_identity_labels.replace(label, "")
    claim_numbers = canonical_numbers(claim_without_identity_labels)
    source_numbers = canonical_numbers(source_text)
    supported_date_numbers = supported_claim_date_numbers(
        claim_text,
        source_text,
        referenced_messages,
    )
    if claim_numbers - source_numbers - supported_date_numbers:
        return "grounding_numeric_conflict"
    return None


def trusted_identity_labels(messages: list[Message]) -> set[str]:
    """返回引用消息中由运行时确认的稳定身份标签。"""

    labels: set[str] = set()
    for message in messages:
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        label = metadata.get("identity_label")
        if metadata.get("identity_trusted") is True and isinstance(label, str):
            normalized = label.strip()
            if normalized:
                labels.add(normalized)
    return labels


def canonical_numbers(text: str) -> set[str]:
    """规范前导零和小数尾零，并把中文数字归一为阿拉伯数字。"""

    converted = _CJK_NUM_RE.sub(lambda match: str(_cjk_to_int(match.group(0))), text)
    canonical: set[str] = set()
    for raw_value in _NUMBER_RE.findall(converted):
        integer, separator, fraction = raw_value.partition(".")
        integer = integer.lstrip("0") or "0"
        if separator:
            fraction = fraction.rstrip("0")
        canonical.add(f"{integer}.{fraction}" if fraction else integer)
    return canonical


def validate_negation(
    claim_text: str,
    profile: GateProfile,
    referenced_messages: list[Message],
) -> str | None:
    """仅以 user 角色引用片段判定否定极性（白名单短语先剔除）。

    取舍：assistant 片段不参与否定预检——assistant 的修辞性否定
    （如“不亚于”“毫无悬念”）不是用户事实证据；用户真实否定翻转
    仍由 user 片段极性对比捕捉。引用中无 user 片段时直接跳过检查。
    """

    user_snippets = [
        snippet
        for snippet in (
            Message.content_to_text(message.content).strip()
            for message in referenced_messages
            if message.role == USER_ROLE
        )
        if snippet
    ]
    if not user_snippets:
        return None
    user_source_text = "\n".join(user_snippets)
    whitelist = {
        phrase.casefold()
        for phrase in (
            *BUILTIN_NEGATION_WHITELIST,
            *profile.word_lists.negation_whitelist,
        )
    }
    claim_clean = claim_text.casefold()
    source_clean = user_source_text.casefold()
    for phrase in sorted(whitelist, key=len, reverse=True):
        claim_clean = claim_clean.replace(phrase, "")
        source_clean = source_clean.replace(phrase, "")
    marker_cfg = profile.word_lists.negation_markers
    if marker_cfg.mode == "replace":
        markers = tuple(item.casefold() for item in marker_cfg.items)
    else:
        markers = tuple(BUILTIN_NEGATION_MARKERS) + tuple(
            item.casefold() for item in marker_cfg.items
        )
    claim_negative = any(marker in claim_clean for marker in markers)
    source_negative = any(marker in source_clean for marker in markers)
    if claim_negative != source_negative:
        return "grounding_negation_conflict"
    return None


__all__ = [
    "canonical_numbers",
    "normalize_text",
    "subject_key",
    "support_score",
    "tokens",
    "trusted_identity_labels",
    "validate_group_subject",
    "validate_negation",
    "validate_numbers",
]
