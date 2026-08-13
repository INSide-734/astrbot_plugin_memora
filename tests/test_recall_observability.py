"""请求级召回计时与共享状态隔离测试。"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.recall.application.recall_observability import RecallTimingContext
from core.managers.memory_engine import MemoryEngine
from core.managers.retrieval_timing import RetrievalTimingSink


def test_recall_timing_context_exposes_only_safe_scalars() -> None:
    """请求上下文只导出 allowlist 标量，并从钩子起点计算软截止时间。"""

    context = RecallTimingContext.start(soft_budget_ms=800, started_monotonic=10.0)
    context.record("plugin_ready_ms", 12.5)
    context.record("query", "不得泄露")
    context.retrieval.update(
        {
            "retrieval_total_ms": 31.0,
            "cache_hit": False,
            "prompt": "不得泄露",
        }
    )

    assert context.deadline_monotonic == pytest.approx(10.8)
    assert context.snapshot() == {
        "plugin_ready_ms": 12.5,
        "retrieval_total_ms": 31.0,
        "cache_hit": False,
    }


@pytest.mark.asyncio
async def test_memory_engine_writes_timing_to_each_request_sink() -> None:
    """并发检索把阶段计时写入各自 sink，不读取检索器共享快照。"""

    release = asyncio.Event()
    both_started = asyncio.Event()
    started = 0

    async def search(query: str, *_args, timing_sink=None, **_kwargs):
        """在两个请求同时进入后写入可区分的请求局部计时。"""

        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await release.wait()
        timing_sink.update(
            {
                "document_total_ms": 11.0 if query == "first" else 22.0,
                "query_count": 1,
            }
        )
        return []

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    engine.dual_route_retriever = MagicMock()
    engine.dual_route_retriever.search = AsyncMock(side_effect=search)
    engine.dual_route_retriever.last_search_timing = {
        "document_total_ms": 999.0,
    }
    engine._retrieval = MagicMock()
    engine._retrieval.cache_key.side_effect = lambda query, *_args, **_kwargs: query
    engine._retrieval.get_cached.return_value = None
    engine._retrieval.get_session_cached.return_value = None
    engine._retrieval.apply_trigger_boost = AsyncMock(
        side_effect=lambda _query, rows: rows
    )
    engine._retrieval.apply_boosts = AsyncMock(side_effect=lambda rows, _emotion: rows)
    engine._maintenance = MagicMock()
    engine._maintenance.migrate_session_if_needed = AsyncMock()

    def close_background(coro) -> None:
        """关闭测试中无需运行的维护协程。"""

        if inspect.iscoroutine(coro):
            coro.close()

    engine._create_tracked_task = MagicMock(side_effect=close_background)
    first_sink = RetrievalTimingSink()
    second_sink = RetrievalTimingSink()

    first_task = asyncio.create_task(
        engine.search_memories("first", timing_sink=first_sink)
    )
    second_task = asyncio.create_task(
        engine.search_memories("second", timing_sink=second_sink)
    )
    await both_started.wait()
    release.set()
    await asyncio.gather(first_task, second_task)

    assert first_sink.snapshot()["document_total_ms"] == 11.0
    assert second_sink.snapshot()["document_total_ms"] == 22.0
    assert first_sink.snapshot()["document_total_ms"] != 999.0


def test_retrieval_timing_sink_rejects_sensitive_and_non_scalar_values() -> None:
    """检索计时 sink 丢弃正文、查询及复杂对象。"""

    sink = RetrievalTimingSink()
    sink.update(
        {
            "retrieval_total_ms": 4.0,
            "query_count": 2,
            "deadline_exhausted": True,
            "graph_route_degraded": True,
            "route_aborted": False,
            "query": "secret",
            "ids": [1, 2],
        }
    )

    assert sink.snapshot() == {
        "retrieval_total_ms": 4.0,
        "query_count": 2,
        "deadline_exhausted": True,
        "graph_route_degraded": True,
        "route_aborted": False,
    }
