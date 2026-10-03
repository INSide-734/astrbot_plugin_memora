"""验证评测报告存储与评测服务行为。"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.features.evaluation.application import (
    EvaluationReport,
    EvaluationResult,
)
from core.features.evaluation.infrastructure.evaluation_service import EvaluationService
from core.features.evaluation.infrastructure.report_store import (
    EvaluationReportStore,
)

_QUALITY_LOOP_SECRET = b"evaluation-quality-loop-test-key-32"


def test_evaluation_package_imports_without_astrbot_mocks():
    """验证评测包在没有 AstrBot Mock 时仍可独立导入。"""

    code = """
import importlib
import importlib.abc
import sys

for name in list(sys.modules):
    if name == "astrbot" or name.startswith("astrbot."):
        del sys.modules[name]

class BlockAstrBot(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "astrbot" or fullname.startswith("astrbot."):
            raise ImportError(f"blocked unexpected AstrBot import: {fullname}")
        return None

sys.meta_path.insert(0, BlockAstrBot())

importlib.import_module("core.features.evaluation")
importlib.import_module("core.features.evaluation.application.retrieval_quality")
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.getcwd()
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_report_store_saves_and_loads_report(tmp_path):
    """验证报告存储仅保留脱敏允许字段。"""

    store = EvaluationReportStore(tmp_path / "evaluation_reports.db")
    await store.initialize()

    report_id = await store.save_report(
        {
            "created_at": 1783150200.0,
            "baseline": "baseline",
            "summary": {
                "total_cases": 2,
                "k": 5,
                "recall_at_k": 0.5,
                "p95_latency_ms": 18.0,
                "provider_calls": 2,
            },
            "datasets": ["private_basic"],
            "variants": [],
            "cases": [
                {
                    "case_id": "coffee",
                    "query": "用户喜欢喝什么咖啡",
                    "ranked_doc_ids": ["mem-coffee"],
                    "recall_at_k": 1.0,
                    "reciprocal_rank": 1.0,
                    "ndcg_at_k": 1.0,
                    "latency_ms": 12.5,
                    "advanced_metrics": {
                        "answer_faithfulness": 0.75,
                        "provider_calls": 2,
                        "reported_provider_calls": 1,
                    },
                }
            ],
        }
    )

    loaded = await store.get_report(report_id)
    assert loaded is not None
    assert loaded["report_id"] == report_id
    assert loaded["summary"]["total_cases"] == 2
    assert loaded["summary"]["reported_p95_latency_ms"] == 18.0
    assert loaded["summary"]["reported_provider_calls"] == 2
    assert "p95_latency_ms" not in loaded["summary"]
    assert "query" not in loaded["cases"][0]
    assert "ranked_doc_ids" not in loaded["cases"][0]
    assert loaded["cases"][0]["reported_latency_ms"] == 12.5
    assert loaded["cases"][0]["advanced_metrics"] == {
        "reported_answer_faithfulness": 0.75,
        "reported_provider_calls": 1,
    }

    reports = await store.list_reports(limit=10)
    assert reports[0]["report_id"] == report_id
    assert reports[0]["summary"]["reported_p95_latency_ms"] == 18.0


@pytest.mark.asyncio
async def test_report_store_sanitizes_quality_loop_on_write_and_read(tmp_path):
    """验证质量闭环写读均只保留低敏 manifest、阶段和配对摘要。"""
    digest = "a" * 64
    store = EvaluationReportStore(tmp_path / "evaluation_reports.db")
    await store.initialize()
    report_id = await store.save_report(
        {
            "created_at": 1783150200.0,
            "baseline": "baseline",
            "summary": {},
            "datasets": [],
            "variants": [],
            "quality_loop": {
                "manifest": {
                    "schema_version": "quality-loop-replay-v1",
                    "evaluator_version": "quality-loop-evaluator-v1",
                    "code_revision": "safe-code",
                    "config_hash": digest,
                    "schema_hash": digest,
                    "fixture_hash": digest,
                    "manifest_hash": digest,
                    "model_id": None,
                    "embedding_id": None,
                    "tokenizer_id": None,
                    "seed": 42,
                    "k": 5,
                    "db_snapshot_hash": None,
                    "pair_count": 1,
                    "query": "QUERY-CANARY",
                },
                "stages": [
                    {
                        "stage": "write",
                        "state": "degraded",
                        "reason": "no_annotation",
                        "metrics": {
                            "write_fact_correctness": None,
                            "query": "QUERY-CANARY",
                        },
                    }
                ],
                "pairs": {
                    "total_pairs": 1,
                    "should_use_hit_rate": 0.0,
                    "should_silence_correct_rate": None,
                    "query": "QUERY-CANARY",
                },
            },
        }
    )

    with sqlite3.connect(store.db_path) as db:
        row = db.execute(
            "SELECT payload_json FROM evaluation_reports WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        payload["quality_loop"]["manifest"]["query"] = "QUERY-CANARY"
        payload["quality_loop"]["stages"][0]["metrics"]["query"] = "QUERY-CANARY"
        payload["quality_loop"]["pairs"]["query"] = "QUERY-CANARY"
        payload["quality_loop"]["pairs"]["reject_reason_counts"] = {
            "secret user text": 3,
            "pair_incomplete": 1,
            "context_key_missing": 2,
        }
        payload["quality_loop"]["pair_outcomes"] = [
            {
                "context_key_hash": "raw-context-key",
                "slot_order": ["should_use", "LEAKY-QUERY-TEXT"],
                "should_use_case_hash": "raw-case-hash",
                "should_silence_case_hash": "raw-case-hash",
                "should_use_hit": True,
                "should_silence_correct": None,
            }
        ]
        db.execute(
            "UPDATE evaluation_reports SET payload_json = ? WHERE report_id = ?",
            (json.dumps(payload), report_id),
        )
        db.commit()

    loaded = await store.get_report(report_id)
    assert loaded is not None
    serialized = json.dumps(loaded, ensure_ascii=False)
    assert "QUERY-CANARY" not in serialized
    assert "secret user text" not in serialized
    assert "raw-context-key" not in serialized
    assert "LEAKY-QUERY-TEXT" not in serialized
    assert loaded["quality_loop"]["manifest"]["pair_count"] == 1
    assert loaded["quality_loop"]["stages"][0]["metrics"] == {
        "write_fact_correctness": None
    }
    assert loaded["quality_loop"]["pairs"]["reject_reason_counts"] == {
        "context_key_missing": 2,
        "pair_incomplete": 1,
    }


@pytest.mark.asyncio
async def test_report_store_saves_native_evaluation_report_with_relevant_sets(tmp_path):
    """验证原生评测报告及相关集合可安全持久化。"""

    store = EvaluationReportStore(tmp_path / "evaluation_reports.db")
    await store.initialize()

    report = EvaluationReport(
        total_cases=1,
        k=5,
        recall_at_k=1.0,
        mrr=1.0,
        ndcg_at_k=1.0,
        observed_p95_latency_ms=8.5,
        cases=[
            EvaluationResult(
                case_id="coffee",
                query="用户喜欢喝什么咖啡",
                ranked_doc_ids=["mem-coffee"],
                relevant_doc_ids={"mem-zebra", "mem-coffee"},
                recall_at_k=1.0,
                reciprocal_rank=1.0,
                ndcg_at_k=1.0,
                observed_latency_ms=8.5,
                metadata={
                    "dataset": "private_basic",
                    "session_id": "session-secret-canary",
                    "user_id": "user-secret-canary",
                },
            )
        ],
        dataset_breakdown={
            "private_basic": {
                "case_count": 1,
                "recall_at_k": 1.0,
                "mrr": 1.0,
                "ndcg_at_k": 1.0,
                "observed_p95_latency_ms": 8.5,
            }
        },
    )

    report_id = await store.save_report(report)

    loaded = await store.get_report(report_id)
    assert loaded is not None
    assert loaded["summary"]["total_cases"] == 1
    assert loaded["summary"]["recall_at_k"] == 1.0
    assert loaded["cases"][0]["case_id"] == "coffee"
    assert "query" not in loaded["cases"][0]
    assert "relevant_doc_ids" not in loaded["cases"][0]
    serialized = json.dumps(loaded, ensure_ascii=False)
    assert "session-secret-canary" not in serialized
    assert "user-secret-canary" not in serialized


class FakeEngine:
    """为评测服务提供确定性检索结果。"""

    async def search_memories(self, **kwargs):
        """按查询内容返回确定性检索结果。"""

        query = kwargs["query"]
        if "咖啡" in query:
            return [{"doc_id": "mem-coffee", "score": 1.0}]
        return []


def _write_single_case_fixture(root: Path, relevant_doc_ids: list[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "case_id": "ablation-case",
        "query": "ablation query",
        "relevant_doc_ids": relevant_doc_ids,
        "metadata": {"dataset": "ablation"},
    }
    (root / "ablation.jsonl").write_text(
        json.dumps(payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_paired_fixture(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "case_id": "use-case",
            "query": "use",
            "relevant_doc_ids": ["mem-1"],
            "metadata": {"context_key": "ctx-1", "expectation": "should_use"},
        },
        {
            "case_id": "silence-case",
            "query": "silence",
            "relevant_doc_ids": ["__no_relevant__"],
            "metadata": {"context_key": "ctx-1", "expectation": "should_silence"},
        },
    ]
    (root / "paired.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_quality_loop_uses_baseline_snapshot_and_safe_provider_id(tmp_path):
    """验证质量闭环绑定隔离基准并隐藏自由格式 provider ID。"""

    class SnapshotAwareEngine:
        def __init__(self) -> None:
            self.config = {
                "provider_settings": {
                    "llm_provider_id": "openai/gpt-4o-mini",
                    "embedding_provider_id": "embedding-v1",
                    "tokenizer_id": "tokenizer-v1",
                }
            }
            self.seen_engines: list[object] = []

        async def search_memories(self, **kwargs):
            self.seen_engines.append(self)
            return [] if kwargs["query"] == "silence" else [{"doc_id": "mem-1"}]

    fixture_dir = tmp_path / "fixtures"
    _write_paired_fixture(fixture_dir)
    engine = SnapshotAwareEngine()
    service = EvaluationService(
        engine=engine,
        fixture_dir=fixture_dir,
        quality_loop_secret=_QUALITY_LOOP_SECRET,
    )

    result = await service.run_evaluation(
        datasets=["paired"],
        k=1,
        variants=["baseline"],
        baseline="baseline",
        save_report=False,
        quality_loop={},
        quality_loop_seed=1,
    )

    quality_loop = result["quality_loop"]
    assert quality_loop["pairs"]["total_pairs"] == 1
    assert quality_loop["manifest"] is not None, [
        stage["reason"] for stage in quality_loop["stages"]
    ]
    assert quality_loop["manifest"]["model_id"] != "openai/gpt-4o-mini"
    assert quality_loop["manifest"]["db_snapshot_hash"] is None
    source = next(
        stage for stage in quality_loop["stages"] if stage["stage"] == "source"
    )
    assert source["state"] == "degraded"
    assert source["reason"] == "db_snapshot_unavailable"
    assert engine.seen_engines
    assert all(seen is not engine for seen in engine.seen_engines)


@pytest.mark.asyncio
async def test_evaluation_service_runs_selected_dataset(tmp_path):
    """验证服务仅运行指定数据集并持久化脱敏报告。"""

    service = EvaluationService(
        engine=FakeEngine(),
        fixture_dir="tests/fixtures/retrieval",
        db_path=tmp_path / "reports.db",
    )
    await service.initialize()

    result = await service.run_evaluation(
        datasets=["private_basic"],
        k=5,
        variants=["baseline"],
        baseline="baseline",
        save_report=True,
    )

    assert result["report_id"]
    assert result["summary"]["total_cases"] >= 10
    assert result["baseline"] == "baseline"
    assert result["cases"]
    assert "query" not in result["cases"][0]
    assert "ranked_doc_ids" not in result["cases"][0]
    assert "relevant_doc_ids" not in result["cases"][0]
    assert "metadata" not in result["cases"][0]


@pytest.mark.asyncio
async def test_evaluation_service_unknown_dataset_selection_runs_no_cases(tmp_path):
    """验证未知数据集不会执行评测用例。"""

    service = EvaluationService(
        engine=FakeEngine(),
        fixture_dir="tests/fixtures/retrieval",
        db_path=tmp_path / "reports.db",
    )
    await service.initialize()

    result = await service.run_evaluation(
        datasets=["missing_fixture"],
        k=5,
        variants=["baseline"],
        baseline="baseline",
        save_report=False,
    )

    assert result["summary"]["total_cases"] == 0
    assert result["datasets"] == []


def test_evaluation_service_lists_fixture_metadata() -> None:
    """验证服务列出评测夹具元数据。"""

    service = EvaluationService(
        engine=FakeEngine(),
        fixture_dir="tests/fixtures/retrieval",
    )

    result = service.list_datasets()

    private = next(
        item for item in result["datasets"] if item["name"] == "private_basic"
    )
    assert private["case_count"] >= 10
    assert private["path"].endswith("private_basic.jsonl")
    assert "preference" in private["intents"]
    assert "private" in private["chat_types"]


@pytest.mark.asyncio
async def test_evaluation_service_marks_unavailable_ablation_variants_skipped(tmp_path):
    """验证不可用消融变体被明确标记为跳过。"""

    service = EvaluationService(
        engine=FakeEngine(),
        fixture_dir="tests/fixtures/retrieval",
        db_path=tmp_path / "reports.db",
    )
    await service.initialize()

    result = await service.run_evaluation(
        datasets=["private_basic"],
        k=99,
        variants=["baseline", "graph_expansion_off", "topic_expansion_off"],
        baseline="baseline",
        save_report=False,
    )

    assert result["summary"]["k"] == 20
    assert result["saved"] is False
    assert result["variants"]["baseline"]["status"] == "completed"
    assert result["variants"]["graph_expansion_off"] == {
        "name": "graph_expansion_off",
        "status": "skipped",
        "capability_status": "unavailable",
        "reason_code": "missing_engine_config",
        "effective_settings": {},
    }
    assert result["variants"]["topic_expansion_off"] == {
        "name": "topic_expansion_off",
        "status": "skipped",
        "capability_status": "unavailable",
        "reason_code": "missing_engine_config",
        "effective_settings": {},
    }


@pytest.mark.asyncio
async def test_evaluation_service_ablation_variants_use_real_config_keys(tmp_path):
    """验证消融变体使用真实配置键且恢复原配置。"""

    fixture_dir = tmp_path / "fixtures"
    _write_single_case_fixture(
        fixture_dir,
        ["mem-baseline", "mem-graph-off", "mem-topic-off"],
    )

    class ConfigDictEngine:
        def __init__(self) -> None:
            self.config = {"recall_engine.chain_graph_expansion_enabled": True}
            self.observed_configs: list[dict[str, object]] = []

        async def search_memories(self, **_kwargs):
            self.observed_configs.append(dict(self.config))
            if self.config.get("recall_engine.chain_graph_expansion_enabled") is False:
                return [{"doc_id": "mem-graph-off", "score": 1.0}]
            if self.config.get("recall_engine.chain_topic_expansion_enabled") is False:
                return [{"doc_id": "mem-topic-off", "score": 1.0}]
            return [{"doc_id": "mem-baseline", "score": 1.0}]

    engine = ConfigDictEngine()
    service = EvaluationService(engine=engine, fixture_dir=fixture_dir)

    result = await service.run_evaluation(
        datasets=["ablation"],
        k=1,
        variants=["baseline", "graph_expansion_off", "topic_expansion_off"],
        baseline="baseline",
        save_report=False,
    )

    assert result["variants"]["graph_expansion_off"]["status"] == "completed"
    assert result["variants"]["topic_expansion_off"]["status"] == "completed"
    assert result["variants"]["graph_expansion_off"]["summary"]["recall_at_k"] == 1.0
    assert result["variants"]["topic_expansion_off"]["summary"]["recall_at_k"] == 1.0
    assert {
        snapshot.get("recall_engine.chain_graph_expansion_enabled")
        for snapshot in engine.observed_configs
    } >= {False, True}
    assert {
        snapshot.get("recall_engine.chain_topic_expansion_enabled")
        for snapshot in engine.observed_configs
    } >= {False}
    assert engine.config == {"recall_engine.chain_graph_expansion_enabled": True}
    assert "recall_engine.chain_topic_expansion_enabled" not in engine.config


@pytest.mark.asyncio
async def test_evaluation_service_runs_memory_evolution_variants_a_b_c(tmp_path):
    """验证记忆演化 A/B/C 变体使用各自有效配置。"""

    from core.features.evaluation.application import load_fixture_dir

    cases = load_fixture_dir("tests/fixtures/retrieval")["memory_evolution"]
    relevant_by_query = {
        case.query: next(iter(case.relevant_doc_ids))
        for case in cases
        if not case.metadata.get("expected_no_hit")
    }

    class EvolutionEngine:
        def __init__(self) -> None:
            self.config = {"memory_evolution": {"enabled": True, "mode": "disabled"}}
            self.observed_modes: list[str] = []
            self.dual_route_retriever = SimpleNamespace(
                derived_expander=object(),
                projection_reader=None,
            )

        async def search_memories(self, **kwargs):
            self.observed_modes.append(self.config["memory_evolution"]["mode"])
            doc_id = relevant_by_query.get(kwargs["query"])
            return [] if doc_id is None else [{"doc_id": doc_id, "score": 1.0}]

    engine = EvolutionEngine()
    service = EvaluationService(
        engine=engine,
        fixture_dir="tests/fixtures/retrieval",
        db_path=tmp_path / "reports.db",
    )
    await service.initialize()

    result = await service.run_evaluation(
        datasets=["memory_evolution"],
        k=3,
        variants=["A", "B", "C"],
        baseline="baseline",
        save_report=False,
    )

    assert result["baseline"] == "baseline"
    assert set(result["variants"]) == {"baseline", "A", "B", "C"}
    assert result["variants"]["baseline"]["status"] == "completed"
    assert result["variants"]["A"]["reason_code"] == "equivalent_to_baseline"
    assert result["variants"]["B"]["status"] == "completed"
    assert result["variants"]["C"]["reason_code"] == (
        "readonly_snapshot_cannot_activate_worker"
    )
    assert engine.config["memory_evolution"]["mode"] == "disabled"
    assert "readonly" in engine.observed_modes
    assert result["summary"]["variant"] == "baseline"
    assert (
        result["summary"]["configuration_hash"]
        == result["variants"]["baseline"]["summary"]["configuration_hash"]
    )


@pytest.mark.asyncio
async def test_evaluation_service_isolates_caches_between_variants(tmp_path):
    """变体应使用独立缓存且不得清空 live engine 缓存。"""

    fixture_dir = tmp_path / "fixtures"
    _write_single_case_fixture(fixture_dir, ["mem-graph-off"])

    class CountingCache(dict):
        def __init__(self) -> None:
            super().__init__()
            self.clear_count = 0

        def clear(self) -> None:
            self.clear_count += 1
            super().clear()

    class CachedEngine:
        def __init__(self) -> None:
            self.config = {"recall_engine.chain_graph_expansion_enabled": True}
            self.cache = CountingCache()

        async def search_memories(self, **_kwargs):
            if "result" in self.cache:
                return self.cache["result"]
            if self.config["recall_engine.chain_graph_expansion_enabled"] is False:
                result = [{"doc_id": "mem-graph-off", "score": 1.0}]
            else:
                result = [{"doc_id": "mem-baseline", "score": 1.0}]
            self.cache["result"] = result
            return result

    engine = CachedEngine()
    service = EvaluationService(engine=engine, fixture_dir=fixture_dir)

    result = await service.run_evaluation(
        datasets=["ablation"],
        k=1,
        variants=["baseline", "graph_expansion_off"],
        baseline="baseline",
        save_report=False,
    )

    assert engine.cache.clear_count == 0
    assert engine.cache == {}
    assert result["summary"]["recall_at_k"] == 0.0
    assert result["variants"]["graph_expansion_off"]["summary"]["recall_at_k"] == 1.0


@pytest.mark.asyncio
async def test_evaluation_service_does_not_invalidate_live_retrieval_cache(
    tmp_path,
):
    """评测前后不得调用生产 RetrievalOptimizer 的失效入口。"""

    fixture_dir = tmp_path / "fixtures"
    _write_single_case_fixture(fixture_dir, ["mem-graph-off"])

    class FakeRetrievalOptimizer:
        def __init__(self) -> None:
            self.cached_result = None
            self.invalidate_count = 0

        def invalidate_cache(self) -> None:
            self.invalidate_count += 1
            self.cached_result = None

    class ProductionLikeCachedEngine:
        def __init__(self) -> None:
            self.config = {"recall_engine.chain_graph_expansion_enabled": True}
            self._retrieval = FakeRetrievalOptimizer()

        async def search_memories(self, **_kwargs):
            if self._retrieval.cached_result is not None:
                return self._retrieval.cached_result
            if self.config["recall_engine.chain_graph_expansion_enabled"] is False:
                result = [{"doc_id": "mem-graph-off", "score": 1.0}]
            else:
                result = [{"doc_id": "mem-baseline", "score": 1.0}]
            self._retrieval.cached_result = result
            return result

    engine = ProductionLikeCachedEngine()
    service = EvaluationService(engine=engine, fixture_dir=fixture_dir)

    result = await service.run_evaluation(
        datasets=["ablation"],
        k=1,
        variants=["baseline", "graph_expansion_off"],
        baseline="baseline",
        save_report=False,
    )

    assert engine._retrieval.invalidate_count == 0
    assert engine._retrieval.cached_result is None
    assert result["summary"]["recall_at_k"] == 0.0
    assert result["variants"]["graph_expansion_off"]["summary"]["recall_at_k"] == 1.0


@pytest.mark.asyncio
async def test_evaluation_service_cache_clear_visits_each_object_once(tmp_path):
    """引擎缓存对象互相引用时，缓存清理只遍历每个对象一次并清一次缓存。"""

    reads = 0

    class CyclicCache:
        def __init__(self) -> None:
            self.clear_count = 0
            self._retrieval: object | None = None

        def clear(self) -> None:
            self.clear_count += 1

    cache = CyclicCache()

    class CyclicEngine:
        @property
        def search_cache(self) -> CyclicCache | None:
            """首次读取返回缓存对象，之后断开引用以限制遍历规模。"""

            nonlocal reads
            reads += 1
            return cache if reads == 1 else None

    engine = CyclicEngine()
    cache._retrieval = engine
    service = EvaluationService(engine=engine, fixture_dir=tmp_path / "fixtures")

    await service._clear_evaluation_caches(engine)

    assert reads == 1
    assert cache.clear_count == 1


@pytest.mark.asyncio
async def test_evaluation_service_propagates_cancellation(tmp_path):
    """检索取消必须穿透评测服务，不能降级为 completed 或 skipped。"""

    fixture_dir = tmp_path / "fixtures"
    _write_single_case_fixture(fixture_dir, ["mem-never-returned"])

    class CancelledEngine:
        async def search_memories(self, **_kwargs):
            """模拟上游任务取消。"""

            raise asyncio.CancelledError

    service = EvaluationService(
        engine=CancelledEngine(),
        fixture_dir=fixture_dir,
    )

    with pytest.raises(asyncio.CancelledError):
        await service.run_evaluation(
            datasets=["ablation"],
            k=1,
            variants=["baseline"],
            baseline="baseline",
            save_report=False,
        )


@pytest.mark.asyncio
async def test_evaluation_service_isolates_single_variant_failure(tmp_path) -> None:
    """单个实验变体普通失败只能跳过该变体，且不得泄露异常文本。"""

    fixture_dir = tmp_path / "fixtures"
    _write_single_case_fixture(fixture_dir, ["mem-graph-off"])

    class PartiallyFailingEngine:
        """根据隔离配置返回结果或模拟单变体故障。"""

        def __init__(self) -> None:
            self.config = {
                "recall_engine.chain_graph_expansion_enabled": True,
                "recall_engine.chain_topic_expansion_enabled": True,
            }

        async def search_memories(self, **_kwargs):
            """topic-off 故障，graph-off 与 baseline 保持可执行。"""

            if self.config["recall_engine.chain_topic_expansion_enabled"] is False:
                raise RuntimeError("provider-secret-canary")
            if self.config["recall_engine.chain_graph_expansion_enabled"] is False:
                return [{"doc_id": "mem-graph-off", "score": 1.0}]
            return [{"doc_id": "mem-baseline", "score": 1.0}]

    service = EvaluationService(
        engine=PartiallyFailingEngine(), fixture_dir=fixture_dir
    )
    result = await service.run_evaluation(
        datasets=["ablation"],
        k=1,
        variants=["baseline", "graph_expansion_off", "topic_expansion_off"],
        baseline="baseline",
        save_report=False,
    )

    assert result["variants"]["baseline"]["status"] == "completed"
    assert result["variants"]["graph_expansion_off"]["status"] == "completed"
    assert result["variants"]["topic_expansion_off"] == {
        "name": "topic_expansion_off",
        "status": "skipped",
        "capability_status": "unavailable",
        "reason_code": "variant_execution_failed",
        "effective_settings": {"chain_topic_expansion_enabled": False},
    }
    assert "provider-secret-canary" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_evaluation_service_case_payload_uses_safe_allowlist(tmp_path):
    """报告与持久化不得包含 query、canonical ID、身份或秘密 metadata。"""

    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir(parents=True)
    payload = {
        "case_id": "safe-case",
        "query": "QUERY-CANARY",
        "relevant_doc_ids": ["MEMORY-ID-CANARY"],
        "metadata": {
            "dataset": "ablation",
            "session_id": "SESSION-CANARY",
            "persona_id": "PERSONA-CANARY",
            "user_id": "USER-CANARY",
            "api_secret": "SECRET-CANARY",
        },
    }
    (fixture_dir / "ablation.jsonl").write_text(
        json.dumps(payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    class MatchingEngine:
        async def search_memories(self, **_kwargs):
            """返回 fixture 对应的 canonical 命中。"""

            return [{"doc_id": "MEMORY-ID-CANARY", "score": 1.0}]

    service = EvaluationService(
        engine=MatchingEngine(),
        fixture_dir=fixture_dir,
        db_path=tmp_path / "reports.db",
    )
    await service.initialize()

    result = await service.run_evaluation(
        datasets=["ablation"],
        k=1,
        variants=["baseline"],
        baseline="baseline",
        save_report=True,
    )
    stored = await service.get_report(result["report_id"])
    serialized = json.dumps({"result": result, "stored": stored}, ensure_ascii=False)

    assert result["cases"][0]["case_id"] == "safe-case"
    assert result["cases"][0]["recall_at_k"] == 1.0
    for canary in (
        "QUERY-CANARY",
        "MEMORY-ID-CANARY",
        "SESSION-CANARY",
        "PERSONA-CANARY",
        "USER-CANARY",
        "SECRET-CANARY",
    ):
        assert canary not in serialized


@pytest.mark.asyncio
async def test_evaluation_service_requested_unavailable_baseline_errors_without_saving(
    tmp_path,
):
    """验证不可用基线返回错误且不保存报告。"""

    service = EvaluationService(
        engine=FakeEngine(),
        fixture_dir="tests/fixtures/retrieval",
        db_path=tmp_path / "reports.db",
    )
    await service.initialize()

    result = await service.run_evaluation(
        datasets=["private_basic"],
        k=5,
        variants=["graph_expansion_off"],
        baseline="graph_expansion_off",
        save_report=True,
    )

    assert result["status"] == "error"
    assert result["message"] == "Baseline variant unavailable"
    assert result["baseline"] == "graph_expansion_off"
    assert result["variants"]["graph_expansion_off"]["status"] == "skipped"
    assert result["saved"] is False
    assert result["report_id"] is None
    assert await service.list_reports(limit=10) == []


@pytest.mark.asyncio
async def test_evaluation_service_lists_gets_and_compares_saved_reports(tmp_path):
    """验证服务可列出、读取并比较已保存报告。"""

    service = EvaluationService(
        engine=FakeEngine(),
        fixture_dir="tests/fixtures/retrieval",
        db_path=tmp_path / "reports.db",
    )
    await service.initialize()
    first = await service.run_evaluation(
        datasets=["private_basic"],
        k=1,
        variants=["baseline"],
        baseline="baseline",
        save_report=True,
    )
    second = await service.run_evaluation(
        datasets=["private_basic"],
        k=5,
        variants=["baseline"],
        baseline="baseline",
        save_report=True,
    )

    reports = await service.list_reports(limit=5)
    detail = await service.get_report(first["report_id"])
    comparison = await service.compare_reports(first["report_id"], second["report_id"])

    assert {item["report_id"] for item in reports} >= {
        first["report_id"],
        second["report_id"],
    }
    assert detail["report_id"] == first["report_id"]
    assert comparison["report_id_a"] == first["report_id"]
    assert comparison["report_id_b"] == second["report_id"]
    assert set(comparison["deltas"]) == {
        "recall_at_k",
        "mrr",
        "ndcg_at_k",
        "observed_p95_latency_ms",
    }


@pytest.mark.asyncio
async def test_quality_loop_ignores_client_manifest_binding_claims(
    tmp_path, monkeypatch
):
    """Client claims and unsafe runner additions cannot alter the public report."""
    import core.features.evaluation.infrastructure.evaluation_service as service_module

    fixture_dir = tmp_path / "fixtures"
    _write_paired_fixture(fixture_dir)
    original_runner = service_module.run_quality_loop

    async def leaking_runner(*args, **kwargs):
        payload = await original_runner(*args, **kwargs)
        payload["query"] = "SERVICE-QUERY-CANARY-83"
        return payload

    monkeypatch.setattr(service_module, "run_quality_loop", leaking_runner)

    class BoundEngine:
        def __init__(self):
            self.config = {
                "provider_settings": {
                    "llm_provider_id": "openai/gpt-4o-mini",
                    "embedding_provider_id": "embedding-v1",
                    "tokenizer_id": "tokenizer-v1",
                },
                "provider_secret": "PROVIDER-SECRET-CANARY-83",
            }
            self.seen = []

        async def search_memories(self, **kwargs):
            self.seen.append(self)
            return [] if kwargs["query"] == "silence" else [{"doc_id": "mem-1"}]

    engine = BoundEngine()
    service = EvaluationService(
        engine=engine,
        fixture_dir=fixture_dir,
        quality_loop_secret=_QUALITY_LOOP_SECRET,
    )
    snapshot_digest = "b" * 64
    snapshot_calls = []

    async def snapshot(_engine):
        snapshot_calls.append(_engine)
        return snapshot_digest

    monkeypatch.setattr(service, "_database_snapshot_hash", snapshot)
    client_claims = {
        "schema_version": "client-schema",
        "evaluator_version": "client-evaluator",
        "code_revision": "client-revision",
        "config_hash": "c" * 64,
        "schema_hash": "d" * 64,
        "fixture_hash": "e" * 64,
        "model_id": "client-model-canary",
        "embedding_id": "client-embedding",
        "tokenizer_id": "client-tokenizer",
        "seed": 999,
        "k": 19,
        "db_snapshot_hash": "f" * 64,
        "pair_fingerprints": [("f" * 64,) * 3],
        "manifest_hash": "f" * 64,
    }
    result = await service.run_evaluation(
        datasets=["paired"],
        k=3,
        variants=["baseline"],
        baseline="baseline",
        save_report=False,
        quality_loop=client_claims,
        quality_loop_seed=17,
    )

    manifest = result["quality_loop"]["manifest"]
    assert manifest["schema_version"] == "quality-loop-replay-v1"
    assert manifest["evaluator_version"] == "quality-loop-evaluator-v1"
    assert manifest["code_revision"] != client_claims["code_revision"]
    assert manifest["config_hash"] == service._configuration_hash(engine.seen[0])
    assert manifest["config_hash"] != client_claims["config_hash"]
    assert manifest["schema_hash"] == service._schema_hash()
    assert manifest["schema_hash"] != client_claims["schema_hash"]
    assert manifest["fixture_hash"] != client_claims["fixture_hash"]
    assert manifest["model_id"] != client_claims["model_id"]
    assert manifest["embedding_id"] == "embedding-v1"
    assert manifest["tokenizer_id"] == "tokenizer-v1"
    assert manifest["seed"] == 17
    assert manifest["k"] == 3
    assert manifest["db_snapshot_hash"] == snapshot_digest
    assert manifest["db_snapshot_hash"] != client_claims["db_snapshot_hash"]
    assert manifest["manifest_hash"] != client_claims["manifest_hash"]
    assert len(snapshot_calls) == 1
    assert snapshot_calls[0] is not engine
    assert "client-model-canary" not in json.dumps(result)
    serialized_result = json.dumps(result)
    assert "SERVICE-QUERY-CANARY-83" not in serialized_result
    assert "PROVIDER-SECRET-CANARY-83" not in serialized_result
    assert "client-pair" not in serialized_result


@pytest.mark.asyncio
async def test_quality_loop_does_not_snapshot_when_there_are_no_valid_pairs(
    tmp_path, monkeypatch
):
    fixture_dir = tmp_path / "fixtures"
    _write_single_case_fixture(fixture_dir, ["mem-1"])
    service = EvaluationService(
        engine=FakeEngine(),
        fixture_dir=fixture_dir,
        quality_loop_secret=_QUALITY_LOOP_SECRET,
    )

    async def unexpected_snapshot(_engine):
        pytest.fail("snapshot must not run without valid quality-loop pairs")

    monkeypatch.setattr(service, "_database_snapshot_hash", unexpected_snapshot)
    result = await service.run_evaluation(
        datasets=["ablation"],
        k=5,
        variants=["baseline"],
        baseline="baseline",
        save_report=False,
        quality_loop={},
    )
    assert result["quality_loop"]["manifest"] is None
    assert result["quality_loop"]["pairs"]["total_pairs"] == 0
    retrieval_only = await service.run_evaluation(
        datasets=["ablation"],
        k=5,
        variants=["baseline"],
        baseline="baseline",
        save_report=False,
    )
    assert "quality_loop" not in retrieval_only


@pytest.mark.asyncio
async def test_quality_loop_missing_database_snapshot_degrades_without_creating_db(
    tmp_path,
):
    fixture_dir = tmp_path / "fixtures"
    _write_paired_fixture(fixture_dir)
    missing_db = tmp_path / "missing" / "memora.db"
    engine = FakeEngine()
    setattr(engine, "db_path", str(missing_db))
    service = EvaluationService(
        engine=engine,
        fixture_dir=fixture_dir,
        quality_loop_secret=_QUALITY_LOOP_SECRET,
    )

    result = await service.run_evaluation(
        datasets=["paired"],
        k=1,
        variants=["baseline"],
        baseline="baseline",
        save_report=False,
        quality_loop={},
    )

    source = next(
        stage
        for stage in result["quality_loop"]["stages"]
        if stage["stage"] == "source"
    )
    assert source["reason"] == "db_snapshot_unavailable"
    assert result["quality_loop"]["manifest"]["db_snapshot_hash"] is None
    assert not missing_db.exists()


@pytest.mark.asyncio
async def test_missing_hmac_key_degrades_quality_loop_without_skipping_retrieval(
    tmp_path,
):
    fixture_dir = tmp_path / "fixtures"
    _write_paired_fixture(fixture_dir)
    service = EvaluationService(engine=FakeEngine(), fixture_dir=fixture_dir)

    result = await service.run_evaluation(
        datasets=["paired"],
        k=1,
        variants=["baseline"],
        baseline="baseline",
        save_report=False,
        quality_loop={},
    )

    assert result["variants"]["baseline"]["status"] == "completed"
    source = next(
        stage
        for stage in result["quality_loop"]["stages"]
        if stage["stage"] == "source"
    )
    assert source["state"] == "unavailable"
    assert source["reason"] == "hmac_secret_unavailable"
    assert result["quality_loop"]["manifest"] is None


@pytest.mark.asyncio
async def test_quality_loop_never_falls_back_to_live_engine_without_isolated_baseline(
    tmp_path, monkeypatch
):
    fixture_dir = tmp_path / "fixtures"
    _write_paired_fixture(fixture_dir)

    class UncopyableEngine:
        config = {}

        def __init__(self):
            self.search_calls = 0

        def __copy__(self):
            raise RuntimeError("snapshot unavailable")

        async def search_memories(self, **_kwargs):
            self.search_calls += 1
            return []

    engine = UncopyableEngine()
    service = EvaluationService(
        engine=engine,
        fixture_dir=fixture_dir,
        quality_loop_secret=_QUALITY_LOOP_SECRET,
    )

    async def unexpected_snapshot(_engine):
        pytest.fail("snapshot hash must not run without an isolated engine")

    monkeypatch.setattr(service, "_database_snapshot_hash", unexpected_snapshot)
    result = await service.run_evaluation(
        datasets=["paired"],
        k=1,
        variants=["baseline"],
        baseline="baseline",
        save_report=False,
        quality_loop={},
    )

    recall = next(
        stage
        for stage in result["quality_loop"]["stages"]
        if stage["stage"] == "recall"
    )
    assert result["status"] == "error"
    assert recall["state"] == "unavailable"
    assert recall["reason"] == "engine_missing"
    assert engine.search_calls == 0
