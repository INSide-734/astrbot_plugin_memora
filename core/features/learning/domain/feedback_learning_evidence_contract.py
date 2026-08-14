"""自主学习证据的低敏版本与回归原因契约。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone

SUPPORTED_EVIDENCE_EVALUATORS = frozenset(
    {"evaluator-v1", "feedback-ranking-evidence-v1", "feedback-ranking-v2"}
)
SUPPORTED_EVIDENCE_QUALITY_GATES = frozenset({"quality-gate-v1", "quality-gate-v2"})
REQUIRED_EVIDENCE_REGRESSION_CHECKS = frozenset(
    {
        "conflict_regression",
        "date_number_regression",
        "identity_regression",
        "negation_regression",
        "privacy_regression",
        "scope_role_regression",
    }
)
ALLOWED_EVIDENCE_RETRIEVAL_STAGES = frozenset(
    {
        "candidate_generation",
        "direct_retrieval",
        "graph_expansion",
        "query_rewrite",
        "relation_expansion",
        "rerank",
        "retrieval_stage",
    }
)
ALLOWED_EVIDENCE_REGRESSION_FAILURES = frozenset(
    {
        "config_noop",
        "conflict_regression",
        "date_number_regression",
        "group_recall_gap_regression",
        "identity_regression",
        "negative_case_regression",
        "negation_regression",
        "privacy_regression",
        "privacy",
        "scope_role_regression",
    }
)


def safe_evidence_code(value: object) -> bool:
    """仅接受短 ASCII revision/code，阻止任意原文进入证据。"""

    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value.isascii()
        and all(char.isalnum() or char in "-_.:" for char in value)
    )


def plain_evidence_int(value: object) -> bool:
    """判断值是否为非布尔整数。"""

    return isinstance(value, int) and not isinstance(value, bool)


def optional_evidence_number(value: object) -> bool:
    """判断成本字段是否为普通数值或显式缺失。"""

    return value is None or (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    )


def evidence_sha256(value: object) -> bool:
    """判断值是否为规范小写 SHA-256 十六进制文本。"""

    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(char in "0123456789abcdef" for char in value)
    )


def valid_evidence_binding(
    aggregation_revision: object,
    source_config_revision: object,
    quality_gate_version: object,
) -> bool:
    """校验两个内容 revision 与固定质量门版本。"""

    return (
        evidence_sha256(aggregation_revision)
        and evidence_sha256(source_config_revision)
        and quality_gate_version in SUPPORTED_EVIDENCE_QUALITY_GATES
    )


def valid_evidence_regression_checks(value: object) -> bool:
    """校验回归检查只含固定低敏标识且不重复。"""

    return (
        isinstance(value, (list, tuple))
        and len(value) == len(set(value))
        and all(item in REQUIRED_EVIDENCE_REGRESSION_CHECKS for item in value)
    )


def complete_evidence_regression_checks(value: object) -> bool:
    """确认六项生产关键回归检查均已显式执行。"""

    return valid_evidence_regression_checks(value) and set(value) == set(
        REQUIRED_EVIDENCE_REGRESSION_CHECKS
    )


def positive_evidence_int(value: object) -> bool:
    """仅接受正整数，拒绝 Python 中可伪装为整数的布尔值。"""

    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def safe_evidence_stage_name(value: object) -> bool:
    """仅允许固定低敏标识符作为检索阶段名称。"""

    return isinstance(value, str) and value in ALLOWED_EVIDENCE_RETRIEVAL_STAGES


def canonical_evidence_json_value(value: object) -> object:
    """把冻结 evidence 值转换为稳定 JSON 兼容结构。"""

    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    raise TypeError(f"unsupported evidence value: {type(value).__name__}")


__all__ = [
    "ALLOWED_EVIDENCE_RETRIEVAL_STAGES",
    "ALLOWED_EVIDENCE_REGRESSION_FAILURES",
    "canonical_evidence_json_value",
    "complete_evidence_regression_checks",
    "evidence_sha256",
    "optional_evidence_number",
    "plain_evidence_int",
    "positive_evidence_int",
    "REQUIRED_EVIDENCE_REGRESSION_CHECKS",
    "safe_evidence_code",
    "safe_evidence_stage_name",
    "SUPPORTED_EVIDENCE_EVALUATORS",
    "SUPPORTED_EVIDENCE_QUALITY_GATES",
    "valid_evidence_binding",
    "valid_evidence_regression_checks",
]
