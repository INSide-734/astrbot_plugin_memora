"""离线 benchmark 的真实 CLI 报告和 selector 失败计数。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.features.reflection.application.topic_candidate_selector import (
    TopicCandidateSelector,
)
from core.features.reflection.domain.summary_models import TopicCandidateSelection
from scripts.benchmark_topic_candidates import (
    BenchmarkConfig,
    _run_benchmark,
    _run_paired_replay,
)

# 显式传仓库根为 cwd，去除 cwd=仓库根的隐含假设
_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_missing_required_args():
    """缺少必填输入时不得执行 benchmark 或输出貌似成功的报告。"""
    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "benchmark_topic_candidates.py")],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    assert result.returncode == 2


def test_full_execution_publishes_every_actual_stratum_and_success_status(tmp_path):
    """CLI 输出可消费的全量结果，精确规模与成功 K24 配对来自实际 selector。"""
    output = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "scripts" / "benchmark_topic_candidates.py"),
            "--output",
            str(output),
            "--oracle-max-topics",
            "50",
            "--oracle-max-prompt-tokens",
            "8000",
            "--misreuse-max-by-bucket",
            '{"tiny":5,"small":10,"medium":15,"large":20,"huge":25}',
            "--windows-per-bucket",
            "1",
            "--negative-windows-per-bucket",
            "1",
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    # 护栏拒绝（recommended_k=None）以退出码 1 表达（P2：benchmark 不得以 0 伪装通过）
    assert result.returncode == 1, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["replay_execution"] == "actual_selector_replay"
    # tiny=4、small=16；其余四个受控 scope 被 CLI 上限截为实际 medium=50。
    assert {
        (entry["scale_bucket"], entry["catalog_topic_count"])
        for entry in report["bucket_results"]
    } == {("tiny", 4), ("small", 16), ("medium", 50)}
    assert len(report["bucket_results"]) == 3 * 2 * 5
    assert report["paired_case_count"] == 16
    assert report["variant_record_counts"]["top_k_24"] == 24
    assert report["variant_status_counts"]["top_k_24"] == {"success": 16, "degraded": 8}
    assert report["variant_status_counts"]["strict_full"] == {"success": 24}
    assert report["pipeline_validation_only"] is True
    assert report["recommended_k"] is report["recommended_activation_threshold"] is None
    assert report["e2e_provider_p95"] is None
    assert all(entry["decision"] == "reject" for entry in report["bucket_results"])
    assert all(
        entry["latency_sample_count"] == entry["token_sample_count"] == 0
        for entry in report["bucket_results"]
    )
    assert all(
        mode == "observe" for mode in report["recommended_bucket_modes"].values()
    )


@pytest.mark.asyncio
async def test_selector_fallbacks_do_not_count_as_successful_pairs(monkeypatch):
    """生产 selector 返回降级结果而非抛异常时，配对成功数仍必须为零。"""

    async def degraded(*_args, **_kwargs):
        return TopicCandidateSelection(reason_code="selector_exception_RuntimeError")

    monkeypatch.setattr(TopicCandidateSelector, "select_candidates", degraded)
    config = BenchmarkConfig(4, 256, {"tiny": 5.0})
    records, paired, variants, statuses = await _run_paired_replay(config, 1, 1)
    assert paired == 0
    assert statuses["strict_full"] == {"success": 24}
    assert statuses["top_k_24"] == {"degraded": 24}
    report = _run_benchmark(records, config, 200, 200, 200, paired, variants, statuses)
    assert report["recommended_k"] is None
    assert all(
        entry["quality_sample_count"] == entry["negative_sample_count"] == 0
        for entry in report["bucket_results"]
    )
    assert all(
        "replay_not_successful" in entry["reason_codes"]
        for entry in report["bucket_results"]
    )
