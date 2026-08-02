"""比较两组反思诊断 JSONL 的隐私安全聚合指标。"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_OUTCOME_FIELDS = (
    "canonical_count",
    "quarantine_count",
    "failed_count",
    "skipped_idempotent_count",
)


@dataclass(frozen=True, slots=True)
class LoadedEvents:
    """保存合法对象事件与无法解析的非空行数量。"""

    events: tuple[dict[str, Any], ...]
    malformed_line_count: int = 0


def load_events(path: str | Path) -> LoadedEvents:
    """加载 JSONL 对象事件，并单独统计语法错误或非对象行。"""

    events: list[dict[str, Any]] = []
    malformed_line_count = 0
    with Path(path).open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                malformed_line_count += 1
                continue
            if not isinstance(payload, dict):
                malformed_line_count += 1
                continue
            events.append(payload)
    return LoadedEvents(tuple(events), malformed_line_count)


def _safe_number(value: object) -> float | None:
    """将有限非负数值规范化为 float，拒绝布尔值和非法数。"""

    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        return None
    return normalized


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    """使用 nearest-rank 定义计算百分位；空样本返回未知。"""

    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _distribution(
    values: list[float], *, include_count: bool = False
) -> dict[str, Any]:
    """计算中位数与 nearest-rank p95，并按需报告有效样本数。"""

    result: dict[str, Any] = {}
    if include_count:
        result["sample_count"] = len(values)
    result["p50"] = statistics.median(values) if values else None
    result["p95"] = _nearest_rank(values, 0.95)
    return result


def _terminal_stage_summary(
    events: Iterable[Mapping[str, Any]],
    stage: str,
) -> dict[str, int | float | None]:
    """汇总一个生成阶段的终态样本数、成功数与成功率。"""

    terminal = [
        event
        for event in events
        if event.get("component") == "reflection"
        and event.get("stage") == stage
        and event.get("status") in {"completed", "failed", "cancelled"}
    ]
    success_count = sum(event.get("status") == "completed" for event in terminal)
    return {
        "sample_count": len(terminal),
        "success_count": success_count,
        "success_rate": success_count / len(terminal) if terminal else None,
    }


def _storage_summary(events: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """汇总包含互斥结果计数的窗口写入事件。"""

    totals = {field: 0 for field in _OUTCOME_FIELDS}
    sample_count = 0
    for event in events:
        if (
            event.get("component") != "reflection"
            or event.get("stage") != "memory_write"
        ):
            continue
        values = {field: _safe_number(event.get(field)) for field in _OUTCOME_FIELDS}
        if not any(value is not None for value in values.values()):
            continue
        sample_count += 1
        for field, value in values.items():
            if value is not None:
                totals[field] += int(value)
    return {"sample_count": sample_count, **totals}


def summarize(
    source: LoadedEvents | Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """把原始事件收敛为不含正文、标识符或任意 payload 的聚合摘要。"""

    if isinstance(source, LoadedEvents):
        events = list(source.events)
        malformed_line_count = source.malformed_line_count
    else:
        events = [dict(event) for event in source if isinstance(event, Mapping)]
        malformed_line_count = 0

    provider_events = [
        event
        for event in events
        if event.get("component") == "reflection"
        and event.get("stage") == "provider"
        and event.get("status") == "completed"
    ]

    def provider_values(field: str) -> list[float]:
        """读取 Provider 完成事件中的单个安全数值字段。"""

        return [
            value
            for event in provider_events
            if (value := _safe_number(event.get(field))) is not None
        ]

    return {
        "valid_event_count": len(events),
        "malformed_line_count": malformed_line_count,
        "provider": {
            "sample_count": len(provider_events),
            "duration_ms": _distribution(provider_values("duration_ms")),
            "prompt_chars": _distribution(provider_values("prompt_chars")),
            "response_chars": _distribution(provider_values("response_chars")),
            "prompt_tokens": _distribution(
                provider_values("prompt_tokens"), include_count=True
            ),
            "completion_tokens": _distribution(
                provider_values("completion_tokens"), include_count=True
            ),
        },
        "parse": _terminal_stage_summary(events, "parse"),
        "grounding": _terminal_stage_summary(events, "grounding"),
        "storage": _storage_summary(events),
    }


def _percentage_delta(baseline: object, candidate: object) -> float | None:
    """计算候选相对基线的百分比变化；基线零值或缺失返回未知。"""

    baseline_value = _safe_number(baseline)
    candidate_value = _safe_number(candidate)
    if baseline_value in {None, 0.0} or candidate_value is None:
        return None
    return round((candidate_value - baseline_value) / baseline_value * 100.0, 6)


def compare(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """保留两侧聚合摘要，并计算固定性能与正确性指标的百分比变化。"""

    baseline_provider = baseline["provider"]
    candidate_provider = candidate["provider"]
    baseline_storage = baseline["storage"]
    candidate_storage = candidate["storage"]
    delta_percent = {
        "provider_duration_ms_p50": _percentage_delta(
            baseline_provider["duration_ms"]["p50"],
            candidate_provider["duration_ms"]["p50"],
        ),
        "provider_duration_ms_p95": _percentage_delta(
            baseline_provider["duration_ms"]["p95"],
            candidate_provider["duration_ms"]["p95"],
        ),
        "prompt_chars_p50": _percentage_delta(
            baseline_provider["prompt_chars"]["p50"],
            candidate_provider["prompt_chars"]["p50"],
        ),
        "prompt_chars_p95": _percentage_delta(
            baseline_provider["prompt_chars"]["p95"],
            candidate_provider["prompt_chars"]["p95"],
        ),
        "prompt_tokens_p50": _percentage_delta(
            baseline_provider["prompt_tokens"]["p50"],
            candidate_provider["prompt_tokens"]["p50"],
        ),
        "completion_tokens_p50": _percentage_delta(
            baseline_provider["completion_tokens"]["p50"],
            candidate_provider["completion_tokens"]["p50"],
        ),
        "parse_success_rate": _percentage_delta(
            baseline["parse"]["success_rate"],
            candidate["parse"]["success_rate"],
        ),
        "grounding_success_rate": _percentage_delta(
            baseline["grounding"]["success_rate"],
            candidate["grounding"]["success_rate"],
        ),
    }
    for field in _OUTCOME_FIELDS:
        delta_percent[field] = _percentage_delta(
            baseline_storage[field],
            candidate_storage[field],
        )
    return {
        "baseline": dict(baseline),
        "candidate": dict(candidate),
        "delta_percent": delta_percent,
    }


def _build_parser() -> argparse.ArgumentParser:
    """创建要求显式 A/B 输入和聚合输出路径的命令行解析器。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="Prompt A 诊断 JSONL")
    parser.add_argument("--candidate", required=True, help="Prompt B 诊断 JSONL")
    parser.add_argument("--output", required=True, help="聚合比较 JSON 输出路径")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """加载两组事件并写出只含聚合标量的比较报告。"""

    args = _build_parser().parse_args(argv)
    report = compare(
        summarize(load_events(args.baseline)),
        summarize(load_events(args.candidate)),
    )
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
