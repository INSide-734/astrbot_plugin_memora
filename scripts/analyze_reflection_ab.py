"""比较两组反思诊断 JSONL 的隐私安全聚合指标。"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
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
    rank = max(1, math.ceil(percentile / 100.0 * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _distribution(
    values: list[float], *, include_count: bool = False
) -> dict[str, Any]:
    """计算中位数与 nearest-rank p95，并按需报告有效样本数。"""

    result: dict[str, Any] = {}
    if include_count:
        result["sample_count"] = len(values)
    result["p50"] = statistics.median(values) if values else None
    result["p95"] = _nearest_rank(values, 95)
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


def aggregate_candidate_metrics(
    db_path: str,
    time_range: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """聚合候选指标为隐私安全的统计摘要（不含敏感信息）。"""

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 构造时间范围过滤条件
    time_filter = ""
    time_params: tuple[Any, ...] = ()
    if time_range is not None:
        time_filter = " AND created_at BETWEEN ? AND ?"
        time_params = time_range

    # 1. 窗口级候选产出率（按 mode 分层）
    # 口径：非零候选窗口 / 总窗口，不是 occurrence 级复用率。
    reuse_rate: dict[str, dict[str, int | float]] = {}
    for mode in ["observe", "top_k", "full", "off"]:
        row = conn.execute(
            f"SELECT COUNT(*) as n_windows, "
            f"SUM(CASE WHEN n_candidates > 0 THEN 1 ELSE 0 END) as n_with "
            f"FROM topic_candidate_metric_windows WHERE mode = ?{time_filter}",
            (mode,) + time_params,
        ).fetchone()
        if row and row["n_windows"] > 0:
            reuse_rate[mode] = {
                "rate": row["n_with"] / row["n_windows"],
                "n_windows": row["n_windows"],
            }

    # 2. provenance 覆盖率
    prov_row = conn.execute(
        f"SELECT SUM(n_candidates) as total, SUM(n_with_provenance) as with_prov "
        f"FROM topic_candidate_metric_windows WHERE n_candidates > 0{time_filter}",
        time_params,
    ).fetchone()
    # 缺失保持缺失（None），不以 0.0 伪装（评测 AGENTS 不变量 5）
    provenance_coverage: dict[str, float | None] = {
        "mean": (
            prov_row["with_prov"] / prov_row["total"]
            if prov_row and prov_row["total"] and prov_row["total"] > 0
            else None
        )
    }

    # 3. selector 延迟分布
    latencies = [
        row[0]
        for row in conn.execute(
            f"SELECT selector_latency_ms FROM topic_candidate_metric_windows "
            f"WHERE selector_latency_ms IS NOT NULL{time_filter} "
            f"ORDER BY selector_latency_ms",
            time_params,
        ).fetchall()
    ]

    # 无有效延迟样本时保持空 dict，不输出 0.0 伪装分位数
    selector_latency_ms: dict[str, float | None] = {}
    if latencies:
        selector_latency_ms = {
            "p50": _nearest_rank(latencies, 50.0),
            "p90": _nearest_rank(latencies, 90.0),
            "p95": _nearest_rank(latencies, 95.0),
            "p99": _nearest_rank(latencies, 99.0),
        }

    # 4. baseline 降级原因分布
    reason_rows = conn.execute(
        f"SELECT reason, COUNT(*) as count FROM topic_candidate_metric_windows "
        f"WHERE reason IS NOT NULL{time_filter} GROUP BY reason",
        time_params,
    ).fetchall()
    baseline_reasons = {row["reason"]: row["count"] for row in reason_rows}

    # 5. token 预算使用：mean 缺失时保持 None
    token_row = conn.execute(
        f"SELECT AVG(n_tokens) as mean, "
        f"SUM(CASE WHEN n_tokens IS NULL THEN 1 ELSE 0 END) as n_missing, "
        f"SUM(CASE WHEN n_tokens IS NOT NULL THEN 1 ELSE 0 END) as n_valid "
        f"FROM topic_candidate_metric_windows"
        f"{' WHERE created_at BETWEEN ? AND ?' if time_range else ''}",
        time_params,
    ).fetchone()
    token_budget: dict[str, float | int | None] = {
        "mean": (
            token_row["mean"] if token_row and token_row["mean"] is not None else None
        ),
        "n_missing": token_row["n_missing"] if token_row else 0,
        "n_valid": token_row["n_valid"] if token_row else 0,
    }

    conn.close()
    return {
        "candidate_metrics": {
            "reuse_rate": reuse_rate,
            "provenance_coverage": provenance_coverage,
            "selector_latency_ms": selector_latency_ms,
            "baseline_reasons": baseline_reasons,
            "token_budget": token_budget,
        }
    }


def _percentage_delta(baseline: object, candidate: object) -> float | None:
    """计算候选相对基线的百分比变化；基线零值或缺失返回未知。"""

    baseline_value = _safe_number(baseline)
    candidate_value = _safe_number(candidate)
    if baseline_value is None or baseline_value == 0.0 or candidate_value is None:
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
    parser.add_argument("--baseline", help="Prompt A 诊断 JSONL 或数据库路径")
    parser.add_argument("--candidate", help="Prompt B 诊断 JSONL 或数据库路径")
    parser.add_argument("--output", required=True, help="聚合比较 JSON 输出路径")
    parser.add_argument(
        "--include-candidates",
        action="store_true",
        help="从数据库聚合候选指标（baseline/candidate 需为 .db 路径）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """加载两组事件并写出只含聚合标量的比较报告。"""

    args = _build_parser().parse_args(argv)

    # 支持两种模式：JSONL 事件或数据库候选指标
    if args.include_candidates:
        # 数据库模式：聚合候选指标；强制 .db 后缀防止误传生产
        # conversations.db 为 JSONL 或反之（双语义参数的唯一防线）
        if not args.baseline or not args.candidate:
            print(
                "错误：--include-candidates 需要 --baseline 和 --candidate 为 .db 路径"
            )
            return 1
        for label, value in (
            ("baseline", args.baseline),
            ("candidate", args.candidate),
        ):
            if not value.endswith(".db"):
                print(f"错误：{label} 在 --include-candidates 模式必须是 .db 文件")
                return 1
        report = {
            "baseline": aggregate_candidate_metrics(args.baseline),
            "candidate": aggregate_candidate_metrics(args.candidate),
        }
    else:
        # JSONL 模式：现有反思事件聚合
        if not args.baseline or not args.candidate:
            print("错误：需要 --baseline 和 --candidate JSONL 路径")
            return 1
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
