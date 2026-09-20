"""语义近重复阈值的离线校准证据门：脱敏聚合、预注册网格与 fail-closed canary。

输入只接受授权/隔离的匿名 fixture（JSON），输出只包含聚合计数、比率、闭集
状态与推荐阈值：正文、query、scope、ID、revision 与 source mapping 都不得
进入报告。`scripts/benchmark_memory_dedup_semantic.py` 只读 fixture，并且只在
显式 `--output` 时写文件；本模块不触碰生产数据库、不修改配置、不自动开启
enforce。

阈值网格与证据下限是预注册常量：网格用于一次性比较，不允许按结果挑选；
样本或分数不足时返回 ``insufficient_evidence``，而不是给出看似通过的推荐。
分数来自授权离线运行（fixture 内的 ``semantic_score``），因此本模块不需要
AstrBot 运行时，也不需要 Provider。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final, Literal, TypeGuard

# 预注册阈值网格：与设计/报告口径一致，不得按结果临时增删。
SEMANTIC_THRESHOLD_GRID: Final = (0.80, 0.85, 0.88, 0.90, 0.92, 0.95)
REPORT_SCHEMA_VERSION: Final = 1
REPORT_NAME: Final = "memory_dedup_semantic_calibration"

# 证据下限：不足时不给推荐阈值（宁可 unavailable 也不给假结论）。
MIN_SCORED_SAMPLES: Final = 20
MIN_DUPLICATE_SAMPLES: Final = 5
MIN_DISTINCT_SAMPLES: Final = 5
MIN_PRECISION: Final = 0.90
MIN_RECALL: Final = 0.80

# 单条文本长度护栏：超长输入的 fixture 视为坏输入，避免把整段对话当样本。
MAX_TEXT_CHARS: Final = 4000
MAX_CANDIDATES_PER_SAMPLE: Final = 20
MAX_SAMPLES: Final = 5000
MAX_REPORT_STRING_CHARS: Final = 120

# 报告键白名单（递归）：新字段必须先在这里登记，否则 canary fail-closed。
REPORT_ALLOWED_KEYS: Final = frozenset(
    {
        "schema_version",
        "report",
        "generated_at_ms",
        "capability",
        "grid",
        "samples",
        "total",
        "scored",
        "unavailable",
        "duplicate",
        "distinct",
        "thresholds",
        "threshold",
        "true_positive",
        "false_positive",
        "true_negative",
        "false_negative",
        "precision",
        "recall",
        "top1_rate",
        "gate",
        "status",
        "reason",
        "recommended_threshold",
        "privacy_canary",
        "checked_values",
        "violations",
    }
)
# 禁止出现的键名标记：即使未来误加进白名单也会被独立拦截。
FORBIDDEN_KEY_MARKERS: Final = (
    "scope",
    "session",
    "persona",
    "participant",
    "content",
    "query",
    "text",
    "memory_id",
    "doc_id",
    "revision",
    "source_mapping",
    "message_id",
    "user_id",
    "idempotency",
)

_FIXTURE_KEYS: Final = frozenset({"schema_version", "samples"})
_SAMPLE_KEYS: Final = frozenset(
    {"label", "query", "candidates", "duplicate_candidate_index"}
)
_CANDIDATE_KEYS: Final = frozenset({"content", "semantic_score"})
# 稳定失败原因闭集：CLI 与测试只接受这里的取值。
LOAD_REASONS: Final = (
    "fixture_not_object",
    "unsupported_schema_version",
    "samples_invalid",
    "samples_out_of_range",
    "sample_not_object",
    "sample_invalid_label",
    "sample_text_invalid",
    "candidates_invalid",
    "candidate_invalid",
    "candidate_score_invalid",
    "duplicate_index_missing",
    "duplicate_index_unexpected",
    "duplicate_index_invalid",
    "unknown_key",
)


@dataclass(frozen=True, slots=True)
class CalibrationCandidate:
    """匿名 fixture 中的一条既有 canonical 投影。"""

    content: str
    semantic_score: float | None


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    """一条待判定样本：query 是否与候选中的某一条同事实。"""

    label: Literal["duplicate", "distinct"]
    query: str
    candidates: tuple[CalibrationCandidate, ...]
    duplicate_index: int | None


def load_calibration_fixture(
    payload: Any,
) -> tuple[tuple[CalibrationSample, ...], str]:
    """严格解码匿名 fixture；返回 ``(samples, reason)``。

    失败时 samples 为空且 reason 是 ``LOAD_REASONS`` 中的稳定码：调用方据此
    fail-closed，而不是把坏输入当成空样本继续出报告。
    """

    if not isinstance(payload, dict):
        return (), "fixture_not_object"
    if any(key not in _FIXTURE_KEYS for key in payload):
        return (), "unknown_key"
    if payload.get("schema_version") != REPORT_SCHEMA_VERSION:
        return (), "unsupported_schema_version"
    raw_samples = payload.get("samples")
    if not isinstance(raw_samples, list):
        return (), "samples_invalid"
    if not 1 <= len(raw_samples) <= MAX_SAMPLES:
        return (), "samples_out_of_range"
    samples: list[CalibrationSample] = []
    for raw_sample in raw_samples:
        sample, reason = _decode_sample(raw_sample)
        if sample is None:
            return (), reason
        samples.append(sample)
    return tuple(samples), ""


def build_calibration_report(
    samples: tuple[CalibrationSample, ...],
    *,
    grid: tuple[float, ...] = SEMANTIC_THRESHOLD_GRID,
    now_ms: int = 0,
    forbidden_values: tuple[str, ...] = (),
) -> dict[str, Any]:
    """按预注册网格聚合成脱敏报告，并就地执行 canary 计数。

    ``forbidden_values`` 通常是 fixture 的全部字符串值；报告返回前必须满足
    键白名单与值不泄露，违规数写入 ``privacy_canary.violations``，由调用方
    fail-closed（本函数不抛异常，保证违规也能被记录与断言）。
    """

    scored, unavailable = _split_scored(samples)
    duplicate = [item for item in scored if item.label == "duplicate"]
    distinct = [item for item in scored if item.label == "distinct"]
    thresholds = _threshold_rows(scored, grid)
    status, reason, recommended = _evaluate_gate(
        scored_count=len(scored),
        duplicate_count=len(duplicate),
        distinct_count=len(distinct),
        thresholds=thresholds,
    )
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report": REPORT_NAME,
        "generated_at_ms": now_ms,
        "capability": "fixture_scores" if scored else "unavailable",
        "grid": list(grid),
        "samples": {
            "total": len(samples),
            "scored": len(scored),
            "unavailable": len(unavailable),
            "duplicate": len(duplicate),
            "distinct": len(distinct),
        },
        "thresholds": thresholds,
        "top1_rate": _top1_rate(duplicate),
        "gate": {"status": status, "reason": reason},
        "recommended_threshold": recommended,
        "privacy_canary": {
            "checked_values": len(forbidden_values),
            "violations": 0,
        },
    }
    violations = check_report_privacy(report, forbidden_values)
    report["privacy_canary"] = {
        "checked_values": len(forbidden_values),
        "violations": len(violations),
    }
    return report


def check_report_privacy(
    report: Any,
    forbidden_values: tuple[str, ...],
) -> list[str]:
    """递归检查报告键白名单与值泄露；返回违规路径（不包含违规值本身）。"""

    violations: list[str] = []
    _walk_report(report, path="report", forbidden=forbidden_values, out=violations)
    return violations


def forbidden_values_from_payload(payload: Any) -> tuple[str, ...]:
    """从原始 fixture 提取必须永不出现的值（正文与 query）。"""

    found: list[str] = []
    _collect_text_values(payload, out=found)
    unique = {value for value in found if value}
    return tuple(sorted(unique))


def _decode_sample(raw_sample: Any) -> tuple[CalibrationSample | None, str]:
    """解码单条样本；返回 ``(sample, reason)``，失败时 sample 为 ``None``。"""

    if not isinstance(raw_sample, dict):
        return None, "sample_not_object"
    if any(key not in _SAMPLE_KEYS for key in raw_sample):
        return None, "unknown_key"
    label = raw_sample.get("label")
    if label not in ("duplicate", "distinct"):
        return None, "sample_invalid_label"
    query = raw_sample.get("query")
    if not _valid_text(query):
        return None, "sample_text_invalid"
    raw_candidates = raw_sample.get("candidates")
    if (
        not isinstance(raw_candidates, list)
        or not 1 <= len(raw_candidates) <= MAX_CANDIDATES_PER_SAMPLE
    ):
        return None, "candidates_invalid"
    candidates: list[CalibrationCandidate] = []
    for raw_candidate in raw_candidates:
        candidate, reason = _decode_candidate(raw_candidate)
        if candidate is None:
            return None, reason
        candidates.append(candidate)
    duplicate_index = raw_sample.get("duplicate_candidate_index")
    if duplicate_index is not None and (
        isinstance(duplicate_index, bool)
        or not isinstance(duplicate_index, int)
        or not 0 <= duplicate_index < len(candidates)
    ):
        return None, "duplicate_index_invalid"
    if duplicate_index is None and label == "duplicate":
        return None, "duplicate_index_missing"
    if duplicate_index is not None and label != "duplicate":
        return None, "duplicate_index_unexpected"
    return (
        CalibrationSample(
            label=label,
            query=query,
            candidates=tuple(candidates),
            duplicate_index=duplicate_index,
        ),
        "",
    )


def _decode_candidate(raw_candidate: Any) -> tuple[CalibrationCandidate | None, str]:
    """解码单条候选；缺失分数表示该候选不可用。"""

    if not isinstance(raw_candidate, dict):
        return None, "candidate_invalid"
    if any(key not in _CANDIDATE_KEYS for key in raw_candidate):
        return None, "unknown_key"
    content = raw_candidate.get("content")
    if not _valid_text(content):
        return None, "sample_text_invalid"
    score = raw_candidate.get("semantic_score")
    if score is not None:
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return None, "candidate_score_invalid"
        if not math.isfinite(float(score)) or not 0.0 <= float(score) <= 1.0:
            return None, "candidate_score_invalid"
        score = float(score)
    return CalibrationCandidate(content=content, semantic_score=score), ""


def _valid_text(value: Any) -> TypeGuard[str]:
    """文本必须是长度受限的非空字符串。"""

    return (
        isinstance(value, str) and bool(value.strip()) and len(value) <= MAX_TEXT_CHARS
    )


def _split_scored(
    samples: tuple[CalibrationSample, ...],
) -> tuple[list[CalibrationSample], list[CalibrationSample]]:
    """按是否含可用分数拆分样本；无分数的样本不进入混淆矩阵。"""

    scored: list[CalibrationSample] = []
    unavailable: list[CalibrationSample] = []
    for sample in samples:
        if any(candidate.semantic_score is not None for candidate in sample.candidates):
            scored.append(sample)
        else:
            unavailable.append(sample)
    return scored, unavailable


def _threshold_rows(
    scored: list[CalibrationSample],
    grid: tuple[float, ...],
) -> list[dict[str, Any]]:
    """按网格逐阈值计算混淆矩阵与精确率/召回率。"""

    rows: list[dict[str, Any]] = []
    for threshold in grid:
        true_positive = false_positive = true_negative = false_negative = 0
        for sample in scored:
            predicted = _predict_duplicate(sample, threshold)
            actual = sample.label == "duplicate"
            if actual and predicted:
                true_positive += 1
            elif actual:
                false_negative += 1
            elif predicted:
                false_positive += 1
            else:
                true_negative += 1
        rows.append(
            {
                "threshold": threshold,
                "true_positive": true_positive,
                "false_positive": false_positive,
                "true_negative": true_negative,
                "false_negative": false_negative,
                "precision": _safe_ratio(true_positive, true_positive + false_positive),
                "recall": _safe_ratio(true_positive, true_positive + false_negative),
            }
        )
    return rows


def _predict_duplicate(sample: CalibrationSample, threshold: float) -> bool:
    """样本在给定阈值下是否被判为重复。"""

    return any(
        candidate.semantic_score is not None and candidate.semantic_score >= threshold
        for candidate in sample.candidates
    )


def _evaluate_gate(
    *,
    scored_count: int,
    duplicate_count: int,
    distinct_count: int,
    thresholds: list[dict[str, Any]],
) -> tuple[str, str, float | None]:
    """评估证据门，返回 ``(status, reason, recommended_threshold)``。"""

    if (
        scored_count < MIN_SCORED_SAMPLES
        or duplicate_count < MIN_DUPLICATE_SAMPLES
        or distinct_count < MIN_DISTINCT_SAMPLES
    ):
        return "insufficient_evidence", "sample_floor_not_met", None
    passing = [
        row["threshold"]
        for row in thresholds
        if row["precision"] >= MIN_PRECISION and row["recall"] >= MIN_RECALL
    ]
    if not passing:
        return "reject", "precision_or_recall_below_floor", None
    # 通过护栏的阈值中取最保守（最高）者，优先减少误合并。
    return "pass", "precision_and_recall_floor_met", max(passing)


def _top1_rate(duplicate_samples: list[CalibrationSample]) -> float | None:
    """期望候选是否排名第一；无样本时返回缺失而不是 0。"""

    evaluated = 0
    matched = 0
    for sample in duplicate_samples:
        scored_candidates = [
            (candidate.semantic_score, index)
            for index, candidate in enumerate(sample.candidates)
            if candidate.semantic_score is not None
        ]
        if not scored_candidates or sample.duplicate_index is None:
            continue
        evaluated += 1
        best_score = max(score for score, _ in scored_candidates)
        best_index = min(
            index for score, index in scored_candidates if score == best_score
        )
        if best_index == sample.duplicate_index:
            matched += 1
    if not evaluated:
        return None
    return matched / evaluated


def _safe_ratio(numerator: int, denominator: int) -> float:
    """分母非正时返回 0.0，避免 NaN 进入报告。"""

    return numerator / denominator if denominator > 0 else 0.0


def _walk_report(
    node: Any,
    *,
    path: str,
    forbidden: tuple[str, ...],
    out: list[str],
) -> None:
    """递归校验字典键与字符串值；违规只记录路径。"""

    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                out.append(f"{path}:key_not_allowed")
                continue
            if any(marker in key for marker in FORBIDDEN_KEY_MARKERS):
                out.append(f"{path}.{key}:forbidden_key_marker")
            if key not in REPORT_ALLOWED_KEYS:
                out.append(f"{path}.{key}:key_not_allowed")
            _walk_report(value, path=f"{path}.{key}", forbidden=forbidden, out=out)
        return
    if isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            _walk_report(item, path=f"{path}[{index}]", forbidden=forbidden, out=out)
        return
    if isinstance(node, str):
        if len(node) > MAX_REPORT_STRING_CHARS:
            out.append(f"{path}:value_too_long")
        for token in forbidden:
            if token and (token in node or node in token):
                out.append(f"{path}:value_leak")
                break


def _collect_text_values(node: Any, *, out: list[str]) -> None:
    """收集 fixture 中的所有字符串值作为 canary 值域。"""

    if isinstance(node, dict):
        for value in node.values():
            _collect_text_values(value, out=out)
        return
    if isinstance(node, (list, tuple)):
        for item in node:
            _collect_text_values(item, out=out)
        return
    if isinstance(node, str):
        out.append(node)


__all__ = [
    "CalibrationCandidate",
    "CalibrationSample",
    "FORBIDDEN_KEY_MARKERS",
    "LOAD_REASONS",
    "MAX_TEXT_CHARS",
    "MIN_DISTINCT_SAMPLES",
    "MIN_DUPLICATE_SAMPLES",
    "MIN_PRECISION",
    "MIN_RECALL",
    "MIN_SCORED_SAMPLES",
    "REPORT_ALLOWED_KEYS",
    "REPORT_NAME",
    "REPORT_SCHEMA_VERSION",
    "SEMANTIC_THRESHOLD_GRID",
    "build_calibration_report",
    "check_report_privacy",
    "forbidden_values_from_payload",
    "load_calibration_fixture",
]
