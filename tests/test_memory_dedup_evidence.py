"""语义去重离线校准：解码、聚合、canary 与 CLI 契约。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.features.evaluation.application.memory_dedup_evidence import (
    LOAD_REASONS,
    MIN_DISTINCT_SAMPLES,
    MIN_DUPLICATE_SAMPLES,
    MIN_RECALL,
    MIN_SCORED_SAMPLES,
    REPORT_ALLOWED_KEYS,
    REPORT_NAME,
    REPORT_SCHEMA_VERSION,
    SEMANTIC_THRESHOLD_GRID,
    CalibrationCandidate,
    CalibrationSample,
    build_calibration_report,
    check_report_privacy,
    forbidden_values_from_payload,
    load_calibration_fixture,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLI = _REPO_ROOT / "scripts" / "benchmark_memory_dedup_semantic.py"
_QUERY_CANARY = "机密正文：项目代号北极星，结算日六月三十日"


def _sample_payload(
    *,
    label: str = "duplicate",
    scores: list[float | None] | None = None,
    duplicate_index: int | None = 0,
) -> dict[str, object]:
    """构造一条匿名 fixture 样本。"""

    values = [0.95, 0.4] if scores is None else scores
    return {
        "label": label,
        "query": f"{_QUERY_CANARY}-{label}",
        "candidates": [
            {
                "content": f"候选正文 {index} 用于 canary 检查",
                "semantic_score": score,
            }
            for index, score in enumerate(values)
        ],
        "duplicate_candidate_index": duplicate_index,
    }


def _fixture(samples: list[dict[str, object]]) -> dict[str, object]:
    """包装为带 schema 版本的 fixture。"""

    return {"schema_version": REPORT_SCHEMA_VERSION, "samples": samples}


def _paired_samples(
    *,
    duplicate_count: int = 12,
    distinct_count: int = 12,
    duplicate_score: float = 0.95,
    distinct_score: float = 0.4,
) -> list[dict[str, object]]:
    """构造胜负分明的样本集合；默认规模满足证据门下限。"""

    samples = [
        _sample_payload(scores=[duplicate_score, 0.2]) for _ in range(duplicate_count)
    ]
    samples += [
        _sample_payload(
            label="distinct",
            scores=[distinct_score, 0.1],
            duplicate_index=None,
        )
        for _ in range(distinct_count)
    ]
    return samples


def _report(
    samples: list[dict[str, object]],
    *,
    forbidden_values: tuple[str, ...] = (),
):
    """从 payload 构造报告。"""

    decoded, reason = load_calibration_fixture(_fixture(samples))
    assert reason == ""
    return build_calibration_report(
        decoded, now_ms=1, forbidden_values=forbidden_values
    )


def test_valid_fixture_decodes_all_samples() -> None:
    """合法 fixture 完整解码，分数缺失保留为 unavailable 候选。"""

    decoded, reason = load_calibration_fixture(
        _fixture(
            [
                _sample_payload(),
                _sample_payload(label="distinct", scores=None, duplicate_index=None),
            ]
        )
    )

    assert reason == ""
    assert len(decoded) == 2
    assert decoded[0].label == "duplicate"
    assert decoded[0].duplicate_index == 0
    assert decoded[1].candidates[0].semantic_score == 0.95


@pytest.mark.parametrize(
    "payload,expected",
    [
        ([], "fixture_not_object"),
        ({"schema_version": 99, "samples": []}, "unsupported_schema_version"),
        ({"schema_version": 1, "samples": []}, "samples_out_of_range"),
        ({"schema_version": 1, "samples": "x"}, "samples_invalid"),
        (
            {"schema_version": 1, "samples": [], "extra": 1},
            "unknown_key",
        ),
        (
            _fixture([{"label": "maybe", "query": "q", "candidates": []}]),
            "sample_invalid_label",
        ),
        (
            _fixture([{"label": "duplicate", "query": " ", "candidates": []}]),
            "sample_text_invalid",
        ),
        (
            _fixture([{"label": "duplicate", "query": "q", "candidates": []}]),
            "candidates_invalid",
        ),
        (
            _fixture(
                [
                    {
                        "label": "duplicate",
                        "query": "q",
                        "candidates": [{"content": "c", "semantic_score": 1.5}],
                        "duplicate_candidate_index": 0,
                    }
                ]
            ),
            "candidate_score_invalid",
        ),
        (
            _fixture(
                [
                    {
                        "label": "duplicate",
                        "query": "q",
                        "candidates": [{"content": "c"}],
                    }
                ]
            ),
            "duplicate_index_missing",
        ),
        (
            _fixture(
                [
                    {
                        "label": "distinct",
                        "query": "q",
                        "candidates": [{"content": "c"}],
                        "duplicate_candidate_index": 0,
                    }
                ]
            ),
            "duplicate_index_unexpected",
        ),
        (
            _fixture(
                [
                    {
                        "label": "distinct",
                        "query": "q",
                        "candidates": [{"content": "c"}],
                        "duplicate_candidate_index": 4,
                    }
                ]
            ),
            "duplicate_index_invalid",
        ),
        (
            _fixture(
                [
                    {
                        "label": "distinct",
                        "query": "q",
                        "candidates": [{"content": "c", "scope_key": "s"}],
                    }
                ]
            ),
            "unknown_key",
        ),
    ],
)
def test_malformed_fixtures_fail_closed(payload: object, expected: str) -> None:
    """坏输入返回闭集原因，绝不降级成空样本报告。"""

    samples, reason = load_calibration_fixture(payload)

    assert samples == ()
    assert reason == expected
    assert reason in LOAD_REASONS


def test_report_aggregates_grid_and_passes_gate() -> None:
    """阈值网格的混淆矩阵、比率与推荐阈值必须等于手算值。"""

    report = _report(_paired_samples())

    assert report["report"] == REPORT_NAME
    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert report["capability"] == "fixture_scores"
    assert report["grid"] == list(SEMANTIC_THRESHOLD_GRID)
    assert report["samples"] == {
        "total": 24,
        "scored": 24,
        "unavailable": 0,
        "duplicate": 12,
        "distinct": 12,
    }
    assert len(report["thresholds"]) == len(SEMANTIC_THRESHOLD_GRID)
    for row in report["thresholds"]:
        assert row["true_positive"] == 12
        assert row["false_negative"] == 0
        assert row["false_positive"] == 0
        assert row["true_negative"] == 12
        assert row["precision"] == 1.0
        assert row["recall"] == 1.0
    assert report["top1_rate"] == 1.0
    assert report["gate"] == {
        "status": "pass",
        "reason": "precision_and_recall_floor_met",
    }
    assert report["recommended_threshold"] == max(SEMANTIC_THRESHOLD_GRID)


def test_report_recommends_highest_passing_threshold() -> None:
    """存在假阳性时推荐仍满足护栏的最高阈值。"""

    report = _report(_paired_samples(duplicate_score=0.93, distinct_score=0.91))

    rows = {row["threshold"]: row for row in report["thresholds"]}
    assert rows[0.80]["false_positive"] == 12
    assert rows[0.80]["precision"] == pytest.approx(12 / 24)
    assert rows[0.92]["false_positive"] == 0
    # 0.95 之上没有候选达标：precision 按 0 处理，不会成为推荐。
    assert rows[0.95]["true_positive"] == 0
    assert report["recommended_threshold"] == 0.92
    assert report["gate"]["status"] == "pass"


def test_report_rejects_when_floors_are_unreachable() -> None:
    """所有阈值都达不到护栏时返回 reject 且不给推荐。"""

    report = _report(_paired_samples(distinct_score=0.99))

    assert report["gate"] == {
        "status": "reject",
        "reason": "precision_or_recall_below_floor",
    }
    assert report["recommended_threshold"] is None


def test_report_requires_minimum_evidence() -> None:
    """样本不足时返回 insufficient_evidence，而不是给出推荐。"""

    report = _report(_paired_samples(duplicate_count=2, distinct_count=1))

    assert report["gate"] == {
        "status": "insufficient_evidence",
        "reason": "sample_floor_not_met",
    }
    assert report["recommended_threshold"] is None
    assert MIN_SCORED_SAMPLES >= MIN_DUPLICATE_SAMPLES + MIN_DISTINCT_SAMPLES


def test_unavailable_scores_are_excluded_and_reported() -> None:
    """缺失分数的样本计入 unavailable，所有分数缺失时 capability=unavailable。"""

    decoded, reason = load_calibration_fixture(
        _fixture(
            [
                _sample_payload(scores=[None, None]),
                _sample_payload(
                    label="distinct", scores=[None, None], duplicate_index=None
                ),
            ]
        )
    )
    assert reason == ""
    report = build_calibration_report(decoded, now_ms=1)

    assert report["capability"] == "unavailable"
    assert report["samples"] == {
        "total": 2,
        "scored": 0,
        "unavailable": 2,
        "duplicate": 0,
        "distinct": 0,
    }
    assert report["top1_rate"] is None
    assert report["gate"]["status"] == "insufficient_evidence"
    assert report["recommended_threshold"] is None


def test_recall_floor_is_enforced_independently() -> None:
    """召回率不足时即使精确率为 1.0 也不通过。"""

    samples = [_sample_payload() for _ in range(15)]
    samples += [_sample_payload(scores=[0.4, 0.1]) for _ in range(5)]
    samples += [
        _sample_payload(label="distinct", scores=[0.2, 0.1], duplicate_index=None)
        for _ in range(MIN_DISTINCT_SAMPLES)
    ]
    report = _report(samples)

    assert report["samples"]["scored"] == 25
    for row in report["thresholds"]:
        assert row["precision"] == 1.0
        assert row["recall"] == pytest.approx(15 / 20)
        assert row["recall"] < MIN_RECALL
    assert report["gate"]["status"] == "reject"


def test_confusion_matrix_counts_every_sample_once() -> None:
    """每个样本在每个阈值下只计入一个格。"""

    report = _report(_paired_samples(duplicate_count=6, distinct_count=6))

    for row in report["thresholds"]:
        total = (
            row["true_positive"]
            + row["false_positive"]
            + row["true_negative"]
            + row["false_negative"]
        )
        assert total == 12


def test_report_passes_canary_and_never_contains_fixture_text() -> None:
    """报告不包含 fixture 的 query/正文，且 canary 计数为 0。"""

    payload = _fixture(
        [
            _sample_payload(),
            _sample_payload(label="distinct", scores=[0.3, 0.1], duplicate_index=None),
        ]
    )
    forbidden = forbidden_values_from_payload(payload)
    decoded, _reason = load_calibration_fixture(payload)
    report = build_calibration_report(decoded, now_ms=1, forbidden_values=forbidden)

    serialized = json.dumps(report, ensure_ascii=False)
    assert _QUERY_CANARY not in serialized
    assert "候选正文" not in serialized
    assert report["privacy_canary"] == {
        "checked_values": len(forbidden),
        "violations": 0,
    }
    assert check_report_privacy(report, forbidden) == []


def test_canary_rejects_leaked_values_and_forbidden_keys() -> None:
    """注入泄露值或禁止键时 canary fail-closed，且违规信息只含路径。"""

    leaky = {
        "schema_version": 1,
        "report": REPORT_NAME,
        "gate": {"status": "pass", "reason": "ok", "session_id": "s-1"},
        "top1_rate": _QUERY_CANARY,
    }

    violations = check_report_privacy(leaky, (_QUERY_CANARY,))

    assert violations
    assert all("s-1" not in item for item in violations)
    assert all(_QUERY_CANARY not in item for item in violations)
    assert any(item.endswith("forbidden_key_marker") for item in violations)
    assert any(item.endswith("value_leak") for item in violations)


def test_canary_rejects_unknown_keys_and_overlong_strings() -> None:
    """白名单之外的键与超长字符串同样 fail-closed。"""

    violations = check_report_privacy(
        {"schema_version": 1, "secret_extra": "x" * 400},
        (),
    )

    assert violations
    assert any(item.endswith("key_not_allowed") for item in violations)
    assert all("x" * 10 not in item for item in violations)
    assert "samples" in REPORT_ALLOWED_KEYS


def test_cli_help_runs_without_runtime() -> None:
    """`--help` 必须在没有 AstrBot 实例的环境下可用。"""

    result = subprocess.run(
        [sys.executable, str(_CLI), "--help"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )

    assert result.returncode == 0
    assert "--fixture" in result.stdout
    assert "--dry-run" in result.stdout


def test_cli_requires_fixture() -> None:
    """缺少必填参数时以用法错误退出，不执行校准。"""

    result = subprocess.run(
        [sys.executable, str(_CLI)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )

    assert result.returncode == 2


def test_cli_dry_run_prints_report_and_writes_nothing(tmp_path) -> None:
    """dry-run 即使给出 --output 也不写文件。"""

    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(_fixture(_paired_samples())), encoding="utf-8")
    output = tmp_path / "report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(_CLI),
            "--fixture",
            str(fixture),
            "--output",
            str(output),
            "--dry-run",
            "--now-ms",
            "1",
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["report"] == REPORT_NAME
    assert report["privacy_canary"]["violations"] == 0
    assert not output.exists()
    # fixture 只读：dry-run 不得改写输入。
    assert json.loads(fixture.read_text(encoding="utf-8"))["schema_version"] == 1


def test_cli_writes_report_to_explicit_output(tmp_path) -> None:
    """显式 --output 时写报告文件并返回证据门状态。"""

    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(_fixture(_paired_samples(distinct_score=0.99))), encoding="utf-8"
    )
    output = tmp_path / "report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(_CLI),
            "--fixture",
            str(fixture),
            "--output",
            str(output),
            "--now-ms",
            "7",
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )

    assert result.returncode == 1, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["generated_at_ms"] == 7
    assert report["gate"]["status"] == "reject"
    assert report["recommended_threshold"] is None


def test_cli_rejects_malformed_fixture(tmp_path) -> None:
    """坏 fixture 以用法错误退出，不输出看似成功的报告。"""

    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"schema_version": 1, "samples": []}), "utf-8")

    result = subprocess.run(
        [sys.executable, str(_CLI), "--fixture", str(fixture)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )

    assert result.returncode == 2
    assert "invalid fixture" in result.stderr
    assert "samples_out_of_range" in result.stderr


def test_cli_missing_fixture_file_is_usage_error(tmp_path) -> None:
    """fixture 路径不存在时以用法错误退出。"""

    result = subprocess.run(
        [sys.executable, str(_CLI), "--fixture", str(tmp_path / "missing.json")],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )

    assert result.returncode == 2


def test_cli_fails_closed_on_canary_violation(tmp_path, monkeypatch) -> None:
    """canary 违规时不输出报告，以退出码 3 结束。"""

    import scripts.benchmark_memory_dedup_semantic as cli

    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(_fixture(_paired_samples())), encoding="utf-8")
    leaky = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report": REPORT_NAME,
        "gate": {"status": "pass", "reason": "ok"},
        "top1_rate": _QUERY_CANARY,
        "privacy_canary": {"checked_values": 1, "violations": 1},
    }
    monkeypatch.setattr(cli, "build_calibration_report", lambda *a, **k: dict(leaky))

    exit_code = cli.main(["--fixture", str(fixture), "--dry-run"])

    assert exit_code == 3


def test_calibration_sample_dataclass_is_immutable() -> None:
    """样本 DTO 冻结，避免聚合过程被就地改写。"""

    sample = CalibrationSample(
        label="duplicate",
        query="q",
        candidates=(CalibrationCandidate(content="c", semantic_score=0.9),),
        duplicate_index=0,
    )

    with pytest.raises(Exception):
        sample.label = "distinct"  # type: ignore[misc]
