from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.models.recall_strategy import RecallStrategy
from core.page_api import PluginPageApi
from core.retrieval.explainable_recall import capture_explainable_recall
from core.retrieval.trace_models import RecallTrace, TraceResult, TraceStage
from core.retrieval.trace_store import RecallTraceStore


class _TraceKind(Enum):
    RECALL = "recall"


@dataclass
class _NestedMeta:
    label: str
    flags: set[str]


def _trace(
    trace_id: str,
    query: str = "coffee",
    created_at: float | None = None,
) -> RecallTrace:
    return RecallTrace(
        trace_id=trace_id,
        query=query,
        total_ms=10.5,
        stages=[TraceStage(name="bm25", duration_ms=2.0, candidate_count=3)],
        results=[
            TraceResult(
                doc_id=f"mem-{trace_id}",
                rank=1,
                initial_score=0.5,
                final_score=0.8,
            )
        ],
        filtered=[],
        created_at=created_at if created_at is not None else 1000.0,
        metadata={"nested": {"tags": ["initial"]}},
    )


def test_recall_trace_is_json_safe():
    trace = RecallTrace(
        trace_id="trace-1",
        query="coffee",
        total_ms=10.5,
        stages=[TraceStage(name="bm25", duration_ms=2.0, candidate_count=3)],
        results=[
            TraceResult(
                doc_id="mem-coffee",
                rank=1,
                initial_score=0.5,
                final_score=0.8,
            )
        ],
        filtered=[],
    )
    payload = trace.to_dict()
    assert payload["trace_id"] == "trace-1"
    assert payload["stages"][0]["name"] == "bm25"
    assert payload["results"][0]["doc_id"] == "mem-coffee"


@pytest.mark.asyncio
async def test_trace_store_saves_gets_and_lists_memory_only():
    store = RecallTraceStore(retention_count=3)
    await store.initialize()

    await store.save_trace(_trace("trace-1"))

    loaded = await store.get_trace("trace-1")
    listed = await store.list_traces()

    assert loaded is not None
    assert loaded["trace_id"] == "trace-1"
    assert listed == [loaded]


@pytest.mark.asyncio
async def test_trace_store_get_trace_returns_deep_copy_memory_only():
    store = RecallTraceStore(retention_count=3)
    await store.initialize()
    await store.save_trace(_trace("trace-1"))

    loaded = await store.get_trace("trace-1")
    assert loaded is not None
    loaded["metadata"]["nested"]["tags"].append("mutated")
    loaded["stages"][0]["metadata"]["leaked"] = True

    reloaded = await store.get_trace("trace-1")
    assert reloaded is not None
    assert reloaded["metadata"]["nested"]["tags"] == ["initial"]
    assert "leaked" not in reloaded["stages"][0]["metadata"]


@pytest.mark.asyncio
async def test_trace_store_list_traces_returns_deep_copy_memory_only():
    store = RecallTraceStore(retention_count=3)
    await store.initialize()
    await store.save_trace(_trace("trace-1"))

    listed = await store.list_traces()
    listed[0]["metadata"]["nested"]["tags"].append("mutated")
    listed[0]["results"][0]["metadata"]["leaked"] = True

    relisted = await store.list_traces()
    assert relisted[0]["metadata"]["nested"]["tags"] == ["initial"]
    assert "leaked" not in relisted[0]["results"][0]["metadata"]


@pytest.mark.asyncio
async def test_trace_store_retention_evicts_oldest_memory_trace():
    store = RecallTraceStore(retention_count=2)
    await store.initialize()

    await store.save_trace(_trace("trace-1"))
    await store.save_trace(_trace("trace-2"))
    await store.save_trace(_trace("trace-3"))

    assert await store.get_trace("trace-1") is None
    assert [item["trace_id"] for item in await store.list_traces()] == [
        "trace-3",
        "trace-2",
    ]


@pytest.mark.asyncio
async def test_trace_store_persists_and_reloads_sqlite_payload(tmp_path):
    db_path = tmp_path / "trace.db"
    first_store = RecallTraceStore(db_path=db_path, retention_count=3)
    await first_store.initialize()
    await first_store.save_trace(_trace("trace-1"))

    second_store = RecallTraceStore(db_path=db_path, retention_count=3)
    await second_store.initialize()

    loaded = await second_store.get_trace("trace-1")
    listed = await second_store.list_traces()

    assert loaded is not None
    assert loaded["trace_id"] == "trace-1"
    assert loaded["results"][0]["doc_id"] == "mem-trace-1"
    assert [item["trace_id"] for item in listed] == ["trace-1"]


@pytest.mark.asyncio
async def test_trace_store_sqlite_retention_removes_oldest_rows(tmp_path):
    db_path = tmp_path / "trace.db"
    store = RecallTraceStore(db_path=db_path, retention_count=2)
    await store.initialize()

    await store.save_trace(_trace("trace-1"))
    await store.save_trace(_trace("trace-2"))
    await store.save_trace(_trace("trace-3"))

    assert await store.get_trace("trace-1") is None
    assert [item["trace_id"] for item in await store.list_traces()] == [
        "trace-3",
        "trace-2",
    ]

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT trace_id FROM recall_traces")
        rows = await cursor.fetchall()

    assert {row[0] for row in rows} == {"trace-2", "trace-3"}


@pytest.mark.asyncio
async def test_trace_store_sqlite_cache_matches_created_at_retention(tmp_path):
    db_path = tmp_path / "trace.db"
    store = RecallTraceStore(db_path=db_path, retention_count=2)
    await store.initialize()

    await store.save_trace(_trace("newest", created_at=300.0))
    await store.save_trace(_trace("oldest", created_at=100.0))
    await store.save_trace(_trace("middle", created_at=200.0))

    assert await store.get_trace("oldest") is None
    assert [item["trace_id"] for item in await store.list_traces()] == [
        "newest",
        "middle",
    ]


@pytest.mark.asyncio
async def test_trace_store_initialize_commits_sqlite_retention_trim(tmp_path):
    db_path = tmp_path / "trace.db"
    first_store = RecallTraceStore(db_path=db_path, retention_count=5)
    await first_store.initialize()

    await first_store.save_trace(_trace("oldest", created_at=100.0))
    await first_store.save_trace(_trace("middle", created_at=200.0))
    await first_store.save_trace(_trace("newest", created_at=300.0))

    second_store = RecallTraceStore(db_path=db_path, retention_count=2)
    await second_store.initialize()

    assert [item["trace_id"] for item in await second_store.list_traces()] == [
        "newest",
        "middle",
    ]

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM recall_traces")
        row = await cursor.fetchone()

    assert row[0] == 2


@pytest.mark.asyncio
async def test_trace_store_sqlite_duplicate_replacement_and_tie_retention(tmp_path):
    db_path = tmp_path / "trace.db"
    store = RecallTraceStore(db_path=db_path, retention_count=2)
    await store.initialize()

    await store.save_trace(_trace("trace-a", query="old", created_at=100.0))
    await store.save_trace(_trace("trace-b", created_at=100.0))
    await store.save_trace(_trace("trace-a", query="new", created_at=100.0))
    replaced = await store.get_trace("trace-a")
    assert replaced is not None
    assert replaced["query"] == "new"

    await store.save_trace(_trace("trace-c", created_at=100.0))

    assert await store.get_trace("trace-a") is None
    assert [item["trace_id"] for item in await store.list_traces()] == [
        "trace-c",
        "trace-b",
    ]


@pytest.mark.asyncio
async def test_trace_store_mapping_normalization_is_recursive_json_safe(tmp_path):
    store = RecallTraceStore(db_path=tmp_path / "trace.db", retention_count=3)
    await store.initialize()

    await store.save_trace(
        {
            "trace_id": "trace-structured",
            "query": "coffee",
            "total_ms": 1.0,
            "created_at": 123.0,
            "metadata": {
                "tags": {"z", "a"},
                "kind": _TraceKind.RECALL,
                "path": Path("notes") / "today.md",
                "nested": _NestedMeta(label="sample", flags={"fresh", "hot"}),
            },
        }
    )

    loaded = await store.get_trace("trace-structured")
    assert loaded is not None
    assert loaded["metadata"] == {
        "tags": ["a", "z"],
        "kind": "recall",
        "path": str(Path("notes") / "today.md"),
        "nested": {"label": "sample", "flags": ["fresh", "hot"]},
    }


@dataclass
class _FakeRecallResult:
    doc_id: int
    content: str
    final_score: float
    metadata: dict
    score_breakdown: dict | None = None
    initial_score: float | None = None


class _FakeConfigManager:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self._values = values or {}
        self.runtime_injection_fallback = True

    def get(self, key: str, default=None):
        return self._values.get(key, default)


class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.debug_trace = [
            {
                "doc_id": 101,
                "initial_score": 0.4,
                "final_score": 0.82,
                "source": "optimizer",
                "metadata": {"reason": "emotion match"},
                "stages": [
                    {
                        "name": "emotion_boost",
                        "before": 0.4,
                        "after": 0.82,
                        "delta": 0.42,
                    }
                ],
            }
        ]

    async def search_memories(self, **kwargs):
        self.calls.append(kwargs)
        return [
            _FakeRecallResult(
                doc_id=101,
                content="用户喜欢喝拿铁",
                initial_score=0.4,
                final_score=0.82,
                score_breakdown={"doc_kw": 0.3, "doc_vec": 0.5},
                metadata={
                    "memory_type": "preference",
                    "session_id": "private:user-1",
                    "canonical_summary": "用户喜欢拿铁",
                },
            )
        ]


class _FakeOptimizerPath:
    def __init__(self) -> None:
        self.received_debug_trace = None

    async def apply_boosts(self, results, _emotion_context, debug_trace=None):
        self.received_debug_trace = debug_trace
        if debug_trace is not None:
            debug_trace.append(
                {
                    "doc_id": 202,
                    "initial_score": 0.5,
                    "final_score": 0.75,
                    "source": "optimizer",
                    "stages": [
                        {"name": "emotion_boost", "before": 0.5, "after": 0.75}
                    ],
                }
            )
        return results


class _FakeEngineWithOptimizerPath:
    def __init__(self) -> None:
        self.optimizer = _FakeOptimizerPath()
        self.calls: list[dict] = []
        self._last_debug_trace: list[dict] = []

    async def search_memories(self, **kwargs):
        self.calls.append(kwargs)
        debug_trace = kwargs.get("debug_trace")
        if debug_trace is None and kwargs.get("trace_debug"):
            debug_trace = []
        results = [
            _FakeRecallResult(
                doc_id=202,
                content="生产路径结果",
                initial_score=0.5,
                final_score=0.75,
                metadata={"memory_type": "fact"},
            )
        ]
        boosted = await self.optimizer.apply_boosts(
            results,
            None,
            debug_trace=debug_trace,
        )
        self._last_debug_trace = list(debug_trace or [])
        return boosted


@pytest.fixture
def page_api_with_fake_engine():
    engine = _FakeEngine()
    plugin = MagicMock()
    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, None))
    plugin.config_manager = _FakeConfigManager(
        {
            "recall_engine.injection_routing_mode": "hybrid",
            "recall_engine.injection_manual_preset": "quality",
            "recall_engine.injection_auto_fallback_preset": "low_cost",
            "recall_engine.injection_hybrid_base_preset": "balanced",
            "recall_engine.injection_hybrid_min_preset": "low_cost",
            "recall_engine.injection_hybrid_max_preset": "quality",
            "recall_engine.injection_delivery_override": "auto",
            "recall_engine.injection_preset_overrides_enabled": True,
            "recall_engine.injection_budget_chars": 777,
            "recall_engine.injection_memory_max_chars": 111,
            "recall_engine.injection_metadata_max_chars": 55,
            "recall_engine.injection_include_key_facts": True,
            "recall_engine.injection_include_topics": False,
            "recall_engine.injection_include_participants": False,
            "recall_engine.injection_compact_header": True,
        }
    )
    plugin.initializer = SimpleNamespace(
        memory_engine=engine,
        conversation_manager=None,
        index_validator=None,
        data_dir=None,
    )
    api = PluginPageApi(plugin)
    api._recall_trace_store = RecallTraceStore()
    return api


@pytest.mark.asyncio
async def test_recall_trace_endpoint_returns_trace(page_api_with_fake_engine):
    response = await page_api_with_fake_engine.test_recall_with_trace_payload(
        {
            "query": "用户喜欢喝什么咖啡",
            "k": 5,
            "chat_type": "private",
            "session_id": "private:user-1",
        }
    )

    assert response["status"] == "ok"
    data = response["data"]
    assert data["trace_id"]
    assert data["query"] == "用户喜欢喝什么咖啡"
    assert isinstance(data["stages"], list)
    assert isinstance(data["results"], list)


@pytest.mark.asyncio
async def test_trace_contains_non_executing_injection_decision(
    page_api_with_fake_engine,
    monkeypatch,
):
    from core.injection.executor import InjectionExecutor
    from core.injection.recorder import InjectionDecisionRecorder

    def unexpected_call(*_args, **_kwargs):
        raise AssertionError("preview must not execute or record an injection")

    monkeypatch.setattr(InjectionExecutor, "execute", unexpected_call)
    monkeypatch.setattr(InjectionDecisionRecorder, "record", unexpected_call)
    engine = page_api_with_fake_engine.plugin.initializer.memory_engine

    response = await page_api_with_fake_engine.test_recall_with_trace_payload(
        {"query": "我之前喜欢什么咖啡", "k": 5}
    )

    assert response["status"] == "ok"
    stages = [
        stage
        for stage in response["data"]["stages"]
        if stage["name"] == "injection_decision"
    ]
    assert len(stages) == 1
    stage = stages[0]
    metadata = stage["metadata"]
    assert stage["candidate_count"] == 1
    assert stage["duration_ms"] >= 0
    assert metadata == {
        "routing_mode": "hybrid",
        "configured_preset": "balanced",
        "recommended_preset": "balanced",
        "resolved_preset": "balanced",
        "effective_budget_chars": 777,
        "reason_codes": ["AUTO_FALLBACK", "INVALID_CONFIG_FALLBACK"],
    }
    assert not ({"memory_content", "query", "doc_id", "trace_id"} & metadata.keys())
    assert len(engine.calls) == 1
    assert response["data"]["results"][0]["doc_id"] == "101"
    assert response["data"]["results"][0]["final_score"] == 0.82


@pytest.mark.asyncio
async def test_recall_trace_missing_engine_returns_error():
    plugin = MagicMock()
    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, None))
    plugin.initializer = SimpleNamespace(
        memory_engine=None,
        conversation_manager=None,
        index_validator=None,
        data_dir=None,
    )
    api = PluginPageApi(plugin)

    response = await api.test_recall_with_trace_payload({"query": "coffee"})

    assert response["status"] == "error"
    assert "MemoryEngine unavailable" in response["message"]


@pytest.mark.asyncio
async def test_recall_trace_detail_returns_saved_trace_after_trace_run(
    page_api_with_fake_engine,
):
    trace_response = await page_api_with_fake_engine.test_recall_with_trace_payload(
        {"query": "用户喜欢喝什么咖啡", "k": 5}
    )
    trace_id = trace_response["data"]["trace_id"]

    detail_response = await page_api_with_fake_engine.get_recall_trace_detail_payload(
        {"trace_id": trace_id}
    )

    assert detail_response["status"] == "ok"
    assert detail_response["data"]["trace_id"] == trace_id
    assert detail_response["data"]["query"] == "用户喜欢喝什么咖啡"


@pytest.mark.asyncio
async def test_recall_trace_detail_missing_trace_id_returns_error(
    page_api_with_fake_engine,
):
    response = await page_api_with_fake_engine.get_recall_trace_detail_payload({})

    assert response["status"] == "error"
    assert "trace_id" in response["message"]


@pytest.mark.asyncio
async def test_recall_trace_invalid_k_uses_default(page_api_with_fake_engine):
    response = await page_api_with_fake_engine.test_recall_with_trace_payload(
        {"query": "coffee", "k": "bad"}
    )

    assert response["status"] == "ok"
    assert page_api_with_fake_engine.plugin.initializer.memory_engine.calls[-1]["k"] == 5
    assert response["data"]["metadata"]["request"]["k"] == 5


@pytest.mark.asyncio
async def test_recall_trace_includes_debug_contribution_fields(
    page_api_with_fake_engine,
):
    response = await page_api_with_fake_engine.test_recall_with_trace_payload(
        {"query": "coffee", "k": 5}
    )

    assert response["status"] == "ok"
    result = response["data"]["results"][0]
    contribution = result["score_contributions"][0]
    assert contribution["source"] == "optimizer"
    assert contribution["metadata"]["doc_id"] == 101
    assert contribution["metadata"]["initial_score"] == 0.4
    assert contribution["metadata"]["final_score"] == 0.82
    assert contribution["metadata"]["stages"][0]["name"] == "emotion_boost"


@pytest.mark.asyncio
async def test_capture_explainable_recall_requests_optimizer_debug_trace():
    engine = _FakeEngineWithOptimizerPath()

    trace = await capture_explainable_recall(
        engine,
        {"query": "coffee", "k": 3, "emotion_context": ["joy"]},
    )

    assert engine.calls[-1]["trace_debug"] is True
    assert engine.optimizer.received_debug_trace is not None
    assert trace["metadata"]["debug_trace_available"] is True
    assert trace["results"][0]["score_contributions"][0]["metadata"]["doc_id"] == 202


@pytest.mark.asyncio
async def test_recall_trace_sanitizes_content_and_sensitive_metadata():
    long_content = "敏感内容" * 80
    secret_value = "secret-token-123"

    class SensitiveEngine:
        async def search_memories(self, **_kwargs):
            return [
                _FakeRecallResult(
                    doc_id=303,
                    content=long_content,
                    final_score=0.9,
                    metadata={
                        "memory_type": "preference",
                        "importance": 0.8,
                        "status": "active",
                        "create_time": 123.0,
                        "canonical_summary": "摘要" * 200,
                        "source_type": "chat",
                        "session_id": "private:user-1",
                        "user_id": "user-1",
                        "raw": {"payload": secret_value},
                        "source": {"message": secret_value},
                        "private": secret_value,
                    },
                )
            ]

    trace = await capture_explainable_recall(
        SensitiveEngine(),
        {
            "query": "coffee",
            "session_id": "private:user-1",
            "user_id": "user-1",
        },
    )
    result_metadata = trace["results"][0]["metadata"]
    request_metadata = trace["metadata"]["request"]
    payload_json = json.dumps(trace, ensure_ascii=False)

    assert "content" not in result_metadata
    assert result_metadata["content_preview"] == long_content[:160]
    assert len(result_metadata["content_preview"]) <= 160
    assert result_metadata["memory_type"] == "preference"
    assert result_metadata["importance"] == 0.8
    assert result_metadata["status"] == "active"
    assert result_metadata["create_time"] == 123.0
    assert result_metadata["source_type"] == "chat"
    assert "session_id" not in result_metadata
    assert "user_id" not in result_metadata
    assert "raw" not in result_metadata
    assert "source" not in result_metadata
    assert "private" not in result_metadata
    assert "session_id" not in request_metadata
    assert "user_id" not in request_metadata
    assert secret_value not in payload_json


def test_build_trace_request_params_converts_recall_strategy_enum():
    api = PluginPageApi(MagicMock())

    params = api._build_trace_request_params(
        {"recall_strategy": "relationship_review"},
        "coffee",
    )
    invalid = api._build_trace_request_params(
        {"recall_strategy": "not-a-strategy"},
        "coffee",
    )

    assert params["recall_strategy"] is RecallStrategy.RELATIONSHIP_REVIEW
    assert invalid["recall_strategy"] is None


@pytest.mark.asyncio
async def test_trace_store_uses_memory_when_initializer_data_dir_missing(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    engine = _FakeEngine()
    plugin = MagicMock()
    plugin._ensure_plugin_ready = AsyncMock(return_value=(True, None))
    plugin.initializer = SimpleNamespace(
        memory_engine=engine,
        conversation_manager=None,
        index_validator=None,
        data_dir=None,
    )
    api = PluginPageApi(plugin)

    response = await api.test_recall_with_trace_payload({"query": "coffee"})

    assert response["status"] == "ok"
    assert api._recall_trace_store.db_path is None
    assert not (tmp_path / "data" / "recall_traces.db").exists()
