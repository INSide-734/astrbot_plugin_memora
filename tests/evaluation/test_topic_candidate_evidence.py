"""Topic 候选证据门：实际规模、同源配对、有效样本和保守发布。"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from typing import Literal

import pytest

from core.features.evaluation.application.topic_candidate_evidence import (
    TOP_K_VALUES,
    CandidateReplayRecord,
    _compute_misreuse_rate,
    _find_recommended_k,
    check_privacy_canary,
    create_replay_record,
    evaluate_evidence_gate,
)


def _records(
    *,
    count: int | None = 17,
    bucket: str = "small",
    chats: tuple[Literal["private", "group"], ...] = ("private", "group"),
    normal_n: int = 200,
    negative_n: int = 100,
) -> list[CandidateReplayRecord]:
    """生成独立源窗口、真实测量形态的正常/负向全 K 配对。"""
    records = []
    for chat in chats:
        for negative, total in ((False, normal_n), (True, negative_n)):
            for index in range(total):
                case = f"{bucket}-{count}-{chat}-{negative}-{index}"
                for variant in ("strict_full", *(f"top_k_{k}" for k in TOP_K_VALUES)):
                    record = create_replay_record(
                        case_hash=case,
                        scale_bucket=bucket,
                        chat_type=chat,
                        catalog_generation=1,
                        catalog_topic_count=count,
                        variant=variant,
                        source_window_digest=f"digest-{case}",
                        candidate_count=4,
                        bm25_hit_count=4,
                        recent_fill_count=0,
                        identity_drop_count=0,
                        safety_violation_count=0,
                        exact_reuse_count=98,
                        exact_topic_count=100,
                        candidate_occurrence_count=98,
                        misreuse_count=0,
                        duplicate_count=0,
                        window_topic_count=100,
                        fragmentation_count=5,
                        token_availability="estimated",
                        estimated_tokens=1000 if variant == "strict_full" else 700,
                        selector_duration_ms=1.0,
                        provider_duration_ms=100.0,
                        reason_code="measured",
                        is_negative_window=negative,
                    )
                    assert record is not None
                    records.append(record)
    return records


def _gate(records: list[CandidateReplayRecord], **kwargs):
    """声明各已测桶的相同误复用上限。"""
    return evaluate_evidence_gate(
        records, {record.scale_bucket: 5.0 for record in records}, **kwargs
    )


def _result(report, k=4, chat="private", count=17):
    """取出一个精确规模与 chat 的公开判门结果。"""
    return next(
        result
        for result in report.bucket_results
        if result.k == k
        and result.chat_type == chat
        and result.catalog_topic_count == count
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"topic": "secret"},
        {"metadata": {"query": "secret"}},
        {"items": [{"Scope": "secret"}]},
        {"PROMPT": "secret"},
    ],
)
def test_privacy_canary_rejects_nested_sensitive_fields(payload):
    """不同层级的敏感字段都使 allowlist 检查拒绝。"""
    safe, violations = check_privacy_canary(payload)
    assert not safe
    assert violations


def test_exact_count_is_preserved_and_minimum_demonstrated_threshold_is_selected():
    """17 个话题的证据只能推荐 17，不能把 small 的下界当作实测阈值。"""
    report = _gate(_records())
    assert (report.recommended_k, report.recommended_activation_threshold) == (4, 17)
    assert report.recommended_bucket_modes["small"] == "top_k"
    assert all(
        mode == "observe"
        for bucket, mode in report.recommended_bucket_modes.items()
        if bucket != "small"
    )
    assert {
        (result.catalog_topic_count, result.chat_type, result.k)
        for result in report.bucket_results
    } == {(17, chat, k) for chat in ("private", "group") for k in TOP_K_VALUES}
    assert all(
        result.decision == "accept"
        and result.quality_sample_count == 200
        and result.negative_sample_count == 100
        for result in report.bucket_results
    )
    assert (report.total_windows, report.total_negative_windows) == (400, 200)
    serialized = json.loads(json.dumps(asdict(report)))
    assert serialized["bucket_results"][0]["catalog_topic_count"] == 17
    assert serialized["recommended_activation_threshold"] == 17


def test_k24_can_be_the_smallest_passing_k():
    """四个较小 K 确实退化时，只允许 K24 通过。"""
    records = [
        replace(record, fragmentation_count=8)
        if record.variant not in ("strict_full", "top_k_24")
        else record
        for record in _records(count=64, bucket="medium")
    ]
    report = _gate(records)
    assert (report.recommended_k, report.recommended_activation_threshold) == (24, 64)
    assert all(
        result.decision == ("accept" if result.k == 24 else "reject")
        for result in report.bucket_results
    )


def test_threshold_must_exclude_every_failing_tested_lower_count():
    """同桶较小规模不通过时，保留结果并提升到真正通过的实测规模。"""
    records = [
        replace(record, provider_duration_ms=120.0)
        if record.variant != "strict_full"
        else record
        for record in _records()
    ]
    records += _records(count=23)
    report = _gate(records)
    assert (report.recommended_k, report.recommended_activation_threshold) == (4, 23)
    assert _result(report).decision == "reject"
    assert _result(report, count=23).decision == "accept"


def test_higher_count_failure_prevents_enabling_lower_success():
    """17 通过而 23 失败，不得启用会覆盖 23 的阈值 17。"""
    records = _records() + [
        replace(record, provider_duration_ms=120.0)
        if record.variant != "strict_full"
        else record
        for record in _records(count=23)
    ]
    report = _gate(records)
    assert report.recommended_k is None
    assert report.recommended_activation_threshold is None
    assert report.recommended_bucket_modes["small"] == "observe"
    assert _result(report).decision == "accept"


def test_minimum_k_precedes_minimum_threshold():
    """K4/23 优先于 K8/17，不能为了较低阈值增大 K。"""
    records = [
        replace(record, fragmentation_count=8)
        if record.variant == "top_k_4"
        else record
        for record in _records()
    ]
    report = _gate(records + _records(count=23))
    assert (report.recommended_k, report.recommended_activation_threshold) == (4, 23)


def test_failed_other_bucket_stays_observe_without_hiding_its_results():
    """不通过的桶保持 observe，但不阻止完整获证的另一个桶独立发布。"""
    records = _records() + [
        replace(record, provider_duration_ms=None)
        for record in _records(count=64, bucket="medium")
    ]
    report = _gate(records)
    assert report.recommended_bucket_modes["small"] == "top_k"
    assert report.recommended_bucket_modes["medium"] == "observe"
    assert any(
        result.scale_bucket == "medium" and result.decision == "reject"
        for result in report.bucket_results
    )
    assert _find_recommended_k(list(report.bucket_results)) is None


def test_legacy_sentinel_rejection_cannot_disappear_when_filtering_k():
    """k=None 的拒绝仍属于观测分层，不能通过过滤 K 将其隐藏。"""
    results = list(_gate(_records()).bucket_results)
    results.append(
        replace(results[0], scale_bucket="medium", k=None, decision="reject")
    )
    assert _find_recommended_k(results) is None


def test_missing_chat_has_explicit_rejection_for_every_k():
    """仅 private 获证不会默认推断 group 已测。"""
    report = _gate(_records(chats=("private",)))
    assert report.recommended_k is None
    group = [result for result in report.bucket_results if result.chat_type == "group"]
    assert {result.k for result in group} == set(TOP_K_VALUES)
    assert all(
        result.decision == "reject" and "stratum_missing" in result.reason_codes
        for result in group
    )


def test_legacy_missing_scale_cannot_enable_rollout():
    """旧记录没有精确规模，即使其它指标齐全也不可发布。"""
    report = _gate(_records(count=None))
    assert report.recommended_k is None
    assert report.recommended_activation_threshold is None
    assert all(
        "catalog_scale_unavailable" in result.reason_codes
        for result in report.bucket_results
    )


def test_off_only_observed_count_is_not_omitted():
    """只有 off 回放的规模也必须输出五个 K 的缺测判定。"""
    records = _records() + [
        replace(_records(count=23, normal_n=1, negative_n=0)[0], variant="off")
    ]
    report = _gate(records)
    assert _result(report, count=23).decision == "reject"
    assert report.recommended_bucket_modes["small"] == "observe"


def test_another_variants_negative_windows_cannot_prove_unrun_k():
    """strict full 与 K8 的负向运行不能计入 K4 的负向样本。"""
    records = [
        record
        for record in _records()
        if not (record.variant == "top_k_4" and record.is_negative_window)
    ]
    report = _gate(records)
    assert _result(report).negative_sample_count == 0
    assert "sample_short_negative" in _result(report).reason_codes
    assert report.recommended_k == 8


def test_negative_samples_never_inflate_normal_minimum_even_with_overrides():
    """负向窗口携带完整数字也不能补足 199 个正常样本；门槛不能调低。"""
    report = _gate(
        _records(normal_n=199, negative_n=250),
        min_quality_n=1,
        min_token_n=1,
        min_latency_n=1,
        min_negative_n=1,
    )
    assert report.recommended_k is None
    result = _result(report)
    assert (
        result.quality_sample_count,
        result.token_sample_count,
        result.latency_sample_count,
    ) == (199, 199, 199)
    assert result.negative_sample_count == 250


@pytest.mark.parametrize(
    ("changes", "counter", "reason"),
    [
        ({"fragmentation_count": None}, "quality_sample_count", "sample_short_quality"),
        ({"window_topic_count": 0}, "quality_sample_count", "sample_short_quality"),
        ({"estimated_tokens": None}, "token_sample_count", "sample_short_token"),
        (
            {"provider_duration_ms": float("nan")},
            "latency_sample_count",
            "sample_short_latency",
        ),
    ],
)
def test_only_numerically_valid_pairs_count(changes, counter, reason):
    """声明可用不等于有效测量；无效配对不会计入 200 个样本。"""
    records = _records()
    index = next(
        i
        for i, record in enumerate(records)
        if record.variant == "top_k_4" and not record.is_negative_window
    )
    records[index] = replace(records[index], **changes)
    result = _result(_gate(records))
    assert getattr(result, counter) == 199
    assert reason in result.reason_codes


@pytest.mark.parametrize(
    "mutation", ["duplicate", "digest", "generation", "negative", "source_alias"]
)
def test_duplicate_or_mismatched_source_pairs_are_rejected(mutation):
    """重复 case、重复源与跨摘要/代际/正常负向配对都不能形成发布证据。"""
    records = _records(normal_n=201)
    index = next(i for i, record in enumerate(records) if record.variant == "top_k_4")
    original = records[index]
    if mutation == "duplicate":
        records.append(original)
    elif mutation == "digest":
        records[index] = replace(original, source_window_digest="other-source")
    elif mutation == "generation":
        records[index] = replace(original, catalog_generation=2)
    elif mutation == "negative":
        records[index] = replace(original, is_negative_window=True)
    else:
        records.append(replace(original, case_hash="alias-of-existing-source"))
    result = _result(_gate(records))
    assert result.decision == "reject"
    assert {"pair_duplicate", "pair_source_mismatch"} & set(result.reason_codes)


def test_failed_negative_execution_is_not_counted_as_safety_evidence():
    """selector 降级未运行负向选择时，不得当作零违规成功。"""
    records = [
        replace(record, execution_status="error")
        if record.is_negative_window and record.variant == "top_k_4"
        else record
        for record in _records()
    ]
    result = _result(_gate(records))
    assert result.negative_sample_count == 0
    assert "replay_not_successful" in result.reason_codes


def test_safety_violation_anywhere_blocks_every_bucket():
    """零安全事件是全报告护栏，正常窗口中的泄漏也立即拒绝。"""
    records = _records()
    records[0] = replace(records[0], safety_violation_count=1)
    report = _gate(records)
    assert report.recommended_k is None
    assert all(
        "safety_nonzero" in result.reason_codes for result in report.bucket_results
    )


def test_unavailable_provider_and_token_measurements_stay_unavailable():
    """原始 selector 时间不冒充 Provider；没有 usage 时不推算 token。"""
    records = [
        replace(
            record,
            estimated_tokens=None,
            token_availability="unavailable",
            provider_duration_ms=None,
        )
        for record in _records()
    ]
    report = _gate(records)
    assert report.e2e_provider_p95 is None
    assert report.recommended_k is None
    result = _result(report)
    assert result.token_sample_count == result.latency_sample_count == 0
    assert result.token_drop_ci_lower is result.provider_p95_delta_ci_upper is None


def test_misreuse_uses_occurrence_percentage_points_and_rejects_missing_annotation():
    """2/20 必须是 10 个百分点，不能丢弃缺标注记录后稀释误复用。"""
    records = [
        replace(record, candidate_occurrence_count=20, misreuse_count=2)
        if record.variant == "top_k_4"
        else record
        for record in _records()
    ]
    result = _result(_gate(records))
    assert result.misreuse_rate == 10.0
    assert "misreuse_guardrail" in result.reason_codes
    measured = records[1]
    assert (
        _compute_misreuse_rate([measured, replace(measured, misreuse_count=None)])
        is None
    )


def test_provider_tail_regression_is_rejected_by_paired_p95():
    """超过 5% 的尾部回退不能被平均时延掩盖。"""
    records = _records(chats=("private",))
    changed = 0
    for index, record in enumerate(records):
        if (
            record.variant == "top_k_4"
            and not record.is_negative_window
            and changed < 20
        ):
            records[index] = replace(record, provider_duration_ms=110.0)
            changed += 1
    result = _result(_gate(records))
    assert result.provider_p95_delta_ci_upper == 10.0
    assert "provider_p95_guardrail" in result.reason_codes


def test_missing_misreuse_limit_rejects_and_extra_strata_tighten_confidence():
    """所有 K/chat/精确规模进入 family-wise 校正，包括缺测分层。"""
    records = _records()
    report = evaluate_evidence_gate(records, {})
    assert all(
        "misreuse_undeclared" in result.reason_codes for result in report.bucket_results
    )
    larger = _gate(records + _records(count=23))
    assert larger.bonferroni_factor == 2 * report.bonferroni_factor
    assert larger.bootstrap_confidence > report.bootstrap_confidence
