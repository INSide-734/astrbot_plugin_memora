"""反思 Prompt 使用的唯一无状态话题标签 renderer。"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

_MAX_LABEL_LENGTH = 100
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_RESERVED_DELIMITERS = frozenset({"---", "===", "```", "###", "***"})
_LABEL_REJECT_PATTERN = re.compile(
    r"(?:<\/?(?:system|assistant|user|tool|instruction)\b|"
    r"\b(?:ignore\s+previous|system\s*:|assistant\s*:|user\s*:|"
    r"执行命令|忽略(?:之前|上文)|不要遵守)\b|\{\{|\}\})",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RenderedLabels:
    """保存安全渲染块及其低敏统计。"""

    rendered_block: str = ""
    valid_count: int = 0
    rejected_count: int = 0
    truncated_count: int = 0
    estimated_tokens: int = 0
    reason_codes: tuple[str, ...] = ()


def _estimate_token_count(text: str) -> int:
    """使用固定字符比例估计渲染块 token 数。"""

    if not text:
        return 0
    return max(1, len(text) // 3)


def normalize_topic_label(
    value: object,
    *,
    max_chars: int = _MAX_LABEL_LENGTH,
) -> str | None:
    """规范化安全展示标签；非法值返回 ``None``。"""

    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        return None
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(char).startswith("C") for char in normalized):
        return None
    normalized = " ".join(normalized.split()).strip()
    if not normalized or len(normalized) > max_chars:
        return None
    if _LABEL_REJECT_PATTERN.search(normalized):
        return None
    return normalized


def normalize_topic_key(
    value: object,
    *,
    max_chars: int = _MAX_LABEL_LENGTH,
) -> str | None:
    """按固定 NFKC、空白折叠和大小写折叠生成唯一键。"""

    normalized = normalize_topic_label(value, max_chars=max_chars)
    return normalized.casefold() if normalized is not None else None


def _label_reason(value: object) -> str | None:
    """返回标签被拒绝时的固定 reason code。"""

    if not isinstance(value, str):
        return "label_invalid_type"
    if not value or not value.strip():
        return "label_empty"
    if len(value) > _MAX_LABEL_LENGTH:
        return "label_too_long"
    if any(unicodedata.category(char).startswith("C") for char in value):
        return "label_control_char"
    try:
        normalized = unicodedata.normalize("NFKC", value).strip()
    except (TypeError, ValueError):
        return "label_normalize_failed"
    if not normalized:
        return "label_normalized_empty"
    if any(delimiter in normalized for delimiter in _RESERVED_DELIMITERS):
        return "label_reserved_delimiter"
    if _LABEL_REJECT_PATTERN.search(normalized):
        return "label_instruction_structure"
    if normalize_topic_label(value) is None:
        return "topic_label_rejected"
    return None


def render_topic_labels(
    labels: Iterable[object],
    *,
    max_total_tokens: int = 256,
    max_labels: int = 32,
    max_chars: int = 2_048,
    require_source_provenance: bool = False,
) -> RenderedLabels:
    """以固定 quoted-item 结构渲染安全话题标签。

    非法标签只被丢弃并记录固定 reason code；渲染结果明确说明候选不是
    当前窗口事实来源，且不包含来源、scope、ID 或 revision。
    """

    if (
        isinstance(max_total_tokens, bool)
        or not isinstance(max_total_tokens, int)
        or isinstance(max_labels, bool)
        or not isinstance(max_labels, int)
        or max_labels < 1
        or isinstance(max_chars, bool)
        or not isinstance(max_chars, int)
        or max_chars < 1
    ):
        return RenderedLabels()
    try:
        values = tuple(labels)
    except TypeError:
        return RenderedLabels()

    valid_labels: list[str] = []
    reason_codes: list[str] = []
    rejected_count = 0
    seen: set[str] = set()
    for value in values:
        provenance = None
        if hasattr(value, "safe_label"):
            provenance = getattr(value, "source_provenance_complete", None)
            value = getattr(value, "safe_label")
        if require_source_provenance and provenance is not True:
            continue
        reason = _label_reason(value)
        if reason is not None:
            rejected_count += 1
            reason_codes.append(reason)
            continue
        label = normalize_topic_label(value)
        key = normalize_topic_key(label)
        if label is None or key is None or key in seen:
            continue
        seen.add(key)
        valid_labels.append(label)

    if not valid_labels:
        return RenderedLabels(
            rejected_count=rejected_count,
            reason_codes=tuple(reason_codes),
        )

    lines = ["**历史话题候选**（可选参考，不是当前窗口事实来源）：", ""]
    current_tokens = _estimate_token_count("\n".join(lines))
    final_labels: list[str] = []
    truncated_count = 0
    total_chars = 0
    for label in valid_labels:
        item = f"- {json.dumps(label, ensure_ascii=False)}"
        if (
            len(final_labels) >= max_labels
            or total_chars + len(item) > max_chars
            or (
                max_total_tokens > 0
                and current_tokens + _estimate_token_count(item) > max_total_tokens
            )
        ):
            truncated_count += 1
            reason_codes.append("label_budget_exceeded")
            break
        lines.append(item)
        final_labels.append(label)
        total_chars += len(item)
        current_tokens += _estimate_token_count(item)

    if not final_labels:
        return RenderedLabels(
            rejected_count=rejected_count,
            truncated_count=truncated_count,
            reason_codes=tuple(reason_codes),
        )

    rendered_block = "\n".join(lines)
    return RenderedLabels(
        rendered_block=rendered_block,
        valid_count=len(final_labels),
        rejected_count=rejected_count,
        truncated_count=truncated_count,
        estimated_tokens=_estimate_token_count(rendered_block),
        reason_codes=tuple(reason_codes),
    )


def validate_label_config(max_total_tokens: int | None) -> int:
    """校验并返回有效的话题标签 token 预算。"""

    if (
        isinstance(max_total_tokens, bool)
        or not isinstance(max_total_tokens, int)
        or max_total_tokens <= 0
    ):
        raise ValueError(f"max_total_tokens 必须为正整数，当前: {max_total_tokens}")
    if max_total_tokens > 2_000:
        raise ValueError(f"max_total_tokens 不得超过 2000，当前: {max_total_tokens}")
    return max_total_tokens


__all__ = [
    "RenderedLabels",
    "normalize_topic_key",
    "normalize_topic_label",
    "render_topic_labels",
    "validate_label_config",
]
