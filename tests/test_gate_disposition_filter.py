"""mark_write 过滤契约：默认不召回，显式包含可召回。"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.features.memory.application.memory_engine import MemoryEngine
from core.features.quality.application.gate_disposition_filter import (
    filter_mark_write,
    is_mark_write,
)
from core.features.retrieval.rrf_fusion import HybridResult


def _result(doc_id: int, disposition: str | None) -> HybridResult:
    metadata = {}
    if disposition is not None:
        metadata["gate_disposition"] = disposition
    return HybridResult(
        doc_id=doc_id,
        final_score=1.0,
        rrf_score=1.0,
        bm25_score=None,
        vector_score=None,
        content=f"memory-{doc_id}",
        metadata=metadata,
    )


def test_mark_write_filtered_by_default() -> None:
    results = [_result(1, "mark_write"), _result(2, None)]
    assert [r.doc_id for r in filter_mark_write(results)] == [2]


def test_include_mark_write_keeps_all() -> None:
    results = [_result(1, "mark_write"), _result(2, None)]
    assert len(filter_mark_write(results, include_mark_write=True)) == 2


def test_is_mark_write_reads_metadata() -> None:
    assert is_mark_write({"gate_disposition": "mark_write"}) is True
    assert is_mark_write({"gate_disposition": "quarantine"}) is False
    assert is_mark_write({}) is False


@pytest.mark.asyncio
async def test_search_memories_filters_mark_write_by_default() -> None:
    """引擎检索默认排除 mark_write，include_mark_write=True 时保留。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    engine.dual_route_retriever = MagicMock()
    engine.dual_route_retriever.search = AsyncMock(
        return_value=[
            _result(1, "mark_write"),
            _result(2, None),
            _result(3, "quarantine"),
        ]
    )
    engine._retrieval = MagicMock()
    engine._retrieval.cache_key = MagicMock(return_value="cache-key")
    engine._retrieval.get_cached = MagicMock(return_value=None)
    engine._retrieval.get_session_cached = MagicMock(return_value=None)
    engine._retrieval.apply_trigger_boost = AsyncMock(side_effect=lambda _q, r: r)
    engine._retrieval.apply_boosts = AsyncMock(side_effect=lambda r, _e: r)
    engine._retrieval.set_cached = MagicMock()
    engine._retrieval.set_session_cached = MagicMock()
    engine._maintenance = MagicMock()
    engine._maintenance.update_access_times_batch = AsyncMock(return_value=1)
    engine._maintenance.migrate_session_if_needed = AsyncMock()

    def _close_background(coro):
        """关闭测试中不需要实际调度的后台协程。"""

        if inspect.iscoroutine(coro):
            coro.close()

    engine._create_tracked_task = MagicMock(side_effect=_close_background)

    default_results = await engine.search_memories("test query", k=5)
    assert [r.doc_id for r in default_results] == [2, 3]

    included_results = await engine.search_memories(
        "test query", k=5, include_mark_write=True
    )
    assert [r.doc_id for r in included_results] == [1, 2, 3]
