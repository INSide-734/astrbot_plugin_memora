"""反思 Prompt A/B 聚合分析器契约。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.analyze_reflection_ab import compare, load_events, main, summarize


def _write_jsonl(path: Path, lines: list[object]) -> None:
    """写入合成 JSONL；字符串条目按原样写入以构造坏行。"""

    serialized = [
        item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        for item in lines
    ]
    path.write_text("\n".join(serialized) + "\n", encoding="utf-8")


def _provider_event(
    duration_ms: float,
    *,
    prompt_chars: int,
    prompt_tokens: int,
    completion_tokens: int,
) -> dict[str, object]:
    """构造 Provider 完成事件。"""

    return {
        "event": "storage_task",
        "component": "reflection",
        "stage": "provider",
        "status": "completed",
        "duration_ms": duration_ms,
        "prompt_chars": prompt_chars,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


def test_load_and_summarize_counts_malformed_lines_and_safe_metrics(
    tmp_path: Path,
) -> None:
    """坏行必须单独计数，合法事件只汇总允许的性能与结果标量。"""

    path = tmp_path / "sample.jsonl"
    _write_jsonl(
        path,
        [
            _provider_event(
                100.0,
                prompt_chars=1000,
                prompt_tokens=100,
                completion_tokens=40,
            ),
            _provider_event(
                300.0,
                prompt_chars=1400,
                prompt_tokens=140,
                completion_tokens=60,
            ),
            {
                "event": "recall_stage",
                "component": "recall",
                "stage": "provider",
                "status": "completed",
                "duration_ms": 1.0,
            },
            {
                "event": "storage_task",
                "component": "reflection",
                "stage": "parse",
                "status": "completed",
            },
            {
                "event": "storage_task",
                "component": "reflection",
                "stage": "parse",
                "status": "failed",
            },
            {
                "event": "storage_task",
                "component": "reflection",
                "stage": "grounding",
                "status": "completed",
            },
            {
                "event": "storage_task",
                "component": "reflection",
                "stage": "memory_write",
                "reason_code": "memory_write_completed",
                "canonical_count": 2,
                "quarantine_count": 1,
                "failed_count": 0,
                "skipped_idempotent_count": 1,
            },
            "{bad json",
            ["not", "an", "object"],
            "",
        ],
    )

    summary = summarize(load_events(path))

    assert summary["valid_event_count"] == 7
    assert summary["malformed_line_count"] == 2
    assert summary["provider"]["sample_count"] == 2
    assert summary["provider"]["duration_ms"] == {"p50": 200.0, "p95": 300.0}
    assert summary["provider"]["prompt_chars"] == {"p50": 1200.0, "p95": 1400.0}
    assert summary["provider"]["completion_tokens"] == {
        "sample_count": 2,
        "p50": 50.0,
        "p95": 60.0,
    }
    assert summary["parse"] == {
        "sample_count": 2,
        "success_count": 1,
        "success_rate": 0.5,
    }
    assert summary["grounding"]["success_rate"] == 1.0
    assert summary["storage"] == {
        "sample_count": 1,
        "canonical_count": 2,
        "quarantine_count": 1,
        "failed_count": 0,
        "skipped_idempotent_count": 1,
    }


def test_compare_reports_candidate_percentage_deltas() -> None:
    """比较结果必须同时保留两侧摘要并计算 B 相对 A 的百分比变化。"""

    baseline = summarize(
        [
            _provider_event(
                100.0,
                prompt_chars=1000,
                prompt_tokens=100,
                completion_tokens=40,
            ),
            _provider_event(
                300.0,
                prompt_chars=1400,
                prompt_tokens=140,
                completion_tokens=60,
            ),
            {"component": "reflection", "stage": "parse", "status": "completed"},
            {"component": "reflection", "stage": "parse", "status": "failed"},
        ]
    )
    candidate = summarize(
        [
            _provider_event(
                50.0,
                prompt_chars=500,
                prompt_tokens=50,
                completion_tokens=20,
            ),
            _provider_event(
                100.0,
                prompt_chars=700,
                prompt_tokens=70,
                completion_tokens=30,
            ),
            {"component": "reflection", "stage": "parse", "status": "completed"},
            {"component": "reflection", "stage": "parse", "status": "completed"},
        ]
    )

    report = compare(baseline, candidate)

    assert report["baseline"] == baseline
    assert report["candidate"] == candidate
    assert report["delta_percent"]["provider_duration_ms_p50"] == -62.5
    assert report["delta_percent"]["provider_duration_ms_p95"] == pytest.approx(
        -66.666667
    )
    assert report["delta_percent"]["prompt_chars_p50"] == -50.0
    assert report["delta_percent"]["completion_tokens_p50"] == -50.0
    assert report["delta_percent"]["parse_success_rate"] == 100.0


def test_cli_writes_aggregate_json_without_source_payload(
    tmp_path: Path,
) -> None:
    """CLI 输出不得复制诊断原始事件或未知敏感字段。"""

    baseline_path = tmp_path / "a.jsonl"
    candidate_path = tmp_path / "b.jsonl"
    output_path = tmp_path / "report.json"
    canary = "PRIVATE-CONTENT-CANARY"
    event = _provider_event(
        10.0,
        prompt_chars=100,
        prompt_tokens=10,
        completion_tokens=4,
    )
    event["content"] = canary
    _write_jsonl(baseline_path, [event])
    _write_jsonl(candidate_path, [event])

    assert (
        main(
            [
                "--baseline",
                str(baseline_path),
                "--candidate",
                str(candidate_path),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )

    output = output_path.read_text(encoding="utf-8")
    assert canary not in output
    assert "content" not in output
    assert json.loads(output)["baseline"]["provider"]["sample_count"] == 1
