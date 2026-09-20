"""mark_write 过滤契约：默认不召回，显式包含可召回。"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
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


@pytest.mark.asyncio
async def test_search_memories_backfills_non_mark_write_over_k() -> None:
    """mark_write 不占名额：截断前过滤，返回 k 条全部非 mark_write。"""

    engine = MemoryEngine(db_path=":memory:", faiss_db=MagicMock())
    engine.dual_route_retriever = None
    engine.hybrid_retriever = MagicMock()
    engine.hybrid_retriever.search = AsyncMock(
        return_value=[
            _result(1, "mark_write"),
            _result(2, None),
            _result(3, None),
            _result(4, "mark_write"),
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

    results = await engine.search_memories("test query", k=2)

    assert [r.doc_id for r in results] == [2, 3]


class _CanonicalRows:
    """缓存命中重校验的最小 canonical 替身：按 ID 返回当前正文与状态。"""

    def __init__(self, rows: dict[int, tuple[str, dict]]) -> None:
        self._rows = rows

    async def get_documents(
        self,
        metadata_filters: dict | None = None,
        ids: list[int] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict]:
        """复刻宿主文档存储的按 ID 批量读取形状。"""

        del metadata_filters, offset
        docs = [
            {
                "id": int(doc_id),
                "text": self._rows[doc_id][0],
                "metadata": self._rows[doc_id][1],
            }
            for doc_id in ids or []
            if int(doc_id) in self._rows
        ]
        return docs[: int(limit)] if limit is not None else docs


@pytest.mark.asyncio
async def test_search_memories_cache_hit_skips_mark_write_access_times() -> None:
    """缓存命中路径不得为被隐藏的 mark_write 记忆更新访问时间。"""

    cached = [_result(1, "mark_write"), _result(2, None)]
    engine = MemoryEngine(
        db_path=":memory:",
        faiss_db=SimpleNamespace(
            document_storage=_CanonicalRows(
                {
                    1: ("memory-1", {"gate_disposition": "mark_write"}),
                    2: ("memory-2", {}),
                }
            )
        ),
    )
    engine._retrieval = MagicMock()
    engine._retrieval.cache_key = MagicMock(return_value="cache-key")
    engine._retrieval.get_cached = MagicMock(return_value=cached)
    engine._retrieval.get_session_cached = MagicMock(return_value=None)
    engine._maintenance = MagicMock()
    engine._maintenance.update_access_times_batch = AsyncMock(return_value=1)

    def _close_background(coro):
        """关闭测试中不需要实际调度的后台协程。"""

        if inspect.iscoroutine(coro):
            coro.close()

    engine._create_tracked_task = MagicMock(side_effect=_close_background)

    results = await engine.search_memories("cached query")

    assert [r.doc_id for r in results] == [2]
    engine._maintenance.update_access_times_batch.assert_called_once()
    ids = engine._maintenance.update_access_times_batch.call_args.args[0]
    assert ids == [2]
