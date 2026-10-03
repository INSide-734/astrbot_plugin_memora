"""质量闭环的阶段闭集、指标束与责任阶段归因。

固定阶段集合为 ``write``、``source``、``recall``、``injection``、
``lifecycle``、``expression``。每阶段携带三态状态（available/degraded/
unavailable）、闭集 reason 与可空指标；未测量的指标保持 ``None``，真实
测得的零保留 ``0``，不得互相伪装。失败/降级必须归因到唯一责任阶段。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

QUALITY_LOOP_STAGES: tuple[str, ...] = (
    "write",
    "source",
    "recall",
    "injection",
    "lifecycle",
    "expression",
)

STAGE_AVAILABLE = "available"
STAGE_DEGRADED = "degraded"
STAGE_UNAVAILABLE = "unavailable"
QUALITY_LOOP_STAGE_STATES = (STAGE_AVAILABLE, STAGE_DEGRADED, STAGE_UNAVAILABLE)
REASON_OK = "ok"
REASON_NO_ANNOTATION = "no_annotation"
REASON_NO_PAIR = "no_paired_fixture"
REASON_ENGINE_MISSING = "engine_missing"
REASON_RETRIEVAL_FAILED = "retrieval_failed"
REASON_INJECTION_PORT_MISSING = "injection_port_missing"
REASON_LIFECYCLE_PORT_MISSING = "lifecycle_port_missing"
REASON_CANARY = "privacy_canary_failed"
REASON_PAIR_INCOMPLETE = "pair_incomplete"
REASON_PAIR_DUPLICATE = "pair_duplicate"
REASON_CONTEXT_KEY_MISSING = "context_key_missing"
REASON_SNAPSHOT_UNAVAILABLE = "db_snapshot_unavailable"
REASON_HMAC_SECRET_UNAVAILABLE = "hmac_secret_unavailable"

QUALITY_LOOP_STAGE_REASONS: frozenset[str] = frozenset(
    {
        REASON_OK,
        REASON_NO_ANNOTATION,
        REASON_NO_PAIR,
        REASON_ENGINE_MISSING,
        REASON_RETRIEVAL_FAILED,
        REASON_INJECTION_PORT_MISSING,
        REASON_LIFECYCLE_PORT_MISSING,
        REASON_CANARY,
        REASON_PAIR_INCOMPLETE,
        REASON_PAIR_DUPLICATE,
        REASON_CONTEXT_KEY_MISSING,
        REASON_SNAPSHOT_UNAVAILABLE,
        REASON_HMAC_SECRET_UNAVAILABLE,
    }
)

#: 每个失败/降级 reason 的唯一责任阶段（闭集映射）。
REASON_TO_OWNING_STAGE: dict[str, str] = {
    REASON_NO_ANNOTATION: "write",
    REASON_NO_PAIR: "recall",
    REASON_ENGINE_MISSING: "recall",
    REASON_RETRIEVAL_FAILED: "recall",
    REASON_INJECTION_PORT_MISSING: "injection",
    REASON_LIFECYCLE_PORT_MISSING: "lifecycle",
    REASON_CANARY: "source",
    REASON_PAIR_INCOMPLETE: "write",
    REASON_PAIR_DUPLICATE: "write",
    REASON_CONTEXT_KEY_MISSING: "write",
    REASON_SNAPSHOT_UNAVAILABLE: "source",
    REASON_HMAC_SECRET_UNAVAILABLE: "source",
}

#: 每阶段允许的指标键；全部可空。
QUALITY_LOOP_STAGE_METRIC_KEYS: dict[str, tuple[str, ...]] = {
    "write": ("write_fact_correctness",),
    "source": ("source_faithfulness", "source_evidence_completeness"),
    "recall": ("candidate_hit_rate",),
    "injection": (
        "final_injected_hit_rate",
        "negative_injection_rate",
        "configured_budget_chars",
        "effective_budget_chars",
    ),
    "lifecycle": (
        "retrieved_count",
        "injected_count",
        "observed_p50_latency_ms",
        "observed_p95_latency_ms",
    ),
    "expression": (
        "annotated_answer_faithfulness",
        "annotated_answer_relevancy",
    ),
}

_SAFE_STAGE_METRICS: dict[str, frozenset[str]] = {
    stage: frozenset(keys) for stage, keys in QUALITY_LOOP_STAGE_METRIC_KEYS.items()
}


def _finite_metric(value: Any) -> float | None:
    """只接受有限非负数值；布尔与非法值按未测量处理。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


@dataclass(frozen=True, slots=True)
class QualityLoopStageRead:
    """单个阶段的闭集读取结果；指标可空。"""

    stage: str
    state: str
    reason: str
    metrics: Mapping[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QualityLoopPairOutcome:
    """一对同上下文盲测的结果摘要（不含 query 与标签原文）。"""

    context_key_hash: str
    slot_order: tuple[str, str]
    should_use_case_hash: str
    should_silence_case_hash: str
    should_use_hit: bool | None
    should_silence_correct: bool | None


def make_stage_read(
    stage: str,
    *,
    state: str,
    reason: str,
    metrics: Mapping[str, float | None] | None = None,
) -> QualityLoopStageRead:
    """构造阶段读取结果，过滤掉闭集外的指标键。"""

    if stage not in QUALITY_LOOP_STAGES:
        raise ValueError("quality_loop_stage_invalid")
    if state not in QUALITY_LOOP_STAGE_STATES:
        raise ValueError("quality_loop_stage_state_invalid")
    if reason not in QUALITY_LOOP_STAGE_REASONS:
        raise ValueError("quality_loop_stage_reason_invalid")
    allowed = _SAFE_STAGE_METRICS[stage]
    normalized: dict[str, float | None] = {}
    for key, value in (metrics or {}).items():
        if key in allowed:
            normalized[key] = _finite_metric(value)
    return QualityLoopStageRead(
        stage=stage,
        state=state,
        reason=reason,
        metrics=normalized,
    )


def owning_stage_for_reason(reason: str) -> str | None:
    """返回责任阶段；未知 reason 无归因（fail-closed，不猜）。"""
    return REASON_TO_OWNING_STAGE.get(reason)


def _owning_stage_for_payload(stage: str, reason: str) -> str | None:
    """阶段通用的无标注原因归属于当前阶段，其余原因使用闭集映射。"""
    if reason == REASON_NO_ANNOTATION:
        return stage
    return owning_stage_for_reason(reason)


def stage_read_to_payload(read: QualityLoopStageRead) -> dict[str, Any]:
    """把阶段读取结果序列化为报告安全载荷。"""
    return {
        "stage": read.stage,
        "state": read.state,
        "reason": read.reason,
        "owning_stage": _owning_stage_for_payload(read.stage, read.reason),
        "metrics": dict(read.metrics),
    }


def sanitize_stage_payload(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """读取旧报告时重新 sanitize 阶段载荷；结构非法返回 None。"""

    stage = payload.get("stage")
    state = payload.get("state")
    reason = payload.get("reason")
    if stage not in QUALITY_LOOP_STAGES:
        return None
    if state not in QUALITY_LOOP_STAGE_STATES:
        return None
    if reason not in QUALITY_LOOP_STAGE_REASONS:
        return None
    metrics_raw = payload.get("metrics")
    allowed = _SAFE_STAGE_METRICS[stage]
    metrics: dict[str, float | None] = {}
    if isinstance(metrics_raw, Mapping):
        for key, value in metrics_raw.items():
            key_text = str(key)
            if key_text in allowed:
                metrics[key_text] = _finite_metric(value)
    return {
        "stage": stage,
        "state": state,
        "reason": reason,
        "owning_stage": _owning_stage_for_payload(stage, reason),
        "metrics": metrics,
    }


__all__ = [
    "QUALITY_LOOP_STAGES",
    "QUALITY_LOOP_STAGE_METRIC_KEYS",
    "QUALITY_LOOP_STAGE_REASONS",
    "QUALITY_LOOP_STAGE_STATES",
    "QualityLoopPairOutcome",
    "QualityLoopStageRead",
    "REASON_TO_OWNING_STAGE",
    "make_stage_read",
    "REASON_HMAC_SECRET_UNAVAILABLE",
    "owning_stage_for_reason",
    "sanitize_stage_payload",
    "stage_read_to_payload",
    "STAGE_AVAILABLE",
    "STAGE_DEGRADED",
    "STAGE_UNAVAILABLE",
]
