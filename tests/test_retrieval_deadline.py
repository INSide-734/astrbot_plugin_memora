"""向量软截止时间、部分结果与取消收束测试。"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.retrieval.graph_retriever import GraphRetriever
from core.retrieval.hybrid_retriever import HybridRetriever
from core.retrieval.rrf_fusion import RRFFusion
from core.retrieval.vector_deadline import run_local_and_bounded_vector


@pytest.mark.asyncio
async def test_expired_deadline_keeps_local_result_without_starting_vector() -> None:
    """截止时间已耗尽时仍执行本地路，并且不创建向量调用。"""

    local = AsyncMock(return_value=["local"])
    vector = AsyncMock(return_value=["vector"])

    local_result, vector_result, timed_out = await run_local_and_bounded_vector(
        local,
        vector,
        deadline_monotonic=time.perf_counter() - 1.0,
    )

    assert local_result == ["local"]
    assert vector_result is None
    assert timed_out is True
    local.assert_awaited_once()
    vector.assert_not_called()


@pytest.mark.asyncio
async def test_calling_task_cancellation_collects_both_routes() -> None:
    """调用方取消时取消并收束本地与向量子任务，然后传播取消异常。"""

    started = asyncio.Event()
    local_cancelled = asyncio.Event()
    vector_cancelled = asyncio.Event()
    entered = 0

    async def wait_forever(cancelled: asyncio.Event) -> list[str]:
        """进入后等待取消，并用事件证明 finally 已执行。"""

        nonlocal entered
        entered += 1
        if entered == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(
        run_local_and_bounded_vector(
            lambda: wait_forever(local_cancelled),
            lambda: wait_forever(vector_cancelled),
            deadline_monotonic=None,
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert local_cancelled.is_set()
    assert vector_cancelled.is_set()


@pytest.mark.asyncio
async def test_document_route_falls_back_to_bm25_after_vector_deadline() -> None:
    """文档向量超过绝对截止时间时保留 BM25，并记录部分降级状态。"""

    bm25 = MagicMock()
    bm25.search = AsyncMock(return_value=[SimpleNamespace(doc_id=1)])
    vector = MagicMock()
    vector.search = AsyncMock(return_value=[])
    retriever = HybridRetriever(bm25, vector, MagicMock(), {"fallback_enabled": True})
    retriever._fallback_bm25_only = MagicMock(return_value=["bm25-fallback"])
    timing: dict[str, object] = {}

    result = await retriever.search(
        "query",
        deadline_monotonic=time.perf_counter() - 1.0,
        timing_sink=timing,
    )

    assert result == ["bm25-fallback"]
    vector.search.assert_not_called()
    assert timing["document_vector_timed_out"] is True
    assert timing["deadline_exhausted"] is True
    assert timing["partial_fallback"] is True


@pytest.mark.asyncio
async def test_graph_route_falls_back_to_keyword_after_vector_deadline() -> None:
    """图向量超过绝对截止时间时保留关键词结果，并记录部分降级状态。"""

    keyword = MagicMock()
    keyword.search = AsyncMock(
        return_value=[
            SimpleNamespace(
                doc_id=7,
                score=0.9,
                graph_distance=0,
                content="keyword result",
                metadata={"importance": 0.5},
            )
        ]
    )
    vector = MagicMock()
    vector.search = AsyncMock(return_value=[])
    retriever = GraphRetriever(keyword, vector, RRFFusion())
    timing: dict[str, object] = {}

    result = await retriever.search(
        "query",
        deadline_monotonic=time.perf_counter() - 1.0,
        timing_sink=timing,
    )

    assert [item.doc_id for item in result] == [7]
    vector.search.assert_not_called()
    assert timing["graph_vector_timed_out"] is True
    assert timing["deadline_exhausted"] is True
    assert timing["partial_fallback"] is True
