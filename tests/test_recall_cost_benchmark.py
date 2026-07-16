"""Behavioral contracts for the dedicated recall-cost benchmark."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_PATH = REPO_ROOT / "scripts" / "benchmark_recall_cost.py"


def _load_benchmark_module():
    spec = importlib.util.spec_from_file_location("benchmark_recall_cost", BENCHMARK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_total_recall_regression_accepts_exact_five_percent_boundary() -> None:
    benchmark = _load_benchmark_module()

    result = benchmark.evaluate_total_path_regression(
        current_p95_ms=105.0,
        baseline_p95_ms=100.0,
    )

    assert result["regression_ratio"] == pytest.approx(0.05)
    assert result["passed"] is True


def test_total_recall_regression_rejects_value_above_five_percent() -> None:
    benchmark = _load_benchmark_module()

    result = benchmark.evaluate_total_path_regression(
        current_p95_ms=105.01,
        baseline_p95_ms=100.0,
    )

    assert result["regression_ratio"] > 0.05
    assert result["passed"] is False


def test_total_recall_baseline_rejects_unversioned_or_nonpositive_data(
    tmp_path: Path,
) -> None:
    benchmark = _load_benchmark_module()
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps({"schema_version": 1, "p95_ms": 0}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="baseline"):
        benchmark.load_total_path_baseline(path)


def test_handler_worker_injects_through_the_real_source_adapter() -> None:
    benchmark = _load_benchmark_module()

    result = benchmark.run_handler_worker(
        REPO_ROOT,
        warmup_runs=0,
        measured_runs=2,
        retrieval_delay_ms=0.0,
    )

    assert result["injected_runs"] == 2
    assert result["system_prompt_mutations"] == 0
    assert result["temporary_extra_user_content_runs"] == 2


def test_handler_benchmark_does_not_configure_the_removed_legacy_key() -> None:
    benchmark_source = BENCHMARK_PATH.read_text(encoding="utf-8")
    support_path = REPO_ROOT / "scripts" / "recall_total_path_benchmark.py"
    support_source = (
        support_path.read_text(encoding="utf-8") if support_path.exists() else ""
    )

    assert '"recall_engine.injection_method"' not in (
        benchmark_source + support_source
    )


def test_recorded_baseline_rejects_a_mismatched_source_commit(
    tmp_path: Path,
) -> None:
    benchmark = _load_benchmark_module()

    with pytest.raises(ValueError, match="source commit"):
        benchmark.record_total_path_baseline(
            source_root=REPO_ROOT,
            source_commit="0" * 40,
            output_path=tmp_path / "baseline.json",
        )


def test_balanced_injection_hit_is_not_below_fixed_budget_baseline() -> None:
    benchmark = _load_benchmark_module()

    metrics, _diagnostics = asyncio.run(
        benchmark._profile_metrics(benchmark.PresetName.BALANCED)
    )

    assert 0.0 < metrics["FixedBudgetInjectionHit"] <= 1.0
    assert metrics["InjectionHit@Budget"] >= metrics["FixedBudgetInjectionHit"]
