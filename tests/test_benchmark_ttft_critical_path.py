"""LLM 前关键路径基准的输出与质量门禁测试。"""

from __future__ import annotations

import pytest

from scripts.benchmark_ttft_critical_path import (
    BenchmarkDelays,
    evaluate_route_quality_guard,
    percentile,
    run_benchmark,
)


def test_percentile_uses_nearest_rank() -> None:
    """分位数使用稳定的 nearest-rank 定义。"""

    values = [1.0, 2.0, 3.0, 4.0]

    assert percentile(values, 0.50) == 2.0
    assert percentile(values, 0.95) == 4.0
    assert percentile(values, 0.99) == 4.0


def test_route_quality_guard_preserves_graph_fixture_and_metrics() -> None:
    """保守门控必须保留全部图关系场景，且四数据集指标不越界。"""

    quality = evaluate_route_quality_guard()

    assert quality["passed"] is True
    assert quality["datasets"]["graph_relation"]["graph_route_rate"] == 1.0
    assert quality["datasets"]["private_basic"]["graph_route_rate"] < 1.0
    assert quality["overall_deltas"] == {
        "recall_at_k": 0.0,
        "mrr": 0.0,
        "ndcg_at_k": 0.0,
    }


@pytest.mark.asyncio
async def test_benchmark_reports_all_scenarios_and_coalesced_call_counts() -> None:
    """基准覆盖八种组合，群聊未命中时每轮只产生一次底层 Embedding。"""

    report = await run_benchmark(
        samples=1,
        delays=BenchmarkDelays(
            cold_ready_seconds=0.02,
            cache_hit_seconds=0.0,
            document_local_seconds=0.0,
            graph_local_seconds=0.0,
            embedding_seconds=0.03,
            soft_budget_seconds=0.04,
        ),
    )

    assert len(report["scenarios"]) == 8
    group_misses = [
        scenario
        for scenario in report["scenarios"]
        if scenario["chat_type"] == "group" and scenario["cache"] == "miss"
    ]
    assert group_misses
    assert all(scenario["embedding_calls"] == 1 for scenario in group_misses)
    assert report["quality_guard"]["passed"] is True
