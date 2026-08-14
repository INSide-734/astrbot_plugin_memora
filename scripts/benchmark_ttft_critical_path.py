#!/usr/bin/env python3
"""LLM 请求前召回关键路径的可控延迟与质量门禁基准。"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.features.evaluation.application import (  # noqa: E402
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)
from core.features.retrieval.embedding_singleflight import (  # noqa: E402
    InFlightEmbeddingProviderProxy,
)
from core.features.retrieval.query_planner import QueryPlanner  # noqa: E402
from core.features.retrieval.query_rewriter import QueryIntent  # noqa: E402
from core.features.retrieval.route_policy import should_use_graph_route  # noqa: E402
from core.features.retrieval.vector_deadline import (  # noqa: E402
    run_local_and_bounded_vector,
)

_DATASETS = (
    "private_basic",
    "group_topic_shift",
    "graph_relation",
    "session_first",
)
_QUALITY_METRICS = ("recall_at_k", "mrr", "ndcg_at_k")


@dataclass(frozen=True, slots=True)
class BenchmarkDelays:
    """基准使用的可控阶段延迟与绝对软预算。"""

    cold_ready_seconds: float = 0.070
    cache_hit_seconds: float = 0.001
    document_local_seconds: float = 0.005
    graph_local_seconds: float = 0.006
    embedding_seconds: float = 0.040
    soft_budget_seconds: float = 0.100


@dataclass(frozen=True, slots=True)
class _Scenario:
    """单个温度、缓存状态与聊天类型组合。"""

    state: str
    cache: str
    chat_type: str


class _CountingEmbeddingProvider:
    """记录真实底层调用次数的可控延迟 Embedding Provider。"""

    def __init__(self, delay_seconds: float) -> None:
        """保存延迟并初始化调用计数。"""

        self.delay_seconds = max(0.0, float(delay_seconds))
        self.calls = 0

    async def get_embedding(self, content: str) -> list[float]:
        """等待固定延迟并返回匿名长度向量。"""

        self.calls += 1
        await asyncio.sleep(self.delay_seconds)
        return [float(len(content)), 1.0]


def percentile(values: list[float], quantile: float) -> float:
    """按 nearest-rank 规则返回非空样本的分位数。"""

    if not values:
        raise ValueError("分位数样本不能为空")
    bounded = min(1.0, max(0.0, float(quantile)))
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(len(ordered) * bounded))
    return ordered[rank - 1]


def _load_cases(fixture_root: Path, dataset: str) -> list[dict[str, Any]]:
    """读取指定匿名 JSONL 评测集。"""

    path = fixture_root / f"{dataset}.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _mean(values: list[float]) -> float:
    """返回数值列表均值；空列表返回零。"""

    return sum(values) / len(values) if values else 0.0


def _score_rankings(
    rankings: list[tuple[list[Any], list[Any]]],
    *,
    k: int,
) -> dict[str, float]:
    """计算一组排名的 Recall@K、MRR 与 nDCG@K 均值。"""

    return {
        "recall_at_k": round(
            _mean(
                [recall_at_k(ranked, relevant, k=k) for ranked, relevant in rankings]
            ),
            4,
        ),
        "mrr": round(
            _mean([reciprocal_rank(ranked, relevant) for ranked, relevant in rankings]),
            4,
        ),
        "ndcg_at_k": round(
            _mean([ndcg_at_k(ranked, relevant, k=k) for ranked, relevant in rankings]),
            4,
        ),
    }


def evaluate_route_quality_guard(
    fixture_root: Path | None = None,
    *,
    k: int = 5,
) -> dict[str, Any]:
    """验证图路门控不会丢弃声明依赖图路的匿名评测场景。"""

    root = fixture_root or PROJECT_ROOT / "tests" / "fixtures" / "retrieval"
    dataset_reports: dict[str, dict[str, Any]] = {}
    overall_baseline: list[tuple[list[Any], list[Any]]] = []
    overall_optimized: list[tuple[list[Any], list[Any]]] = []

    for dataset in _DATASETS:
        baseline_rankings: list[tuple[list[Any], list[Any]]] = []
        optimized_rankings: list[tuple[list[Any], list[Any]]] = []
        graph_routes = 0
        cases = _load_cases(root, dataset)
        for case in cases:
            relevant = list(case.get("relevant_doc_ids") or [])
            intent = QueryIntent.from_keywords(str(case.get("query") or ""))
            plan = QueryPlanner.build(query=str(case.get("query") or ""), intent=intent)
            use_graph = should_use_graph_route(plan, intent)
            graph_routes += int(use_graph)
            baseline_ranked = list(relevant)
            requires_graph = bool((case.get("metadata") or {}).get("requires_graph"))
            optimized_ranked = (
                [] if requires_graph and not use_graph else list(relevant)
            )
            baseline_rankings.append((baseline_ranked, relevant))
            optimized_rankings.append((optimized_ranked, relevant))

        baseline = _score_rankings(baseline_rankings, k=k)
        optimized = _score_rankings(optimized_rankings, k=k)
        deltas = {
            metric: round(optimized[metric] - baseline[metric], 4)
            for metric in _QUALITY_METRICS
        }
        dataset_reports[dataset] = {
            "case_count": len(cases),
            "graph_route_rate": round(graph_routes / len(cases), 4) if cases else 0.0,
            "baseline": baseline,
            "optimized": optimized,
            "deltas": deltas,
        }
        overall_baseline.extend(baseline_rankings)
        overall_optimized.extend(optimized_rankings)

    baseline_overall = _score_rankings(overall_baseline, k=k)
    optimized_overall = _score_rankings(overall_optimized, k=k)
    overall_deltas = {
        metric: round(optimized_overall[metric] - baseline_overall[metric], 4)
        for metric in _QUALITY_METRICS
    }
    overall_ok = all(delta >= -0.01 for delta in overall_deltas.values())
    datasets_ok = all(
        delta >= -0.02
        for report in dataset_reports.values()
        for delta in report["deltas"].values()
    )
    return {
        "passed": overall_ok and datasets_ok,
        "thresholds": {"overall_max_drop": 0.01, "dataset_max_drop": 0.02},
        "baseline_overall": baseline_overall,
        "optimized_overall": optimized_overall,
        "overall_deltas": overall_deltas,
        "datasets": dataset_reports,
    }


async def _run_scenario(
    *,
    scenario: _Scenario,
    samples: int,
    delays: BenchmarkDelays,
) -> dict[str, Any]:
    """运行单个冷/热、缓存和聊天类型组合。"""

    query = (
        "群里谁负责发布说明" if scenario.chat_type == "group" else "用户偏好燕麦拿铁"
    )
    intent = QueryIntent.from_keywords(query)
    plan = QueryPlanner.build(query=query, intent=intent)
    use_graph = should_use_graph_route(plan, intent)
    provider = _CountingEmbeddingProvider(delays.embedding_seconds)
    proxy = InFlightEmbeddingProviderProxy(provider)
    durations_ms: list[float] = []
    partial_fallbacks = 0

    for _index in range(samples):
        started = time.perf_counter()
        deadline = started + max(0.0, delays.soft_budget_seconds)
        if scenario.state == "cold":
            await asyncio.sleep(max(0.0, delays.cold_ready_seconds))

        if scenario.cache == "hit":
            await asyncio.sleep(max(0.0, delays.cache_hit_seconds))
            partial = False
        else:

            async def run_route(local_delay: float) -> bool:
                """运行本地结果优先、向量受限的一条模拟路由。"""

                async def local_search() -> list[str]:
                    """等待固定本地检索延迟并返回匿名结果。"""

                    await asyncio.sleep(max(0.0, local_delay))
                    return ["local-result"]

                _local, _vector, timed_out = await run_local_and_bounded_vector(
                    local_search,
                    lambda: proxy.get_embedding(query),
                    deadline_monotonic=deadline,
                )
                return timed_out

            route_tasks = [run_route(delays.document_local_seconds)]
            if use_graph:
                route_tasks.append(run_route(delays.graph_local_seconds))
            partial = any(await asyncio.gather(*route_tasks))

        partial_fallbacks += int(partial)
        durations_ms.append((time.perf_counter() - started) * 1000.0)

    return {
        "state": scenario.state,
        "cache": scenario.cache,
        "chat_type": scenario.chat_type,
        "samples": samples,
        "p50_ms": round(percentile(durations_ms, 0.50), 3),
        "p95_ms": round(percentile(durations_ms, 0.95), 3),
        "p99_ms": round(percentile(durations_ms, 0.99), 3),
        "partial_fallback_rate": round(partial_fallbacks / samples, 4),
        "embedding_calls": provider.calls,
        "graph_route_used": use_graph,
    }


async def run_benchmark(
    *,
    samples: int = 10,
    delays: BenchmarkDelays | None = None,
) -> dict[str, Any]:
    """运行八种关键路径组合，并附带检索质量停止门禁。"""

    sample_count = max(1, int(samples))
    active_delays = delays or BenchmarkDelays()
    scenarios: list[dict[str, Any]] = []
    for state in ("cold", "warm"):
        for cache in ("hit", "miss"):
            for chat_type in ("private", "group"):
                scenarios.append(
                    await _run_scenario(
                        scenario=_Scenario(
                            state=state,
                            cache=cache,
                            chat_type=chat_type,
                        ),
                        samples=sample_count,
                        delays=active_delays,
                    )
                )
    return {
        "scope": "pre_llm_recall_hook_simulation",
        "note": "只测量 LLM 请求前插件关键路径，不宣称 Provider 首字节时间",
        "scenarios": scenarios,
        "quality_guard": evaluate_route_quality_guard(),
    }


def _build_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=10, help="每个组合的样本数")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    return parser


def _print_human(report: dict[str, Any]) -> None:
    """输出适合本地门禁阅读的紧凑摘要。"""

    print(report["note"])
    for scenario in report["scenarios"]:
        print(
            "{state}/{cache}/{chat_type}: p50={p50_ms:.3f}ms "
            "p95={p95_ms:.3f}ms p99={p99_ms:.3f}ms "
            "partial={partial_fallback_rate:.0%} embeddings={embedding_calls}".format(
                **scenario
            )
        )
    quality = report["quality_guard"]
    print(
        "quality_guard={status} overall_deltas={deltas}".format(
            status="PASS" if quality["passed"] else "FAIL",
            deltas=quality["overall_deltas"],
        )
    )


def main() -> int:
    """运行基准，并在质量门禁失败时返回非零状态码。"""

    args = _build_parser().parse_args()
    report = asyncio.run(run_benchmark(samples=args.samples))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 0 if report["quality_guard"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
