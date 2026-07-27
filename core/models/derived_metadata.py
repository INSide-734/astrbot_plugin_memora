"""有限、可重建且带 canonical 来源证据的派生元数据契约。"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Mapping

DERIVED_METADATA_SCHEMA_VERSION = "v1"
DERIVED_METADATA_REASON_CODES = frozenset(
    {
        "annotation_accepted",
        "annotation_schema_rejected",
        "annotation_budget_rejected",
        "annotation_prompt_like_rejected",
    }
)
CONTEXT_LABELS = frozenset(
    {"preference", "event", "plan", "relationship", "temporal", "emotional", "location"}
)
_BIDI_CONTROLS = frozenset(
    {
        "\u061c",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    }
)
_URL_OR_EMAIL = re.compile(r"(?:https?://|www\.|\b[^\s@]+@[^\s@]+\.[^\s@]+)", re.I)
_LONG_NUMBER = re.compile(r"\d{8,}")
_PROMPT_MARKER = re.compile(
    r"(?:```|<\/?(?:system|assistant|user|instruction)[^>]*>|\b(?:system|assistant|user)\s*:)",
    re.I,
)
_DIRECTIVE_PHRASES = (
    "ignore previous",
    "忽略之前",
    "忽略上文",
    "执行命令",
    "请输出",
    "不要遵守",
)


@dataclass(frozen=True, slots=True)
class DerivedMetadataSourceRef:
    """描述派生注解对应的 canonical source 快照。"""

    memory_id: int
    revision_token: str
    trusted_scope: str
    privacy_level: str
    source_role: str
    valid_from: str | None = None
    valid_to: str | None = None
    schema_version: str = DERIVED_METADATA_SCHEMA_VERSION
    extractor_version: str = "extractor-v1"

    def __post_init__(self) -> None:
        """校验 source identity、可见性边界和固定版本字段。"""

        if (
            isinstance(self.memory_id, bool)
            or not isinstance(self.memory_id, int)
            or self.memory_id <= 0
        ):
            raise ValueError("source_memory_id_invalid")
        for value, reason in (
            (self.revision_token, "source_revision_invalid"),
            (self.trusted_scope, "source_scope_invalid"),
            (self.privacy_level, "source_privacy_invalid"),
            (self.source_role, "source_role_invalid"),
            (self.schema_version, "source_schema_version_invalid"),
            (self.extractor_version, "source_extractor_version_invalid"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(reason)


@dataclass(frozen=True, slots=True)
class DerivedMetadataBudget:
    """首版派生元数据的字段、字符和序列化预算。"""

    max_keywords: int = 8
    max_topic_tags: int = 6
    max_context_labels: int = 6
    max_item_chars: int = 32
    max_topic_chars: int = 24
    max_total_items: int = 16
    max_total_chars: int = 256
    max_json_bytes: int = 1024

    def __post_init__(self) -> None:
        """确保所有预算都是正整数，避免无意义的负预算状态。"""

        for value in (
            self.max_keywords,
            self.max_topic_tags,
            self.max_context_labels,
            self.max_item_chars,
            self.max_topic_chars,
            self.max_total_items,
            self.max_total_chars,
            self.max_json_bytes,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("derived_metadata_budget_invalid")


@dataclass(frozen=True, slots=True)
class DerivedMetadataProposal:
    """不可信提取结果；通过 validator 前不得进入任何索引。"""

    source: DerivedMetadataSourceRef
    keywords: tuple[str, ...] = field(default_factory=tuple)
    topic_tags: tuple[str, ...] = field(default_factory=tuple)
    context_labels: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DerivedMetadataAnnotation:
    """通过预算和内容安全校验后的规范化派生注解。"""

    source: DerivedMetadataSourceRef
    keywords: tuple[str, ...]
    topic_tags: tuple[str, ...]
    context_labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DerivedMetadataValidationResult:
    """validator 的安全结果，不回显 proposal 原文。"""

    accepted: bool
    reason_code: str
    annotation: DerivedMetadataAnnotation | None = None
    total_items: int = 0
    total_chars: int = 0
    json_bytes: int = 0


def validate_derived_metadata_proposal(
    proposal: DerivedMetadataProposal | Mapping[str, Any],
    budget: DerivedMetadataBudget | None = None,
) -> DerivedMetadataValidationResult:
    """规范化并验证派生提案，任何失败都返回固定 reason code。

    处理顺序固定为“结构校验、内容规范化与去重、单字段预算、整体预算”，
    这样不会先截断再改变提案语义，也不会让字段堆叠绕过总预算。
    """

    active_budget = budget or DerivedMetadataBudget()
    try:
        candidate = _coerce_proposal(proposal)
    except (TypeError, ValueError):
        return DerivedMetadataValidationResult(False, "annotation_schema_rejected")

    normalized: dict[str, tuple[str, ...]] = {}
    try:
        for field_name, values, item_limit, char_limit in (
            (
                "keywords",
                candidate.keywords,
                active_budget.max_keywords,
                active_budget.max_item_chars,
            ),
            (
                "topic_tags",
                candidate.topic_tags,
                active_budget.max_topic_tags,
                active_budget.max_topic_chars,
            ),
        ):
            if not isinstance(values, (tuple, list)):
                return DerivedMetadataValidationResult(
                    False, "annotation_schema_rejected"
                )
            if len(values) > item_limit:
                return DerivedMetadataValidationResult(
                    False, "annotation_budget_rejected"
                )
            normalized[field_name] = _normalize_terms(values, char_limit)
        labels = candidate.context_labels
        if (
            not isinstance(labels, (tuple, list))
            or len(labels) > active_budget.max_context_labels
        ):
            return DerivedMetadataValidationResult(False, "annotation_budget_rejected")
        normalized["context_labels"] = _normalize_labels(labels)
    except _PromptLikeValue:
        return DerivedMetadataValidationResult(False, "annotation_prompt_like_rejected")
    except (TypeError, ValueError):
        return DerivedMetadataValidationResult(False, "annotation_schema_rejected")

    normalized = _deduplicate_fields(normalized)
    total_values = _all_values(normalized)
    total_items = len(total_values)
    total_chars = sum(len(value) for value in total_values)
    payload = {
        "keywords": normalized["keywords"],
        "topic_tags": normalized["topic_tags"],
        "context_labels": normalized["context_labels"],
    }
    json_bytes = len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    if (
        total_items > active_budget.max_total_items
        or total_chars > active_budget.max_total_chars
        or json_bytes > active_budget.max_json_bytes
    ):
        return DerivedMetadataValidationResult(
            False,
            "annotation_budget_rejected",
            total_items=total_items,
            total_chars=total_chars,
            json_bytes=json_bytes,
        )
    annotation = DerivedMetadataAnnotation(
        source=candidate.source,
        keywords=normalized["keywords"],
        topic_tags=normalized["topic_tags"],
        context_labels=normalized["context_labels"],
    )
    return DerivedMetadataValidationResult(
        True,
        "annotation_accepted",
        annotation,
        total_items,
        total_chars,
        json_bytes,
    )


class _PromptLikeValue(ValueError):
    """内部标记，用于区分内容安全拒绝和普通 schema 错误。"""


def _coerce_proposal(
    proposal: DerivedMetadataProposal | Mapping[str, Any],
) -> DerivedMetadataProposal:
    """将 dataclass 或严格字段映射转换为提案对象。"""

    if isinstance(proposal, DerivedMetadataProposal):
        return proposal
    if not isinstance(proposal, Mapping):
        raise TypeError("proposal_type_invalid")
    allowed = {"source", "keywords", "topic_tags", "context_labels"}
    if set(proposal) - allowed:
        raise ValueError("proposal_field_unknown")
    source = proposal.get("source")
    if not isinstance(source, DerivedMetadataSourceRef):
        raise ValueError("proposal_source_invalid")
    return DerivedMetadataProposal(
        source=source,
        keywords=proposal.get("keywords", ()),
        topic_tags=proposal.get("topic_tags", ()),
        context_labels=proposal.get("context_labels", ()),
    )


def _normalize_terms(
    values: tuple[str, ...] | list[str], item_limit: int
) -> tuple[str, ...]:
    """规范化词法字段并按跨字段比较键稳定去重。"""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_value(value, item_limit)
        key = normalized.casefold()
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    return tuple(result)


def _normalize_labels(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """校验固定 context label 枚举并稳定去重。"""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError("context_label_type_invalid")
        normalized = value.strip().casefold()
        if normalized not in CONTEXT_LABELS:
            raise ValueError("context_label_unknown")
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


def _normalize_value(value: Any, max_chars: int) -> str:
    """执行 NFKC、空白折叠和内容安全检查。"""

    if not isinstance(value, str):
        raise TypeError("metadata_value_type_invalid")
    if any(
        unicodedata.category(char).startswith("C") or char in _BIDI_CONTROLS
        for char in value
    ):
        raise _PromptLikeValue("metadata_control_character")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized or len(normalized) > max_chars:
        raise ValueError("metadata_value_length_invalid")
    if _URL_OR_EMAIL.search(normalized) or _LONG_NUMBER.search(normalized):
        raise _PromptLikeValue("metadata_sensitive_shape")
    if _PROMPT_MARKER.search(normalized) or any(
        phrase in normalized.casefold() for phrase in _DIRECTIVE_PHRASES
    ):
        raise _PromptLikeValue("metadata_prompt_like")
    return normalized


def _all_values(normalized: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    """以字段顺序合并值，供总预算和字节预算计算。"""

    return tuple(
        value
        for field_name in ("keywords", "topic_tags", "context_labels")
        for value in normalized[field_name]
    )


def _deduplicate_fields(
    normalized: Mapping[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    """跨 keywords 与 topic_tags 去重，避免同义标签叠加预算和信号。"""

    seen: set[str] = set()
    result: dict[str, tuple[str, ...]] = {}
    for field_name in ("keywords", "topic_tags", "context_labels"):
        values: list[str] = []
        for value in normalized[field_name]:
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                values.append(value)
        result[field_name] = tuple(values)
    return result


__all__ = [
    "CONTEXT_LABELS",
    "DERIVED_METADATA_REASON_CODES",
    "DERIVED_METADATA_SCHEMA_VERSION",
    "DerivedMetadataAnnotation",
    "DerivedMetadataBudget",
    "DerivedMetadataProposal",
    "DerivedMetadataSourceRef",
    "DerivedMetadataValidationResult",
    "validate_derived_metadata_proposal",
]
