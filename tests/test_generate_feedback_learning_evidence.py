"""离线反馈学习 Evidence 生成脚本的权限与输入契约测试。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.evaluation.feedback_learning_evidence_store import (
    FeedbackLearningEvidenceInbox,
)
from core.features.learning.domain.feedback_learning_evidence_contract import (
    REQUIRED_EVIDENCE_REGRESSION_CHECKS,
)
from scripts import generate_feedback_learning_evidence


def _sha256(value: str) -> str:
    """为测试匿名标识计算稳定 SHA-256。"""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _replay_document(*, regression_failures: list[str] | None = None) -> dict:
    """构造能证明 shadow 排序改善的完整匿名回放文档。"""

    started = datetime(2026, 8, 3, 2, tzinfo=timezone.utc)
    cases = []
    for index in range(8):
        relevant = _sha256(f"relevant-{index}")
        cases.append(
            {
                "case_id": _sha256(f"case-{index}"),
                "query_hash": _sha256(f"query-{index}"),
                "group_hash": _sha256(f"group-{index % 2}"),
                "relevant_doc_hashes": [relevant],
                "expected_no_hit": False,
                "baseline_candidates": [
                    {
                        "doc_hash": _sha256(f"noise-{index}"),
                        "score": 0.8,
                        "route": "graph",
                    },
                    {
                        "doc_hash": relevant,
                        "score": 0.75,
                        "route": "document",
                    },
                ],
                "observed_at_utc": (started + timedelta(minutes=index)).isoformat(),
                "baseline_stage_latencies_ms": {
                    "candidate_generation": 120.0 + index,
                    "rerank": 80.0 + index,
                },
                "shadow_stage_latencies_ms": {
                    "candidate_generation": 108.0 + index,
                    "rerank": 72.0 + index,
                },
                "baseline_ttft_ms": 400.0 + index,
                "shadow_ttft_ms": 375.0 + index,
                "baseline_provider_calls": 2.0,
                "shadow_provider_calls": 2.0,
                "baseline_token_cost": 100.0 + index,
                "shadow_token_cost": 93.0 + index,
            }
        )
    return {
        "schema_version": "feedback-learning-evidence-input-v1",
        "aggregation_revision": "a" * 64,
        "source_config_revision": "b" * 64,
        "quality_gate_version": "quality-gate-v1",
        "dataset_version": "feedback-ranking-controlled-v1",
        "k": 1,
        "aggregate": {
            "window_start_utc": "2026-08-03T00:00:00+00:00",
            "window_end_utc": "2026-08-03T01:00:00+00:00",
            "accepted_count": 16,
            "independent_window_count": 2,
            "decayed_support": 0.9,
            "baseline_document_weight": 0.7,
            "baseline_graph_weight": 0.3,
            "target_document_weight": 0.8,
            "target_graph_weight": 0.2,
            "policy_version": 1,
        },
        "cases": cases,
        "regression_checks": sorted(REQUIRED_EVIDENCE_REGRESSION_CHECKS),
        "regression_failures": regression_failures or [],
    }


def _write_replay(path: Path, document: dict) -> None:
    """把测试回放写成 UTF-8 JSON。"""

    path.write_text(
        json.dumps(document, ensure_ascii=True, allow_nan=False),
        encoding="utf-8",
    )


def test_generate_ready_evidence_into_fixed_inbox(
    tmp_path: Path,
    capsys,
) -> None:
    """完整匿名回放只写固定 Inbox，并返回可审计低敏摘要。"""

    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    replay_path = tmp_path / "replay.json"
    _write_replay(replay_path, _replay_document())

    exit_code = generate_feedback_learning_evidence.main(
        ["--data-dir", str(data_dir), "--input", str(replay_path)]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "evidence_revision": output["evidence_revision"],
        "passed": True,
        "reason_codes": [],
        "status": "ready",
    }
    assert len(output["evidence_revision"]) == 64
    inbox = FeedbackLearningEvidenceInbox(data_dir)
    assert inbox.current_path.is_file()
    assert inbox.artifact_path(output["evidence_revision"]).is_file()
    assert sorted(path.name for path in data_dir.iterdir()) == ["evaluation"]


def test_rejected_gate_is_persisted_but_exits_nonzero(
    tmp_path: Path,
    capsys,
) -> None:
    """完整但未通过回归门的 artifact 可审计，命令必须非零退出。"""

    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    replay_path = tmp_path / "replay.json"
    _write_replay(
        replay_path,
        _replay_document(regression_failures=["privacy_regression"]),
    )

    exit_code = generate_feedback_learning_evidence.main(
        ["--data-dir", str(data_dir), "--input", str(replay_path)]
    )

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "rejected"
    assert output["passed"] is False
    assert output["reason_codes"] == ["regression_failures_present"]
    assert FeedbackLearningEvidenceInbox(data_dir).current_path.is_file()


def test_input_rejects_unknown_fields_and_raw_identifiers(
    tmp_path: Path,
    capsys,
) -> None:
    """未知字段与非 SHA-256 标识必须在创建 Inbox 前 fail-closed。"""

    for name, mutate in (
        (
            "unknown-field",
            lambda document: document.update({"config_writer": "forbidden"}),
        ),
        (
            "raw-query",
            lambda document: document["cases"][0].update(
                {"query_hash": "raw private query"}
            ),
        ),
    ):
        data_dir = tmp_path / name
        data_dir.mkdir()
        replay_path = tmp_path / f"{name}.json"
        document = _replay_document()
        mutate(document)
        _write_replay(replay_path, document)

        exit_code = generate_feedback_learning_evidence.main(
            ["--data-dir", str(data_dir), "--input", str(replay_path)]
        )

        assert exit_code == 2
        output = json.loads(capsys.readouterr().err)
        assert output == {
            "error": {"code": "evidence_input_invalid", "retryable": False},
            "status": "error",
        }
        assert not FeedbackLearningEvidenceInbox(data_dir).directory.exists()


def test_duplicate_json_key_is_rejected_without_echoing_input(
    tmp_path: Path,
    capsys,
) -> None:
    """重复键不得覆盖已校验字段，错误输出也不得回显原始内容。"""

    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    replay_path = tmp_path / "replay.json"
    replay_path.write_text(
        '{"schema_version":"feedback-learning-evidence-input-v1",'
        '"schema_version":"SECRET-CANARY"}',
        encoding="utf-8",
    )

    exit_code = generate_feedback_learning_evidence.main(
        ["--data-dir", str(data_dir), "--input", str(replay_path)]
    )

    assert exit_code == 2
    output = capsys.readouterr().err
    assert "SECRET-CANARY" not in output
    assert json.loads(output)["error"]["code"] == "evidence_input_invalid"


def test_help_is_executable_and_script_has_no_config_write_authority() -> None:
    """真实命令可显示帮助，源码不得导入生产配置写适配器。"""

    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "generate_feedback_learning_evidence.py"
    )
    completed = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        cwd=script_path.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "--data-dir" in completed.stdout
    assert "--input" in completed.stdout
    source = script_path.read_text(encoding="utf-8")
    assert "ConfigManager" not in source
    assert "learning_config_adapter" not in source
