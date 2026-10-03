"""质量闭环 runner：同上下文成对盲测与阶段证据组装。

从受控 fixture 构建 ``should_use``/``should_silence`` 配对，按显式种子
决定盲测槽位顺序；阶段执行只读，不触碰 live engine 配置。缺测量保持
``None``，配对/隐私失败返回稳定 reason，不以下游默认值掩盖。
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.quality_loop_manifest import (
    QualityLoopReplayManifest,
    build_quality_loop_replay_manifest,
    manifest_public_summary,
    quality_loop_case_hash,
    quality_loop_context_key_hash,
    validate_quality_loop_manifest,
)
from ..domain.quality_loop_stages import (
    REASON_CANARY,
    REASON_CONTEXT_KEY_MISSING,
    REASON_ENGINE_MISSING,
    REASON_HMAC_SECRET_UNAVAILABLE,
    REASON_INJECTION_PORT_MISSING,
    REASON_LIFECYCLE_PORT_MISSING,
    REASON_NO_ANNOTATION,
    REASON_NO_PAIR,
    REASON_OK,
    REASON_PAIR_DUPLICATE,
    REASON_PAIR_INCOMPLETE,
    REASON_RETRIEVAL_FAILED,
    STAGE_AVAILABLE,
    STAGE_DEGRADED,
    STAGE_UNAVAILABLE,
    QualityLoopPairOutcome,
    QualityLoopStageRead,
    make_stage_read,
    stage_read_to_payload,
)
from .memory_dedup_evidence import forbidden_values_from_payload
from .retrieval_quality import EvaluationCase
from .topic_candidate_evidence import check_privacy_canary

_EXPECTATIONS = ("should_use", "should_silence")
_PUBLIC_CANARY_FRAGMENTS = (
    "should_use",
    "should_silence",
    "write",
    "source",
    "recall",
    "injection",
    "lifecycle",
    "expression",
    "ok",
    "available",
    "unavailable",
    "degraded",
    "no_annotation",
    "no_paired_fixture",
    "retrieval_failed",
    "engine_missing",
    "injection_port_missing",
    "lifecycle_port_missing",
    "privacy_canary_failed",
    "pair_incomplete",
    "pair_duplicate",
    "context_key_missing",
    "db_snapshot_unavailable",
    "hmac_secret_unavailable",
)


class QualityLoopPairError(ValueError):
    """配对输入不合法；code 为稳定拒绝原因。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _case_expectation(case: EvaluationCase) -> str | None:
    value = case.metadata.get("expectation")
    return value if isinstance(value, str) and value in _EXPECTATIONS else None


def _case_context_key(case: EvaluationCase) -> str | None:
    value = case.metadata.get("context_key")
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def build_quality_loop_pairs(
    cases: Sequence[EvaluationCase],
) -> tuple[list[tuple[EvaluationCase, EvaluationCase]], list[str]]:
    """按 context_key 组装 use/silence 配对，返回 (pairs, reject_reasons)。

    同一 context_key 必须恰好一条 should_use 与一条 should_silence；
    未声明配对字段的普通用例不参与配对，也不计入拒绝。已声明
    ``context_key``/``expectation`` 但重复、孤立或单侧缺失的按闭集
    reason 拒绝，拒绝不中断其余配对。
    """

    grouped: dict[str, dict[str, list[EvaluationCase]]] = {}
    rejects: list[str] = []
    for case in cases:
        context_key = _case_context_key(case)
        expectation = _case_expectation(case)
        if context_key is None and expectation is None:
            continue
        if context_key is None or expectation is None:
            rejects.append(REASON_CONTEXT_KEY_MISSING)
            continue
        bucket = grouped.setdefault(
            context_key, {"should_use": [], "should_silence": []}
        )
        bucket[expectation].append(case)

    pairs: list[tuple[EvaluationCase, EvaluationCase]] = []
    for context_key in sorted(grouped):
        bucket = grouped[context_key]
        use_cases, silence_cases = bucket["should_use"], bucket["should_silence"]
        if len(use_cases) > 1 or len(silence_cases) > 1:
            rejects.append(REASON_PAIR_DUPLICATE)
            continue
        if not use_cases or not silence_cases:
            rejects.append(REASON_PAIR_INCOMPLETE)
            continue
        pairs.append((use_cases[0], silence_cases[0]))
    return pairs, rejects


def blind_slot_order(*, seed: int, context_key: str) -> tuple[str, str]:
    """按 sha256(seed‖context_key) 决定盲测展示顺序（A 槽在前）。"""
    digest = hashlib.sha256(f"{seed}\0{context_key}".encode("utf-8")).digest()
    order: tuple[str, str] = ("should_use", "should_silence")
    if digest[0] & 0x01:
        return (order[1], order[0])
    return order


def _pair_hit(case: EvaluationCase, ranked: Sequence[str], k: int) -> bool | None:
    """should_use 命中=前 K 含相关 ID；should_silence 正确=前 K 为空。"""
    if not case.relevant_doc_ids:
        return None
    top_k = {str(item).strip() for item in ranked[:k]}
    if case.metadata.get("expectation") == "should_silence":
        return not top_k
    return bool(top_k & {str(item).strip() for item in case.relevant_doc_ids})


def _case_fingerprint(case: EvaluationCase, *, context_key_secret: bytes) -> str:
    """单条用例内容指纹（使用安装密钥并做域分离）。"""
    return quality_loop_case_hash(
        case.case_id,
        query=case.query,
        relevant=sorted(case.relevant_doc_ids),
        metadata=case.metadata,
        secret=context_key_secret,
    )


def _pair_fingerprints(
    pairs: Sequence[tuple[EvaluationCase, EvaluationCase]],
    *,
    context_key_secret: bytes,
) -> list[tuple[str, str, str]]:
    """把配对转为 manifest 指纹，并以 HMAC 隐藏上下文键。"""
    return [
        (
            quality_loop_context_key_hash(
                _case_context_key(use_case) or "", secret=context_key_secret
            ),
            _case_fingerprint(use_case, context_key_secret=context_key_secret),
            _case_fingerprint(silence_case, context_key_secret=context_key_secret),
        )
        for use_case, silence_case in pairs
    ]


def _annotation_mean(
    cases: Sequence[EvaluationCase],
    key: str,
) -> float | None:
    """求 metadata 中标注指标的均值；无任何标注时保持 None。"""
    values: list[float] = []
    for case in cases:
        raw = case.metadata.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        number = float(raw)
        if 0.0 <= number <= 1.0:
            values.append(number)
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _annotated_stage(
    stage: str,
    cases: Sequence[EvaluationCase],
    metric_keys: tuple[str, ...],
) -> QualityLoopStageRead:
    """构造基于 fixture 标注的阶段；无标注输入时 degraded+no_annotation。"""

    # metric_keys 是闭集指标名（如 annotated_answer_faithfulness 的
    # expression 阶段键保留 annotated_ 前缀）；来源 metadata 键统一按
    # annotated_ 前缀读取，避免双重前缀。
    metrics = {
        key: _annotation_mean(
            cases,
            key if key.startswith("annotated_") else f"annotated_{key}",
        )
        for key in metric_keys
    }
    has_annotation = bool(cases) and any(
        value is not None for value in metrics.values()
    )
    return make_stage_read(
        stage,
        state=STAGE_AVAILABLE if has_annotation else STAGE_DEGRADED,
        reason=REASON_OK if has_annotation else REASON_NO_ANNOTATION,
        metrics=metrics,
    )


def _ranked_doc_ids(item: Any) -> str:
    """从检索结果提取规范 doc_id 文本。"""
    if isinstance(item, Mapping):
        raw = item.get("doc_id") or item.get("id") or item.get("memory_id")
    else:
        raw = getattr(item, "doc_id", item)
    return str(raw or "").strip()


async def _recall_stage(
    pairs: Sequence[tuple[EvaluationCase, EvaluationCase]],
    *,
    retriever: Any,
    k: int,
    seed: int,
    context_key_secret: bytes,
    pair_outcomes: list[QualityLoopPairOutcome],
) -> tuple[QualityLoopStageRead, list[bool]]:
    """执行成对盲测检索并返回 recall 阶段证据。"""
    recall_hits: list[bool] = []
    state, reason = STAGE_AVAILABLE, REASON_OK
    if not pairs:
        return (
            make_stage_read(
                "recall",
                state=STAGE_DEGRADED,
                reason=REASON_NO_PAIR,
                metrics={"candidate_hit_rate": None},
            ),
            recall_hits,
        )
    if retriever is None:
        return (
            make_stage_read(
                "recall",
                state=STAGE_UNAVAILABLE,
                reason=REASON_ENGINE_MISSING,
                metrics={"candidate_hit_rate": None},
            ),
            recall_hits,
        )
    for use_case, silence_case in pairs:
        context_key = _case_context_key(use_case) or ""
        order = blind_slot_order(seed=seed, context_key=context_key)
        outcome_slots: dict[str, bool | None] = {}
        for expectation in order:
            target = use_case if expectation == "should_use" else silence_case
            try:
                retrieved = retriever(target, k)
                if hasattr(retrieved, "__await__"):
                    retrieved = await retrieved
                ranked = [_ranked_doc_ids(item) for item in list(retrieved or [])[:k]]
            except Exception:
                state, reason = STAGE_DEGRADED, REASON_RETRIEVAL_FAILED
                outcome_slots[expectation] = None
                continue
            hit = _pair_hit(target, ranked, k)
            outcome_slots[expectation] = hit
            if expectation == "should_use" and hit is not None:
                recall_hits.append(hit)
        pair_outcomes.append(
            QualityLoopPairOutcome(
                context_key_hash=quality_loop_context_key_hash(
                    context_key, secret=context_key_secret
                ),
                slot_order=order,
                should_use_case_hash=_case_fingerprint(
                    use_case, context_key_secret=context_key_secret
                ),
                should_silence_case_hash=_case_fingerprint(
                    silence_case, context_key_secret=context_key_secret
                ),
                should_use_hit=outcome_slots.get("should_use"),
                should_silence_correct=outcome_slots.get("should_silence"),
            )
        )
    hit_rate = round(sum(recall_hits) / len(recall_hits), 4) if recall_hits else None
    return (
        make_stage_read(
            "recall",
            state=state,
            reason=reason,
            metrics={"candidate_hit_rate": hit_rate},
        ),
        recall_hits,
    )


async def _injection_stage(injection_port: Any) -> QualityLoopStageRead:
    """注入阶段证据：端口缺失 unavailable；有端口时读取配对注入率。"""
    if injection_port is None or not callable(
        getattr(injection_port, "read_pair_rates", None)
    ):
        return make_stage_read(
            "injection",
            state=STAGE_UNAVAILABLE,
            reason=REASON_INJECTION_PORT_MISSING,
        )
    try:
        rates = injection_port.read_pair_rates()
        if inspect.isawaitable(rates):
            rates = await rates
        if not isinstance(rates, Mapping):
            raise TypeError("injection_rates_invalid")
    except Exception:
        return make_stage_read(
            "injection",
            state=STAGE_UNAVAILABLE,
            reason=REASON_INJECTION_PORT_MISSING,
        )
    return make_stage_read(
        "injection",
        state=STAGE_AVAILABLE,
        reason=REASON_OK,
        metrics={
            "final_injected_hit_rate": rates.get("final_injected_hit_rate"),
            "negative_injection_rate": rates.get("negative_injection_rate"),
            "configured_budget_chars": rates.get("configured_budget_chars"),
            "effective_budget_chars": rates.get("effective_budget_chars"),
        },
    )


async def _lifecycle_stage(lifecycle_port: Any) -> QualityLoopStageRead:
    """生命周期阶段证据：读取现有注入生命周期聚合端口，保持只读。"""
    if lifecycle_port is None:
        return make_stage_read(
            "lifecycle",
            state=STAGE_UNAVAILABLE,
            reason=REASON_LIFECYCLE_PORT_MISSING,
        )
    read_counts = getattr(lifecycle_port, "read_counts", None)
    if callable(read_counts):
        reader = read_counts
        reader_args: tuple[Any, ...] = ()
        reader_kwargs: dict[str, Any] = {}
    else:
        reader = getattr(lifecycle_port, "lifecycle_summary", None)
        reader_args = ()
        reader_kwargs = {"window": "24h"}
        if not callable(reader):
            reader = getattr(lifecycle_port, "summary", None)
            reader_args = ("24h",)
            reader_kwargs = {}
    if not callable(reader):
        return make_stage_read(
            "lifecycle",
            state=STAGE_UNAVAILABLE,
            reason=REASON_LIFECYCLE_PORT_MISSING,
        )
    try:
        counts = reader(*reader_args, **reader_kwargs)
        if inspect.isawaitable(counts):
            counts = await counts
        if not isinstance(counts, Mapping):
            raise TypeError("lifecycle_counts_invalid")
    except Exception:
        return make_stage_read(
            "lifecycle",
            state=STAGE_UNAVAILABLE,
            reason=REASON_LIFECYCLE_PORT_MISSING,
        )
    return make_stage_read(
        "lifecycle",
        state=STAGE_AVAILABLE,
        reason=REASON_OK,
        metrics={
            "retrieved_count": counts.get("retrieved_count"),
            "injected_count": counts.get("injected_count"),
            "observed_p50_latency_ms": counts.get("observed_p50_latency_ms"),
            "observed_p95_latency_ms": counts.get("observed_p95_latency_ms"),
        },
    )


def _privacy_canary_values(cases: Sequence[EvaluationCase]) -> tuple[str, ...]:
    """Collect sensitive fixture strings without treating labels as secrets."""
    sensitive_metadata: list[dict[str, Any]] = []
    for case in cases:
        metadata = {
            key: value for key, value in case.metadata.items() if key != "expectation"
        }
        sensitive_metadata.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "relevant_doc_ids": sorted(case.relevant_doc_ids),
                "context_key": _case_context_key(case),
                "metadata": metadata,
            }
        )
    return tuple(
        value
        for value in forbidden_values_from_payload(sensitive_metadata)
        if len(value) >= 4
    )


def check_quality_loop_payload_privacy(
    payload: Mapping[str, Any],
    cases: Sequence[EvaluationCase],
    *,
    additional_forbidden_values: Sequence[str] = (),
) -> bool:
    """检查最终公开载荷的敏感字段和值泄露。"""
    forbidden = tuple(
        sorted(
            value
            for value in set(
                _privacy_canary_values(cases) + tuple(additional_forbidden_values)
            )
            if value not in _PUBLIC_CANARY_FRAGMENTS
        )
    )
    try:
        is_safe, _ = check_privacy_canary(
            dict(payload),
            forbidden_values=forbidden,
            minimum_substring_length=16,
        )
    except Exception:
        return False
    return is_safe


async def run_quality_loop(
    cases: Sequence[EvaluationCase],
    *,
    retriever: Any,
    k: int,
    seed: int,
    code_revision: str,
    config_hash: str,
    schema_hash: str,
    context_key_secret: bytes,
    model_id: str | None = None,
    embedding_id: str | None = None,
    tokenizer_id: str | None = None,
    db_snapshot_hash: str | None = None,
    injection_port: Any = None,
    lifecycle_port: Any = None,
    additional_forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    """运行一次质量闭环，返回安全的 ``quality_loop`` 报告载荷。

    retriever 为 ``make_memory_engine_retriever(engine)`` 风格的可调用；
    injection/lifecycle 端口为可选只读计数来源，缺失时对应阶段
    unavailable，不用零值伪装。
    """

    if not isinstance(context_key_secret, bytes) or len(context_key_secret) < 32:
        raise QualityLoopPairError(REASON_HMAC_SECRET_UNAVAILABLE)
    pairs, rejects = build_quality_loop_pairs(cases)
    pair_reason_counts: dict[str, int] = {}
    for reason in rejects:
        pair_reason_counts[reason] = pair_reason_counts.get(reason, 0) + 1

    canary_safe, _ = check_privacy_canary(
        {"pair_count": len(pairs), "reject_reasons": sorted(set(rejects))}
    )
    if not canary_safe:
        raise QualityLoopPairError(REASON_CANARY)

    manifest: QualityLoopReplayManifest | None = None
    if pairs:
        fingerprints = _pair_fingerprints(pairs, context_key_secret=context_key_secret)
        manifest = build_quality_loop_replay_manifest(
            code_revision=code_revision,
            config_hash=config_hash,
            schema_hash=schema_hash,
            seed=seed,
            pair_fingerprints=fingerprints,
            k=k,
            model_id=model_id,
            embedding_id=embedding_id,
            tokenizer_id=tokenizer_id,
            db_snapshot_hash=db_snapshot_hash,
        )
        if not validate_quality_loop_manifest(
            manifest,
            expected_pairs=fingerprints,
            expected_code_revision=code_revision,
            expected_config_hash=config_hash,
            expected_schema_hash=schema_hash,
            expected_seed=seed,
            expected_k=k,
            expected_model_id=model_id,
            expected_embedding_id=embedding_id,
            expected_tokenizer_id=tokenizer_id,
            expected_db_snapshot_hash=db_snapshot_hash,
        ):
            raise QualityLoopPairError(REASON_PAIR_DUPLICATE)

    pair_outcomes: list[QualityLoopPairOutcome] = []
    recall_stage, _ = await _recall_stage(
        pairs,
        retriever=retriever,
        k=k,
        seed=seed,
        context_key_secret=context_key_secret,
        pair_outcomes=pair_outcomes,
    )
    annotated_cases = [case for pair in pairs for case in pair]

    lifecycle_stage = await _lifecycle_stage(lifecycle_port)
    injection_stage = await _injection_stage(injection_port)
    stages = [
        _annotated_stage("write", annotated_cases, ("write_fact_correctness",)),
        _annotated_stage(
            "source",
            annotated_cases,
            ("source_faithfulness", "source_evidence_completeness"),
        ),
        recall_stage,
        injection_stage,
        lifecycle_stage,
        _annotated_stage(
            "expression",
            annotated_cases,
            ("annotated_answer_faithfulness", "annotated_answer_relevancy"),
        ),
    ]

    use_hits = [
        outcome.should_use_hit
        for outcome in pair_outcomes
        if outcome.should_use_hit is not None
    ]
    silence_correct = [
        outcome.should_silence_correct
        for outcome in pair_outcomes
        if outcome.should_silence_correct is not None
    ]

    payload = {
        "manifest": manifest_public_summary(manifest) if manifest else None,
        "stages": [stage_read_to_payload(read) for read in stages],
        "pairs": {
            "total_pairs": len(pairs),
            "should_use_hit_rate": (
                round(sum(use_hits) / len(use_hits), 4) if use_hits else None
            ),
            "should_silence_correct_rate": (
                round(sum(silence_correct) / len(silence_correct), 4)
                if silence_correct
                else None
            ),
            "reject_reason_counts": dict(sorted(pair_reason_counts.items())),
        },
        "pair_outcomes": [
            {
                "context_key_hash": outcome.context_key_hash,
                "slot_order": list(outcome.slot_order),
                "should_use_case_hash": outcome.should_use_case_hash,
                "should_silence_case_hash": outcome.should_silence_case_hash,
                "should_use_hit": outcome.should_use_hit,
                "should_silence_correct": outcome.should_silence_correct,
            }
            for outcome in pair_outcomes
        ],
    }
    if not check_quality_loop_payload_privacy(
        payload, cases, additional_forbidden_values=additional_forbidden_values
    ):
        raise QualityLoopPairError(REASON_CANARY)
    return payload


__all__ = [
    "QualityLoopPairError",
    "blind_slot_order",
    "build_quality_loop_pairs",
    "run_quality_loop",
]
