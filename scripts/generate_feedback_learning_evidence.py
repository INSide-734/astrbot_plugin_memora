#!/usr/bin/env python3
"""从受控匿名回放生成并投递反馈学习 Evidence artifact。"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.evaluation.feedback_learning_pipeline import (  # noqa: E402
    run_feedback_ranking_evaluation_and_publish_evidence,
)
from core.evaluation.feedback_ranking_ablation import (  # noqa: E402
    FeedbackRankingEvidenceRequest,
    FeedbackRankingPairedSample,
)
from core.evaluation.retrieval_quality import EvaluationCase  # noqa: E402
from core.features.learning.domain.feedback_learning_evidence import (  # noqa: E402
    EvidenceEvaluatorConfig,
    FeedbackRankingConfigSnapshot,
    build_feedback_ranking_replay_manifest,
    feedback_ranking_case_hash,
    parse_evidence_utc_timestamp,
)
from core.features.learning.domain.feedback_learning_evidence_contract import (  # noqa: E402
    ALLOWED_EVIDENCE_REGRESSION_FAILURES,
    SUPPORTED_EVIDENCE_QUALITY_GATES,
    complete_evidence_regression_checks,
    safe_evidence_code,
    safe_evidence_stage_name,
)
from core.features.learning.domain.models import (  # noqa: E402
    FeedbackSignalAggregate,
    FeedbackSignalPolicy,
)
from core.features.learning.infrastructure.feedback_learning_evidence_store import (  # noqa: E402
    LearningEvidenceInboxError,
)

_INPUT_SCHEMA_VERSION = "feedback-learning-evidence-input-v1"
_MAX_INPUT_BYTES = 4 * 1024 * 1024
_MAX_CASES = 500
_MAX_CANDIDATES = 100
_MAX_STAGES = 20
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "aggregation_revision",
        "source_config_revision",
        "quality_gate_version",
        "dataset_version",
        "k",
        "aggregate",
        "cases",
        "regression_checks",
        "regression_failures",
    }
)
_AGGREGATE_FIELDS = frozenset(
    {
        "window_start_utc",
        "window_end_utc",
        "accepted_count",
        "independent_window_count",
        "decayed_support",
        "baseline_document_weight",
        "baseline_graph_weight",
        "target_document_weight",
        "target_graph_weight",
        "policy_version",
    }
)
_CASE_FIELDS = frozenset(
    {
        "case_id",
        "query_hash",
        "group_hash",
        "relevant_doc_hashes",
        "expected_no_hit",
        "baseline_candidates",
        "observed_at_utc",
        "baseline_stage_latencies_ms",
        "shadow_stage_latencies_ms",
        "baseline_ttft_ms",
        "shadow_ttft_ms",
        "baseline_provider_calls",
        "shadow_provider_calls",
        "baseline_token_cost",
        "shadow_token_cost",
    }
)
_CANDIDATE_FIELDS = frozenset({"doc_hash", "score", "route"})


class EvidenceInputError(ValueError):
    """表示匿名 Evidence 回放不符合固定输入契约。"""


@dataclass(frozen=True, slots=True)
class _Replay:
    """保存已验证的离线回放及其隔离检索候选。"""

    data_dir: Path
    cases: tuple[EvaluationCase, ...]
    candidates: dict[str, tuple[dict[str, object], ...]]
    aggregate: FeedbackSignalAggregate
    policy: FeedbackSignalPolicy
    request: FeedbackRankingEvidenceRequest
    k: int


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """拒绝 JSON 对象中的重复键，避免校验与执行看到不同值。"""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceInputError("duplicate_json_key")
        result[key] = value
    return result


def _reject_nonfinite_constant(_value: str) -> None:
    """拒绝 JSON 扩展的 NaN、Infinity 和 -Infinity。"""

    raise EvidenceInputError("nonfinite_json_number")


def _read_document(path: Path) -> Mapping[str, Any]:
    """在固定大小上限内读取严格 JSON 对象，不回显原始输入。"""

    try:
        if not path.is_file() or path.stat().st_size > _MAX_INPUT_BYTES:
            raise EvidenceInputError("input_file_invalid")
        payload = json.loads(
            path.read_bytes().decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite_constant,
        )
    except EvidenceInputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise EvidenceInputError("input_json_invalid") from exc
    if not isinstance(payload, Mapping):
        raise EvidenceInputError("input_object_required")
    return payload


def _exact_fields(value: object, fields: frozenset[str]) -> Mapping[str, Any]:
    """要求对象字段集合与固定 schema 完全一致。"""

    if not isinstance(value, Mapping) or set(value) != fields:
        raise EvidenceInputError("input_fields_invalid")
    return value


def _sha(value: object) -> str:
    """校验并返回小写 SHA-256 标识。"""

    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise EvidenceInputError("anonymous_identifier_invalid")
    return value


def _number(value: object, *, minimum: float = 0.0) -> float:
    """校验有限非负普通数值，拒绝布尔值和特殊浮点数。"""

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EvidenceInputError("number_invalid")
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise EvidenceInputError("number_invalid")
    return number


def _positive_int(value: object) -> int:
    """校验正整数并拒绝可伪装成整数的布尔值。"""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EvidenceInputError("positive_integer_invalid")
    return value


def _utc(value: object) -> Any:
    """校验 canonical UTC 时间文本。"""

    parsed = parse_evidence_utc_timestamp(value)
    if parsed is None:
        raise EvidenceInputError("utc_timestamp_invalid")
    return parsed


def _stage_timings(value: object) -> tuple[tuple[str, float], ...]:
    """读取固定 allowlist 的 stage timing，并以名称排序。"""

    if not isinstance(value, Mapping) or not 1 <= len(value) <= _MAX_STAGES:
        raise EvidenceInputError("stage_timing_invalid")
    result: list[tuple[str, float]] = []
    for name, timing in value.items():
        if not safe_evidence_stage_name(name):
            raise EvidenceInputError("stage_name_invalid")
        result.append((name, _number(timing)))
    return tuple(sorted(result))


def _candidates(value: object) -> tuple[dict[str, object], ...]:
    """读取匿名 baseline 候选，并拒绝重复文档或未知 route。"""

    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_CANDIDATES:
        raise EvidenceInputError("candidate_list_invalid")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in value:
        item = _exact_fields(raw, _CANDIDATE_FIELDS)
        doc_hash = _sha(item["doc_hash"])
        if doc_hash in seen or item["route"] not in {"document", "graph"}:
            raise EvidenceInputError("candidate_invalid")
        seen.add(doc_hash)
        result.append(
            {
                "doc_id": doc_hash,
                "score": _number(item["score"], minimum=0.0),
                "route": item["route"],
            }
        )
    return tuple(result)


def _parse_case(
    raw: object,
    *,
    stage_names: tuple[str, ...] | None,
) -> tuple[
    EvaluationCase,
    tuple[dict[str, object], ...],
    FeedbackRankingPairedSample,
    tuple[str, ...],
]:
    """把单条匿名回放转换为评测用例、候选和配对性能样本。"""

    item = _exact_fields(raw, _CASE_FIELDS)
    case_id = _sha(item["case_id"])
    query_hash = _sha(item["query_hash"])
    group_hash = _sha(item["group_hash"])
    expected_no_hit = item["expected_no_hit"]
    if not isinstance(expected_no_hit, bool):
        raise EvidenceInputError("negative_case_flag_invalid")
    relevant_values = item["relevant_doc_hashes"]
    if not isinstance(relevant_values, list) or len(relevant_values) != len(
        set(relevant_values)
    ):
        raise EvidenceInputError("relevant_document_ids_invalid")
    relevant = {_sha(value) for value in relevant_values}
    if expected_no_hit != (not relevant):
        raise EvidenceInputError("negative_case_binding_invalid")
    candidates = _candidates(item["baseline_candidates"])
    observed_at = item["observed_at_utc"]
    _utc(observed_at)
    baseline_stages = _stage_timings(item["baseline_stage_latencies_ms"])
    shadow_stages = _stage_timings(item["shadow_stage_latencies_ms"])
    names = tuple(name for name, _ in baseline_stages)
    if names != tuple(name for name, _ in shadow_stages) or (
        stage_names is not None and names != stage_names
    ):
        raise EvidenceInputError("stage_set_mismatch")
    case = EvaluationCase(
        case_id=case_id,
        query=query_hash,
        relevant_doc_ids=relevant,
        metadata={
            "scope_domain": "global_aggregate",
            "persona_domain": None,
            "group_label": group_hash,
            "expected_no_hit": expected_no_hit,
        },
    )
    sample = FeedbackRankingPairedSample(
        case_hash=feedback_ranking_case_hash(case_id),
        observed_at_utc=observed_at,
        baseline_stage_latencies_ms=baseline_stages,
        shadow_stage_latencies_ms=shadow_stages,
        baseline_ttft_ms=_number(item["baseline_ttft_ms"]),
        shadow_ttft_ms=_number(item["shadow_ttft_ms"]),
        baseline_provider_calls=_number(item["baseline_provider_calls"]),
        shadow_provider_calls=_number(item["shadow_provider_calls"]),
        baseline_token_cost=_number(item["baseline_token_cost"]),
        shadow_token_cost=_number(item["shadow_token_cost"]),
    )
    return case, candidates, sample, names


def _parse_replay(data_dir: Path, document: Mapping[str, Any]) -> _Replay:
    """严格解析回放文档并构造不具备生产写权限的离线请求。"""

    root = _exact_fields(document, _ROOT_FIELDS)
    if root["schema_version"] != _INPUT_SCHEMA_VERSION:
        raise EvidenceInputError("input_schema_version_invalid")
    aggregation_revision = _sha(root["aggregation_revision"])
    source_revision = _sha(root["source_config_revision"])
    quality_gate = root["quality_gate_version"]
    if quality_gate not in SUPPORTED_EVIDENCE_QUALITY_GATES:
        raise EvidenceInputError("quality_gate_version_invalid")
    dataset_version = root["dataset_version"]
    if not safe_evidence_code(dataset_version):
        raise EvidenceInputError("dataset_version_invalid")
    k = root["k"]
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 20:
        raise EvidenceInputError("evaluation_depth_invalid")

    aggregate_data = _exact_fields(root["aggregate"], _AGGREGATE_FIELDS)
    window_start = _utc(aggregate_data["window_start_utc"])
    window_end = _utc(aggregate_data["window_end_utc"])
    if window_end <= window_start:
        raise EvidenceInputError("window_range_invalid")
    baseline_document = _number(aggregate_data["baseline_document_weight"])
    baseline_graph = _number(aggregate_data["baseline_graph_weight"])
    target_document = _number(aggregate_data["target_document_weight"])
    target_graph = _number(aggregate_data["target_graph_weight"])
    if not math.isclose(baseline_document + baseline_graph, 1.0):
        raise EvidenceInputError("baseline_weight_invalid")
    if not math.isclose(target_document + target_graph, 1.0):
        raise EvidenceInputError("target_weight_invalid")
    policy = FeedbackSignalPolicy(
        policy_version=_positive_int(aggregate_data["policy_version"]),
        baseline_document_weight=baseline_document,
        baseline_graph_weight=baseline_graph,
    )
    delta = round(target_document - baseline_document, 12)
    if abs(delta) > policy.max_weight_delta + 1e-12 or not math.isclose(
        target_graph - baseline_graph, -delta, abs_tol=1e-12
    ):
        raise EvidenceInputError("weight_delta_invalid")
    independent_windows = _positive_int(aggregate_data["independent_window_count"])
    if independent_windows < 2:
        raise EvidenceInputError("independent_window_count_invalid")
    aggregate = FeedbackSignalAggregate(
        scope_domain="global_aggregate",
        persona_domain=None,
        window_start=window_start,
        window_end=window_end,
        accepted_count=_positive_int(aggregate_data["accepted_count"]),
        independent_window_count=independent_windows,
        decayed_support=_number(aggregate_data["decayed_support"]),
        proposed_document_weight=target_document,
        proposed_graph_weight=target_graph,
        delta_from_baseline=delta,
        status="candidate",
        policy_version=policy.policy_version,
    )

    raw_cases = root["cases"]
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= _MAX_CASES:
        raise EvidenceInputError("case_list_invalid")
    cases: list[EvaluationCase] = []
    candidates: dict[str, tuple[dict[str, object], ...]] = {}
    samples: list[FeedbackRankingPairedSample] = []
    case_ids: set[str] = set()
    stage_names: tuple[str, ...] | None = None
    for raw_case in raw_cases:
        case, case_candidates, sample, names = _parse_case(
            raw_case,
            stage_names=stage_names,
        )
        if case.case_id in case_ids:
            raise EvidenceInputError("duplicate_case_id")
        case_ids.add(case.case_id)
        stage_names = names
        cases.append(case)
        candidates[case.case_id] = case_candidates
        samples.append(sample)
    assert stage_names is not None

    regression_checks = root["regression_checks"]
    if not complete_evidence_regression_checks(regression_checks):
        raise EvidenceInputError("regression_checks_incomplete")
    failures = root["regression_failures"]
    if (
        not isinstance(failures, list)
        or len(failures) != len(set(failures))
        or any(item not in ALLOWED_EVIDENCE_REGRESSION_FAILURES for item in failures)
    ):
        raise EvidenceInputError("regression_failures_invalid")
    request = FeedbackRankingEvidenceRequest(
        aggregation_revision=aggregation_revision,
        quality_gate_version=quality_gate,
        replay_manifest=build_feedback_ranking_replay_manifest(
            cases,
            dataset_version=dataset_version,
        ),
        baseline_snapshot=FeedbackRankingConfigSnapshot(
            source_config_revision=source_revision,
            document_route_weight=baseline_document,
            graph_route_weight=baseline_graph,
        ),
        target_snapshot=FeedbackRankingConfigSnapshot(
            source_config_revision=source_revision,
            document_route_weight=target_document,
            graph_route_weight=target_graph,
        ),
        evaluator_config=EvidenceEvaluatorConfig(
            retrieval_stage_names=stage_names,
        ),
        independent_window_count=independent_windows,
        paired_samples=tuple(samples),
        regression_checks=tuple(sorted(regression_checks)),
        regression_failures=tuple(failures),
    )
    return _Replay(
        data_dir=data_dir,
        cases=tuple(cases),
        candidates=candidates,
        aggregate=aggregate,
        policy=policy,
        request=request,
        k=k,
    )


async def _run_replay(replay: _Replay):
    """运行隔离回放并将结果投递到固定 Evidence Inbox。"""

    def baseline_retriever(case: EvaluationCase, _k: int) -> Sequence[Any]:
        """仅返回输入中的匿名候选，不访问生产引擎或配置。"""

        return list(replay.candidates[case.case_id])

    return await run_feedback_ranking_evaluation_and_publish_evidence(
        replay.data_dir,
        replay.cases,
        baseline_retriever,
        replay.aggregate,
        k=replay.k,
        policy=replay.policy,
        evidence_request=replay.request,
    )


def _print_json(payload: Mapping[str, object], *, error: bool = False) -> None:
    """输出固定低敏 JSON 摘要，不输出路径、原始输入或异常文本。"""

    print(
        json.dumps(payload, ensure_ascii=True, sort_keys=True),
        file=sys.stderr if error else sys.stdout,
    )


def _parser() -> argparse.ArgumentParser:
    """构造离线 Evidence 入口的命令行解析器。"""

    parser = argparse.ArgumentParser(
        description="从受控匿名回放生成反馈学习 Evidence artifact。"
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        help="已存在的插件受控数据目录；artifact 只写入固定 Evidence Inbox。",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="受控匿名 JSON 回放文件。",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """执行离线 Evidence 生成，返回稳定的 shell 退出码。"""

    args = _parser().parse_args(argv)
    try:
        data_dir = Path(args.data_dir).resolve()
        if not data_dir.is_dir():
            raise EvidenceInputError("data_directory_invalid")
        replay = _parse_replay(data_dir, _read_document(Path(args.input).resolve()))
        report = asyncio.run(_run_replay(replay))
    except asyncio.CancelledError:
        raise
    except LearningEvidenceInboxError:
        _print_json(
            {
                "status": "error",
                "error": {"code": "evidence_persistence_failed", "retryable": False},
            },
            error=True,
        )
        return 1
    except (EvidenceInputError, OSError, ValueError, TypeError):
        _print_json(
            {
                "status": "error",
                "error": {"code": "evidence_input_invalid", "retryable": False},
            },
            error=True,
        )
        return 2

    artifact = report.evidence_artifact
    status = report.evidence_status
    payload = {
        "status": status,
        "evidence_revision": artifact.evidence_revision if artifact else None,
        "passed": bool(artifact and artifact.passed),
        "reason_codes": list(report.evidence_reason_codes),
    }
    _print_json(payload)
    return 0 if status == "ready" and artifact is not None and artifact.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
