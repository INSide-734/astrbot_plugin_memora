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
from .grounding_dates import (
    _CJK_DIGITS,
    _CJK_NUM_RE,
    _cjk_to_int,
    supported_claim_date_numbers,
)
from .grounding_evidence import USER_ROLE

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?")
_LATIN_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
_CJK_CHUNK_RE = re.compile(r"[\u3400-\u9fff]+")
_CJK_MEASURE_SUFFIXES = (
    "个百分点",
    "百分比",
    "摄氏度",
    "华氏度",
    "度",
    "点",
    "℃",
    "℉",
    "%",
    "％",
    "毫秒",
    "分钟",
    "小时",
    "年前",
    "年后",
    "个月",
    "厘米",
    "毫米",
    "公里",
    "千米",
    "公斤",
    "千克",
    "毫升",
    "秒",
    "年",
    "月",
    "日",
    "天",
    "周",
    "岁",
    "米",
    "克",
    "斤",
    "升",
    "元",
    "块",
    "倍",
    "次",
    "回",
    "遍",
    "章",
    "节",
    "页",
    "层",
    "集",
    "期",
    "号",
    "个",
    "名",
    "位",
    "人",
    "只",
    "条",
    "件",
    "项",
    "种",
    "本",
    "台",
    "辆",
    "张",
    "对",
    "双",
    "组",
    "级",
    "场",
    "份",
    "杯",
    "瓶",
    "顿",
    "家",
    "间",
    "座",
    "篇",
    "首",
    "句",
    "趟",
    "部",
    "封",
)
_CJK_APPROX_TAIL_MARKERS = ("多", "余", "几", "来")
# 明确语境前缀（序数、星期、百分比）可直接判定后面的数字串是数量。
_CJK_EXPLICIT_PREFIXES = ("第", "周", "星期", "礼拜", "百分之")
# 近似与系词前缀本身也会出现在词尾（如「但是」「没有」「最近」），
# 只作为辅助语境，不单独决定数字串是数量。
_CJK_APPROX_PREFIXES = (
    "大约",
    "至少",
    "至多",
    "最多",
    "最少",
    "超过",
    "不到",
    "不超过",
    "约为",
    "将近",
    "近",
    "约",
    "是",
    "为",
    "有",
)
_CJK_RATIO_PREFIXES = ("比", ":", "：", "/", "／")
_CJK_RATIO_MARKERS = set(_CJK_RATIO_PREFIXES)
# 以「千/万/亿」开头的裸数字串不是标准数量写法（标准写法是「一千」「一万」），
# 独立数量短语只接受「零一二两三四五六七八九十」开头。
_CJK_STANDALONE_LEAD_RE = re.compile(r"[零一二两三四五六七八九十]")
_CJK_FRACTION_DIGITS = {char: str(value) for char, value in _CJK_DIGITS.items()}
_CJK_DECIMAL_RE = re.compile(
    r"[点．.](?P<fraction>[零一二两三四五六七八九十百千万亿]+)"
)
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


def _subject_labels(
    messages: list[Message],
    profile: GateProfile,
) -> dict[str, set[str]]:
    """按稳定标识收集每个主体在当前范围内的可归属标签。"""

    users: dict[str, set[str]] = {}
    for message in messages:
        if str(message.role or "").strip().lower() != USER_ROLE:
            continue
        key = subject_key(message)
        if not key:
            continue
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        labels = {
            normalize_text(value, profile)
            for value in (
                message.sender_id,
                message.sender_name,
                metadata.get("identity_label"),
            )
            if isinstance(value, str) and value.strip()
        }
        users.setdefault(key, set()).update(labels)
    return users


def validate_group_subject(
    candidate: dict[str, Any],
    referenced_messages: list[Message],
    *,
    is_group_chat: bool,
    profile: GateProfile,
    subject_messages: list[Message] | None = None,
) -> str | None:
    """用稳定标识验证群聊主体，显示名不参与归属。

    ``subject_messages`` 给出用于同名歧义的更大判定范围（推断引用时为整个
    窗口）：范围内同名标签被多个稳定主体共享、或未声明 participants 且范围
    内存在多个主体时按歧义隔离。主体一致性只要求引用正文涉及的用户都能被
    participants 归属，不要求范围里的无关主体也出现在 participants 中。
    """

    if not is_group_chat:
        return None
    users = _subject_labels(referenced_messages, profile)
    ambiguity_users = (
        users
        if subject_messages is None
        else _subject_labels(subject_messages, profile)
    )
    if not ambiguity_users:
        ambiguity_users = users
    participants = {
        normalize_text(item, profile)
        for item in (candidate.get("participants") or [])
        if isinstance(item, str) and item.strip()
    }
    if not participants:
        return "grounding_subject_ambiguous" if len(ambiguity_users) > 1 else None
    # 同一标签同时属于多个稳定主体（群聊同名成员）时不能用于归属。
    ambiguous = {
        label
        for label in participants
        if sum(1 for labels in ambiguity_users.values() if label in labels) > 1
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


def _has_cjk_measure_suffix(text: str) -> bool:
    """判断中文数字后是否紧跟数量延续、量词或近似收尾语境。

    ``text`` 是数字串之后的剩余正文：先在「多/余/几/来」这类近似尾缀后继续，
    接受延续的数量级（「一百多万」）、纯近似收尾（「一万多」）或紧随的量词；
    「来」不单独收尾，避免把「一来」这类词当成数量。
    """

    remainder = text.lstrip()
    if remainder.startswith(_CJK_APPROX_TAIL_MARKERS):
        marker, remainder = remainder[0], remainder[1:].lstrip()
        if remainder.startswith(("万", "亿")):
            return True
        if marker in ("多", "余", "几") and not remainder:
            return True
    return any(remainder.startswith(suffix) for suffix in _CJK_MEASURE_SUFFIXES)


def _is_standalone_cjk_number(text: str, match: re.Match[str]) -> bool:
    """数字串两侧都是词边界时视为独立数量短语（如整条回复「二十」）。"""

    if _CJK_STANDALONE_LEAD_RE.match(match.group(0)) is None:
        return False
    before = text[: match.start()]
    after = text[match.end() :]
    return (not before or _CJK_CHUNK_RE.fullmatch(before[-1]) is None) and (
        not after or _CJK_CHUNK_RE.fullmatch(after[0]) is None
    )


def _is_contextual_cjk_number(text: str, match: re.Match[str]) -> bool:
    """只接受带数量、序数、比例或明确数值前缀的中文数字串。

    「一起」「统一」这类词中的数字字符既不构成完整数量短语，也没有量词或
    序数语境；近似与系词前缀不能单独证明数量，只接受含数字字符的多位写法
    （如「有二十」），避免「但是千万要小心」这类词尾前缀误判。
    """

    token = match.group(0)
    before = text[: match.start()].rstrip()
    after = text[match.end() :]
    if (
        len(token) == 1
        and token not in _CJK_DIGITS
        and before[-1:] in _CJK_APPROX_TAIL_MARKERS
    ):
        # 「一百多万」里的量级字符延续前一个数字串，不能单独计数。
        return False
    if before.endswith(_CJK_EXPLICIT_PREFIXES):
        return True
    if before and before[-1] in _CJK_RATIO_MARKERS:
        return True
    if before and before[-1] in {"点", "时"}:
        return True
    after_stripped = after.lstrip()
    if after_stripped.startswith(_CJK_RATIO_PREFIXES):
        return True
    if _has_cjk_measure_suffix(after):
        return True
    decimal = _CJK_DECIMAL_RE.match(after_stripped)
    if decimal is not None and _has_cjk_measure_suffix(after_stripped[decimal.end() :]):
        return True
    if _is_standalone_cjk_number(text, match):
        return True
    # 前缀只作辅助语境，仍需数字串自身是多位且含数字字符的写法（如「有二十」）。
    return (
        len(token) > 1
        and any(char in _CJK_DIGITS for char in token)
        and before.endswith(_CJK_APPROX_PREFIXES)
    )


def _canonical_decimal(integer: int, fraction: str) -> str:
    """按现有阿拉伯小数规则规范中文小数（去掉小数尾零）。"""

    fraction = fraction.rstrip("0")
    return f"{integer}.{fraction}" if fraction else str(integer)


def _canonical_fraction(text: str) -> str:
    """把中文小数位按位映射为阿拉伯数字串，保留前导零。

    含数量单位（如「三点十五」）时回退普通数字解析，避免按位映射丢字符。
    """

    if all(char in _CJK_FRACTION_DIGITS for char in text):
        return "".join(_CJK_FRACTION_DIGITS[char] for char in text)
    return str(_cjk_to_int(text))


def canonical_numbers(text: str) -> set[str]:
    """规范数字，并仅在真实数量语境中归一中文数字。"""

    canonical: set[str] = set()
    for raw_value in _NUMBER_RE.findall(text):
        integer, separator, fraction = raw_value.partition(".")
        integer = integer.lstrip("0") or "0"
        if separator:
            fraction = fraction.rstrip("0")
        canonical.add(f"{integer}.{fraction}" if fraction else integer)

    matches = list(_CJK_NUM_RE.finditer(text))
    skip_until = -1
    for match in matches:
        if match.start() < skip_until or not _is_contextual_cjk_number(text, match):
            continue
        value = _cjk_to_int(match.group(0))
        after = text[match.end() :]
        after_stripped = after.lstrip()
        decimal = _CJK_DECIMAL_RE.match(after_stripped)
        if decimal is not None:
            # 该匹配已由上面的语境判定放行：数字串后紧跟「点/．/.」加小数位。
            canonical.add(
                _canonical_decimal(
                    value, _canonical_fraction(decimal.group("fraction"))
                )
            )
            skip_until = match.end() + (
                len(after) - len(after_stripped) + decimal.end()
            )
            continue
        canonical.add(str(value))
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
