"""Topic 候选复用的离线证据门：脱敏记录、配对统计与安全判定。"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Literal, TypeGuard

# 预注册 K 网格：证据门、回放和 benchmark 必须使用同一集合。
TOP_K_VALUES: tuple[int, ...] = (4, 8, 12, 16, 24)
_BASE_BOOTSTRAP_CONFIDENCE = 0.95

# 隐私 canary：这些字段不得出现在记录中
_PRIVACY_CANARY_FIELDS = frozenset(
    {
        "topic",
        "label",
        "scope",
        "scope_key",
        "query",
        "prompt",
        "canonical_id",
        "memory_id",
        "user_id",
        "participant_id",
        "identity",
    }
)


@dataclass(frozen=True, slots=True)
class CandidateReplayRecord:
    """单次回放的 allowlist 记录；隐私 canary 失败时拒绝整条记录。

    ``candidate_occurrence_count`` 和 ``misreuse_count`` 分别是人工标注
    输出 occurrence 中命中候选集合的分母与其中不满足语义一致谓词的分子；
    两者均使用计数，证据门统一转换为百分点。``provider_duration_ms``
    只接受实际 Provider instrumentation 的端到端墙钟观测，缺失保持 ``None``。

    is_negative_window 标记该记录来自独立构造的安全测试窗口
    （包含 mark_write、orphan、dormant、archived 等负向来源），
    与 safety_violation_count（真实安全事件计数）是两个正交维度。
    """

    case_hash: str
    scale_bucket: str
    chat_type: Literal["private", "group"]
    catalog_generation: int
    variant: Literal[
        "off",
        "strict_full",
        "top_k_4",
        "top_k_8",
        "top_k_12",
        "top_k_16",
        "top_k_24",
    ]
    source_window_digest: str
    candidate_count: int
    bm25_hit_count: int
    recent_fill_count: int
    identity_drop_count: int
    safety_violation_count: int
    exact_reuse_count: int | None
    exact_topic_count: int | None
    candidate_occurrence_count: int | None
    misreuse_count: int | None
    duplicate_count: int | None
    window_topic_count: int | None
    fragmentation_count: int | None
    token_availability: Literal["estimated", "unavailable"]
    estimated_tokens: int | None
    selector_duration_ms: float
    provider_duration_ms: float | None
    reason_code: str
    is_negative_window: bool = False
    catalog_topic_count: int | None = None
    execution_status: Literal["success", "degraded", "error"] = "success"


@dataclass(frozen=True, slots=True)
class EvidenceGateResult:
    """每个 (bucket, catalog_topic_count, chat_type, K) 的证据门结果。"""

    scale_bucket: str
    chat_type: str
    k: int | None  # None 表示 strict_full
    decision: Literal["accept", "reject"]
    reason_codes: tuple[str, ...]
    quality_sample_count: int
    token_sample_count: int
    latency_sample_count: int
    negative_sample_count: int
    fragmentation_ci_lower: float | None
    fragmentation_ci_upper: float | None
    token_drop_ci_lower: float | None
    token_drop_ci_upper: float | None
    provider_p95_delta_ci_lower: float | None
    provider_p95_delta_ci_upper: float | None
    misreuse_rate: float | None
    bootstrap_confidence: float
    catalog_topic_count: int | None = None


@dataclass(frozen=True, slots=True)
class EvidenceReport:
    """完整证据门报告；不含 topic/scope/query/prompt/ID。"""

    total_windows: int
    total_negative_windows: int
    bucket_results: tuple[EvidenceGateResult, ...]
    recommended_k: int | None
    e2e_provider_p95: float | None
    observe_shadow_cost: None  # 当前回放不包含 observe 成本
    bonferroni_factor: int
    bootstrap_confidence: float
    recommended_activation_threshold: int | None = None
    recommended_bucket_modes: dict[str, str] = field(default_factory=dict)


def create_replay_record(
    case_hash: str,
    scale_bucket: str,
    chat_type: str,
    catalog_generation: int,
    variant: str,
    source_window_digest: str,
    candidate_count: int,
    bm25_hit_count: int,
    recent_fill_count: int,
    identity_drop_count: int,
    safety_violation_count: int,
    exact_reuse_count: int | None,
    exact_topic_count: int | None,
    candidate_occurrence_count: int | None,
    misreuse_count: int | None,
    duplicate_count: int | None,
    window_topic_count: int | None,
    fragmentation_count: int | None,
    token_availability: str,
    estimated_tokens: int | None,
    selector_duration_ms: float,
    provider_duration_ms: float | None,
    reason_code: str,
    is_negative_window: bool = False,
    catalog_topic_count: int | None = None,
    execution_status: Literal["success", "degraded", "error"] = "success",
) -> CandidateReplayRecord | None:
    """构造 allowlist 记录；隐私 canary 失败返回 None。"""
    record_dict = {
        "case_hash": case_hash,
        "scale_bucket": scale_bucket,
        "chat_type": chat_type,
        "catalog_generation": catalog_generation,
        "variant": variant,
        "source_window_digest": source_window_digest,
        "candidate_count": candidate_count,
        "bm25_hit_count": bm25_hit_count,
        "recent_fill_count": recent_fill_count,
        "identity_drop_count": identity_drop_count,
        "safety_violation_count": safety_violation_count,
        "exact_reuse_count": exact_reuse_count,
        "exact_topic_count": exact_topic_count,
        "candidate_occurrence_count": candidate_occurrence_count,
        "misreuse_count": misreuse_count,
        "duplicate_count": duplicate_count,
        "window_topic_count": window_topic_count,
        "fragmentation_count": fragmentation_count,
        "token_availability": token_availability,
        "estimated_tokens": estimated_tokens,
        "selector_duration_ms": selector_duration_ms,
        "provider_duration_ms": provider_duration_ms,
        "reason_code": reason_code,
        "is_negative_window": is_negative_window,
        "catalog_topic_count": catalog_topic_count,
        "execution_status": execution_status,
    }

    is_safe, _ = check_privacy_canary(record_dict)
    if not is_safe:
        return None

    return CandidateReplayRecord(**record_dict)


def check_privacy_canary(record: dict[str, Any]) -> tuple[bool, list[str]]:
    """检查记录是否包含隐私 canary 字段；fail-closed。

    Returns:
        (is_safe, violations): is_safe=False 时记录被拒绝
    """
    violations = []

    def _check_value(value: Any, path: str = "") -> None:
        """递归检查值中的字段名。"""
        if isinstance(value, dict):
            for key, val in value.items():
                if key.lower() in _PRIVACY_CANARY_FIELDS:
                    violations.append(f"{path}.{key}" if path else key)
                _check_value(val, f"{path}.{key}" if path else key)
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                _check_value(item, f"{path}[{i}]")

    _check_value(record)
    return (len(violations) == 0, violations)


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    """使用 nearest-rank 定义计算百分位；空样本返回 None。"""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100.0 * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _paired_bootstrap_ci(
    paired_diffs: list[float],
    *,
    confidence: float = _BASE_BOOTSTRAP_CONFIDENCE,
    n_boot: int = 10000,
    seed: str | None = None,
) -> tuple[float, float] | None:
    """计算确定性配对 bootstrap 均值区间；样本或置信水平无效时返回缺失。"""
    if len(paired_diffs) < 2 or not 0.0 < confidence < 1.0:
        return None
    if min(paired_diffs) == max(paired_diffs):
        return (paired_diffs[0], paired_diffs[0])

    if seed is None:
        seed = "topic_candidate_evidence_bootstrap"
    seed_bytes = hashlib.sha256(seed.encode("utf-8")).digest()
    rng = _DeterministicRandom(int.from_bytes(seed_bytes[:8], "big"))

    n = len(paired_diffs)
    alpha = 1.0 - confidence
    boot_means = []
    for _ in range(n_boot):
        resample = [paired_diffs[rng.randint(0, n - 1)] for _ in range(n)]
        boot_means.append(sum(resample) / n)

    boot_means.sort()
    lower_idx = int(alpha / 2 * n_boot)
    upper_idx = min(n_boot - 1, int((1 - alpha / 2) * n_boot))
    return (boot_means[lower_idx], boot_means[upper_idx])


def _paired_p95_delta_bootstrap_ci(
    paired_durations: list[tuple[float, float]],
    *,
    confidence: float,
    n_boot: int = 10000,
    seed: str | None = None,
) -> tuple[float, float] | None:
    """计算 K 相对 strict full 的配对 Provider P95 增量置信区间。"""
    if len(paired_durations) < 2 or not 0.0 < confidence < 1.0:
        return None
    if min(paired_durations) == max(paired_durations):
        full, candidate = paired_durations[0]
        return (candidate - full, candidate - full)

    if seed is None:
        seed = "topic_candidate_provider_p95_bootstrap"
    seed_bytes = hashlib.sha256(seed.encode("utf-8")).digest()
    rng = _DeterministicRandom(int.from_bytes(seed_bytes[:8], "big"))

    n = len(paired_durations)
    alpha = 1.0 - confidence
    deltas: list[float] = []
    for _ in range(n_boot):
        sampled = [paired_durations[rng.randint(0, n - 1)] for _ in range(n)]
        full_p95 = _nearest_rank([full for full, _ in sampled], 95.0)
        candidate_p95 = _nearest_rank([candidate for _, candidate in sampled], 95.0)
        assert full_p95 is not None
        assert candidate_p95 is not None
        deltas.append(candidate_p95 - full_p95)

    deltas.sort()
    lower_idx = int(alpha / 2 * n_boot)
    upper_idx = min(n_boot - 1, int((1 - alpha / 2) * n_boot))
    return (deltas[lower_idx], deltas[upper_idx])


class _DeterministicRandom:
    """确定性伪随机数生成器；LCG 实现。"""

    def __init__(self, seed: int):
        self._state = seed & 0xFFFFFFFFFFFFFFFF
        self._a = 6364136223846793005
        self._c = 1442695040888963407
        self._m = 2**64

    def randint(self, a: int, b: int) -> int:
        """返回 [a, b] 范围内的随机整数。"""
        self._state = (self._a * self._state + self._c) % self._m
        return a + int((self._state / self._m) * (b - a + 1))


def _bonferroni_confidence(factor: int) -> float:
    """把 95% family-wise 置信目标换算为单项 bootstrap 置信水平。"""
    return 1.0 - (1.0 - _BASE_BOOTSTRAP_CONFIDENCE) / max(1, factor)


def evaluate_evidence_gate(
    records: list[CandidateReplayRecord],
    misreuse_max_by_bucket: dict[str, float],
    min_quality_n: int = 200,
    min_token_n: int = 200,
    min_latency_n: int = 200,
    min_negative_n: int = 100,
    fragmentation_max_increase_pct: float = 1.0,
    token_min_drop_pct: float = 25.0,
    provider_p95_max_increase_ms: float = 0.0,
) -> EvidenceReport:
    """按实测规模分层；缺失 chat/K、实际测量或独立样本均不得放行。"""
    groups: dict[tuple[str, int | None, str, str], list[CandidateReplayRecord]] = {}
    strata: set[tuple[str, int | None, str]] = set()
    windows: set[tuple[str, int | None, str, str, bool]] = set()
    for record in records:
        count = record.catalog_topic_count
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            count = None
        stratum = (record.scale_bucket, count, record.chat_type)
        groups.setdefault((*stratum, record.variant), []).append(record)
        strata.update(
            (record.scale_bucket, count, chat) for chat in ("private", "group")
        )
        strata.add(stratum)
        windows.add((*stratum, record.case_hash, record.is_negative_window))

    bonferroni_factor = len(TOP_K_VALUES) * len(strata)
    confidence = _bonferroni_confidence(bonferroni_factor)
    safety_nonzero = any(record.safety_violation_count != 0 for record in records)
    results = []
    for bucket, count, chat in sorted(
        strata, key=lambda item: (item[0], item[1] or -1, item[2])
    ):
        for k in TOP_K_VALUES:
            results.append(
                _evaluate_single_k(
                    bucket=bucket,
                    catalog_topic_count=count,
                    chat_type=chat,
                    k=k,
                    full_records=groups.get((bucket, count, chat, "strict_full"), []),
                    k_records=groups.get((bucket, count, chat, f"top_k_{k}"), []),
                    misreuse_max=misreuse_max_by_bucket.get(bucket),
                    min_quality_n=max(200, min_quality_n),
                    min_token_n=max(200, min_token_n),
                    min_latency_n=max(200, min_latency_n),
                    min_negative_n=max(100, min_negative_n),
                    safety_nonzero=safety_nonzero,
                    fragmentation_max_increase_pct=fragmentation_max_increase_pct,
                    token_min_drop_pct=token_min_drop_pct,
                    provider_p95_max_increase_ms=provider_p95_max_increase_ms,
                    bootstrap_confidence=confidence,
                )
            )

    recommended_k, threshold, modes = _recommend_rollout(results)
    provider_durations = [
        record.provider_duration_ms
        for record in records
        if not record.is_negative_window
        and _valid_duration(record.provider_duration_ms)
    ]
    return EvidenceReport(
        total_windows=sum(not window[-1] for window in windows),
        total_negative_windows=sum(window[-1] for window in windows),
        bucket_results=tuple(results),
        recommended_k=recommended_k,
        e2e_provider_p95=_nearest_rank(provider_durations, 95.0),
        observe_shadow_cost=None,
        bonferroni_factor=bonferroni_factor,
        bootstrap_confidence=confidence,
        recommended_activation_threshold=threshold,
        recommended_bucket_modes=modes,
    )


def _evaluate_single_k(
    bucket: str,
    catalog_topic_count: int | None,
    chat_type: str,
    k: int,
    full_records: list[CandidateReplayRecord],
    k_records: list[CandidateReplayRecord],
    misreuse_max: float | None,
    min_quality_n: int,
    min_token_n: int,
    min_latency_n: int,
    min_negative_n: int,
    safety_nonzero: bool,
    fragmentation_max_increase_pct: float,
    token_min_drop_pct: float,
    provider_p95_max_increase_ms: float,
    bootstrap_confidence: float,
) -> EvidenceGateResult:
    """仅使用同源正常配对计算有效样本数；负向样本必须实际运行此 K。"""
    paired, reason_codes = _pair_records(full_records, k_records)
    normal_pairs = [pair for pair in paired if not pair[0].is_negative_window]
    negative_count = sum(pair[0].is_negative_window for pair in paired)
    fragmentation_diffs = _compute_fragmentation_diffs(normal_pairs)
    token_diffs = _compute_token_diffs(normal_pairs)
    duration_pairs = _compute_provider_duration_pairs(normal_pairs)
    if catalog_topic_count is None:
        reason_codes.append("catalog_scale_unavailable")
    if chat_type not in ("private", "group"):
        reason_codes.append("chat_type_invalid")
    if safety_nonzero:
        reason_codes.append("safety_nonzero")
    if not full_records or not k_records:
        reason_codes.append("stratum_missing")
    if negative_count < min_negative_n:
        reason_codes.append("sample_short_negative")
    if len(fragmentation_diffs) < min_quality_n:
        reason_codes.append("sample_short_quality")
    if len(token_diffs) < min_token_n:
        reason_codes.append("sample_short_token")
    if len(duration_pairs) < min_latency_n:
        reason_codes.append("sample_short_latency")
    if misreuse_max is None or not _valid_duration(misreuse_max) or misreuse_max > 100:
        reason_codes.append("misreuse_undeclared")
    if reason_codes:
        return _make_reject_result(
            bucket,
            chat_type,
            k,
            reason_codes,
            len(fragmentation_diffs),
            len(token_diffs),
            len(duration_pairs),
            negative_count,
            bootstrap_confidence,
            catalog_topic_count,
        )

    seed = f"{bucket}_{catalog_topic_count}_{chat_type}_{k}"
    frag_ci = _paired_bootstrap_ci(
        fragmentation_diffs, confidence=bootstrap_confidence, seed=seed + "_frag"
    )
    token_ci = _paired_bootstrap_ci(
        token_diffs, confidence=bootstrap_confidence, seed=seed + "_token"
    )
    provider_ci = _paired_p95_delta_bootstrap_ci(
        duration_pairs, confidence=bootstrap_confidence, seed=seed + "_provider_p95"
    )
    misreuse_rate = _compute_misreuse_rate([candidate for _, candidate in normal_pairs])
    if frag_ci is None:
        reason_codes.append("fragmentation_unavailable")
    elif frag_ci[1] > fragmentation_max_increase_pct:
        reason_codes.append("fragmentation_guardrail")
    if token_ci is None:
        reason_codes.append("token_drop_unavailable")
    elif token_ci[0] < token_min_drop_pct:
        reason_codes.append("token_drop_guardrail")
    if provider_ci is None:
        reason_codes.append("provider_p95_unavailable")
    elif provider_ci[1] > provider_p95_max_increase_ms:
        reason_codes.append("provider_p95_guardrail")
    if misreuse_rate is None:
        reason_codes.append("misreuse_unknown")
    elif misreuse_max is not None and misreuse_rate > misreuse_max:
        reason_codes.append("misreuse_guardrail")
    return EvidenceGateResult(
        scale_bucket=bucket,
        catalog_topic_count=catalog_topic_count,
        chat_type=chat_type,
        k=k,
        decision="accept" if not reason_codes else "reject",
        reason_codes=tuple(reason_codes),
        quality_sample_count=len(fragmentation_diffs),
        token_sample_count=len(token_diffs),
        latency_sample_count=len(duration_pairs),
        negative_sample_count=negative_count,
        fragmentation_ci_lower=frag_ci[0] if frag_ci else None,
        fragmentation_ci_upper=frag_ci[1] if frag_ci else None,
        token_drop_ci_lower=token_ci[0] if token_ci else None,
        token_drop_ci_upper=token_ci[1] if token_ci else None,
        provider_p95_delta_ci_lower=provider_ci[0] if provider_ci else None,
        provider_p95_delta_ci_upper=provider_ci[1] if provider_ci else None,
        misreuse_rate=misreuse_rate,
        bootstrap_confidence=bootstrap_confidence,
    )


def _pair_records(
    full_records: list[CandidateReplayRecord],
    k_records: list[CandidateReplayRecord],
) -> tuple[list[tuple[CandidateReplayRecord, CandidateReplayRecord]], list[str]]:
    """唯一 case 必须具有相同来源摘要、generation 和分层，拒绝重复取末条。"""
    indexes: list[dict[str, CandidateReplayRecord]] = []
    reasons: list[str] = []
    duplicates: set[str] = set()
    for records in (full_records, k_records):
        index: dict[str, CandidateReplayRecord] = {}
        sources: dict[str, str] = {}
        for record in records:
            if record.case_hash in index:
                duplicates.add(record.case_hash)
            if record.source_window_digest in sources:
                duplicates.update(
                    (record.case_hash, sources[record.source_window_digest])
                )
            sources[record.source_window_digest] = record.case_hash
            index[record.case_hash] = record
        indexes.append(index)
    if duplicates:
        reasons.append("pair_duplicate")
    pairs = []
    for case_hash in (indexes[0].keys() & indexes[1].keys()) - duplicates:
        full, candidate = indexes[0][case_hash], indexes[1][case_hash]
        if (
            full.execution_status != "success"
            or candidate.execution_status != "success"
        ):
            if "replay_not_successful" not in reasons:
                reasons.append("replay_not_successful")
            continue
        if (
            not case_hash
            or not full.source_window_digest
            or any(
                getattr(full, name) != getattr(candidate, name)
                for name in (
                    "source_window_digest",
                    "catalog_generation",
                    "scale_bucket",
                    "chat_type",
                    "catalog_topic_count",
                    "is_negative_window",
                )
            )
        ):
            if "pair_source_mismatch" not in reasons:
                reasons.append("pair_source_mismatch")
            continue
        pairs.append((full, candidate))
    pairs.sort(key=lambda pair: pair[0].case_hash)
    return pairs, reasons


def _compute_fragmentation_diffs(
    pairs: list[tuple[CandidateReplayRecord, CandidateReplayRecord]],
) -> list[float]:
    """质量样本仅计入有效 occurrence 分母与碎片计数俱全的正常配对。"""
    diffs = []
    for full, candidate in pairs:
        rates = []
        for record in (full, candidate):
            if (
                _valid_count(record.fragmentation_count)
                and _valid_count(record.window_topic_count, positive=True)
                and record.fragmentation_count <= record.window_topic_count
                and _valid_count(record.exact_reuse_count)
                and _valid_count(record.exact_topic_count, positive=True)
                and record.exact_reuse_count <= record.exact_topic_count
            ):
                rates.append(
                    record.fragmentation_count / record.window_topic_count * 100
                )
        if len(rates) == 2:
            diffs.append(rates[1] - rates[0])
    return diffs


def _compute_token_diffs(
    pairs: list[tuple[CandidateReplayRecord, CandidateReplayRecord]],
) -> list[float]:
    """计算 token 降幅（(full - K) / full * 100，百分比）。"""
    diffs = []
    for full_rec, k_rec in pairs:
        if (
            full_rec.token_availability == k_rec.token_availability == "estimated"
            and _valid_count(full_rec.estimated_tokens, positive=True)
            and _valid_count(k_rec.estimated_tokens)
        ):
            drop_pct = (
                (full_rec.estimated_tokens - k_rec.estimated_tokens)
                / full_rec.estimated_tokens
                * 100
            )
            diffs.append(drop_pct)
    return diffs


def _compute_provider_duration_pairs(
    pairs: list[tuple[CandidateReplayRecord, CandidateReplayRecord]],
) -> list[tuple[float, float]]:
    """提取 strict full 与 K 均有实际 Provider 观测的配对时延。"""
    durations: list[tuple[float, float]] = []
    for full_rec, k_rec in pairs:
        if _valid_duration(full_rec.provider_duration_ms) and _valid_duration(
            k_rec.provider_duration_ms
        ):
            durations.append(
                (full_rec.provider_duration_ms, k_rec.provider_duration_ms)
            )
    return durations


def _compute_misreuse_rate(records: list[CandidateReplayRecord]) -> float | None:
    """以百分点计算误复用率；人工标注分母缺失或无效时返回未知。"""
    valid = [
        record
        for record in records
        if _valid_count(record.candidate_occurrence_count)
        and _valid_count(record.misreuse_count)
        and record.misreuse_count <= record.candidate_occurrence_count
    ]
    if not valid or len(valid) != len(records):
        return None

    candidate_occurrences = sum(
        record.candidate_occurrence_count or 0 for record in valid
    )
    if candidate_occurrences <= 0:
        return None
    misreuse_occurrences = sum(record.misreuse_count or 0 for record in valid)
    return misreuse_occurrences / candidate_occurrences * 100.0


def _valid_count(value: object, *, positive: bool = False) -> TypeGuard[int]:
    """有效计数拒绝 bool、缺测和负值。"""
    return type(value) is int and value >= (1 if positive else 0)


def _valid_duration(value: object) -> TypeGuard[float]:
    """有效观测必须是非负有限数值。"""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _make_reject_result(
    bucket: str,
    chat_type: str,
    k: int | None,
    reason_codes: list[str],
    quality_n: int,
    token_n: int,
    latency_n: int,
    negative_n: int,
    bootstrap_confidence: float,
    catalog_topic_count: int | None = None,
) -> EvidenceGateResult:
    """构造带有本次 family-wise 置信水平的 reject 结果。"""
    return EvidenceGateResult(
        scale_bucket=bucket,
        catalog_topic_count=catalog_topic_count,
        chat_type=chat_type,
        k=k,
        decision="reject",
        reason_codes=tuple(reason_codes),
        quality_sample_count=quality_n,
        token_sample_count=token_n,
        latency_sample_count=latency_n,
        negative_sample_count=negative_n,
        fragmentation_ci_lower=None,
        fragmentation_ci_upper=None,
        token_drop_ci_lower=None,
        token_drop_ci_upper=None,
        provider_p95_delta_ci_lower=None,
        provider_p95_delta_ci_upper=None,
        misreuse_rate=None,
        bootstrap_confidence=bootstrap_confidence,
    )


def _find_recommended_k(results: list[EvidenceGateResult]) -> int | None:
    """每个已观测分层均须通过；k=None 的拒绝不能被过滤后遗忘。"""
    strata = {
        (r.scale_bucket, r.catalog_topic_count, chat)
        for r in results
        for chat in ("private", "group")
    }
    if any(r.k is None or r.catalog_topic_count is None for r in results):
        return None
    for k in TOP_K_VALUES:
        matching = [r for r in results if r.k == k]
        if (
            matching
            and len(matching) == len(strata)
            and {r.chat_type for r in matching} == {"private", "group"}
            and all(r.decision == "accept" for r in matching)
        ):
            return k
    return None


def _recommend_rollout(
    results: list[EvidenceGateResult],
) -> tuple[int | None, int | None, dict[str, str]]:
    """先选最小通过 K，再选实测最小阈值；每个启用桶的后续规模均须通过。"""
    buckets = {"tiny", "small", "medium", "large", "xlarge", "huge"} | {
        r.scale_bucket for r in results
    }
    observe = {bucket: "observe" for bucket in sorted(buckets)}
    thresholds = sorted(
        {
            r.catalog_topic_count
            for r in results
            if r.catalog_topic_count is not None and r.catalog_topic_count >= 3
        }
    )
    for k in TOP_K_VALUES:
        for threshold in thresholds:
            modes = observe.copy()
            for bucket in buckets:
                relevant = [
                    r
                    for r in results
                    if r.scale_bucket == bucket
                    and (r.k == k or r.k is None)
                    and (
                        r.catalog_topic_count is None
                        or r.catalog_topic_count >= threshold
                    )
                ]
                if _find_recommended_k(relevant) == k:
                    modes[bucket] = "top_k"
            if any(
                r.k == k
                and r.catalog_topic_count == threshold
                and modes[r.scale_bucket] == "top_k"
                for r in results
            ):
                return k, threshold, modes
    return None, None, observe


__all__ = [
    "TOP_K_VALUES",
    "CandidateReplayRecord",
    "EvidenceGateResult",
    "EvidenceReport",
    "check_privacy_canary",
    "create_replay_record",
    "evaluate_evidence_gate",
]
